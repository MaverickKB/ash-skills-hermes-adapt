from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class ScriptTestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def put(self, name: str, text: str) -> Path:
        path = self.scratch / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_script(self, script: str, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(REPO / "skills" / script), *map(str, arguments)],
            cwd=self.scratch,
            capture_output=True,
            text=True,
            timeout=15,
        )


@unittest.skipUnless(os.name == "posix", "requires symbolic link support")
class SkillCheckTests(ScriptTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.skills = self.scratch / "skills"
        self.skill = self.skills / "example"
        self.skill.mkdir(parents=True)
        self.put("skills/example/SKILL.md", "---\nname: example\ndescription: Use for testing.\n---\n# Example\n")
        self.dist = self.scratch / "dist"

    def build(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run_script("skill-check/scripts/build.py", "--skills-dir", self.skills, "--dist", self.dist, *arguments)

    def assert_rejected(self) -> None:
        for result in (self.build(), self.build("--check")):
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("symlink", result.stderr)
        self.assertFalse(self.dist.exists())
        result = self.run_script("skill-check/scripts/validate.py", self.skills, "--run-scripts")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("symlink", result.stdout)

    def test_rejects_file_symlink_escaping_skill(self) -> None:
        outside = self.put("outside.txt", "Synthetic content outside the skill.\n")
        (self.skill / "attachment.txt").symlink_to(outside)
        self.assert_rejected()

    def test_rejects_directory_symlink_escaping_skill(self) -> None:
        outside = self.scratch / "outside"
        outside.mkdir()
        (self.skill / "references").symlink_to(outside, target_is_directory=True)
        self.assert_rejected()

    def test_rejects_symlinked_skill_folder(self) -> None:
        outside = self.scratch / "outside"
        self.skill.rename(outside)
        self.skill.symlink_to(outside, target_is_directory=True)
        self.assert_rejected()

    def test_internal_file_link_can_be_packaged(self) -> None:
        source = self.put("skills/example/source.txt", "Shipped content.\n")
        (self.skill / "alias.txt").symlink_to(source.name)
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.dist / "example.skill") as archive:
            self.assertEqual(archive.read("example/alias.txt"), source.read_bytes())
        result = self.build("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class MigrateSafeTests(ScriptTestCase):
    def migrate(self, forward: str, *, extra_seed: str = "", rollback: str = "DELETE FROM child WHERE id = 10;") -> subprocess.CompletedProcess[str]:
        seed = self.put(
            "seed.sql",
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));"
            "INSERT INTO parent VALUES (1);" + extra_seed,
        )
        up = self.put("up.sql", forward)
        down = self.put("down.sql", rollback)
        return self.run_script("migrate-safe/scripts/migration_check.py", "sqlite-test", "--seed", seed, "--up", up, "--down", down)

    def test_orphaned_row_fails_round_trip(self) -> None:
        result = self.migrate("INSERT OR IGNORE INTO child VALUES (10, 73);")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FOREIGN KEY constraint failed", result.stdout)
        self.assertIn("MIGRATION: UNSAFE", result.stdout)

    def test_detects_orphan_when_script_disables_foreign_keys(self) -> None:
        result = self.migrate("PRAGMA foreign_keys=OFF; INSERT OR IGNORE INTO child VALUES (10, 73);")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("foreign key violation", result.stdout)

    def test_reports_invalid_seed_cleanly(self) -> None:
        result = self.migrate("SELECT 1;", extra_seed="INSERT INTO child VALUES (10, 73);")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL  seed", result.stdout)
        self.assertIn("FOREIGN KEY constraint failed", result.stdout)
        self.assertIn("MIGRATION: UNSAFE", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_valid_foreign_keys_preserve_safe_verdict(self) -> None:
        result = self.migrate("INSERT OR IGNORE INTO child VALUES (10, 1);")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MIGRATION: SAFE", result.stdout)


    def test_rejects_attachment_to_external_database(self) -> None:
        outside = self.scratch / "separate.sqlite"
        with sqlite3.connect(outside) as conn:
            conn.execute("CREATE TABLE observed(id INTEGER PRIMARY KEY)")
        target = str(outside).replace("'", "''")
        result = self.migrate(
            f"ATTACH DATABASE '{target}' AS separate;"
            "INSERT OR IGNORE INTO separate.observed VALUES (1);"
            "DETACH DATABASE separate;"
        )
        with sqlite3.connect(outside) as conn:
            self.assertEqual(conn.execute("SELECT id FROM observed").fetchall(), [])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("authoriz", result.stdout.lower())

    def test_rejects_vacuum_into_external_file(self) -> None:
        original = str(self.scratch).replace("'", "''")
        result = self.migrate(f"VACUUM INTO ('{original}/' || 'export.sqlite');")
        self.assertFalse((self.scratch / "export.sqlite").exists())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("authoriz", result.stdout.lower())

    def test_allows_plain_vacuum(self) -> None:
        result = self.migrate("VACUUM;")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PrefillAndKVTests(ScriptTestCase):
    def budget(self, context: int) -> tuple[subprocess.CompletedProcess[str], dict]:
        report = self.scratch / "budget.json"
        result = self.run_script(
            "tensorfold/prefill-and-kv/scripts/prefill_plan.py",
            "kv-budget", "--layers", 32, "--kv-heads", 8, "--head-dim", 128,
            "--vram-gib", 8, "--weights-gib", 5, "--overhead-gib", 2,
            "--context", context, "--json", report,
        )
        return result, json.loads(report.read_text(encoding="utf-8"))

    def test_required_spill_returns_failure_with_report(self) -> None:
        result, report = self.budget(8193)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("KV BUDGET: SPILL NEEDED", result.stdout)
        self.assertIs(report["fits_in_vram"], False)
        self.assertEqual(report["spill_pages"], 1)

    def test_exact_fit_still_passes(self) -> None:
        result, report = self.budget(8192)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KV BUDGET: FITS", result.stdout)
        self.assertIs(report["fits_in_vram"], True)
        self.assertEqual(report["spill_pages"], 0)


class ThinkLikeAshTests(ScriptTestCase):
    script = "think-like-ash/scripts/think_like_ash.py"

    def setUp(self) -> None:
        super().setUp()
        self.web = self.scratch / "faults.json"
        result = self.run_script(self.script, "init", "--symptom", "Synthetic fault", "--out", self.web)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_script(
            self.script, "add", "--web", self.web, "--layer", "dns", "--id", "D1",
            "--cause", "Stale synthetic setting", "--test", "Compare setting",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def record(self, option: str, cost: str) -> None:
        result = self.run_script(
            self.script, "resolve", "--web", self.web, "--cause", "D1", "--option", option,
            "--cost", cost, "--risk", "low", "--reversibility", "easy",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def read_web(self) -> dict:
        return json.loads(self.web.read_text(encoding="utf-8"))

    def write_legacy_duplicate(self, copies: int) -> None:
        web = self.read_web()
        entry = {**web["resolutions"][0], "cost": "low"}
        web["resolutions"][:0] = [entry] * copies
        self.web.write_text(json.dumps(web), encoding="utf-8")

    def pick(self) -> subprocess.CompletedProcess[str]:
        return self.run_script(self.script, "pick", "--web", self.web, "--cause", "D1")

    def test_pick_rejects_four_copies_of_one_option(self) -> None:
        self.record("direct fix", "high")
        self.write_legacy_duplicate(3)
        result = self.pick()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 of 4", result.stdout)

    def test_resolve_replaces_the_existing_score(self) -> None:
        self.record("direct fix", "low")
        self.record("direct fix", "high")
        records = self.read_web()["resolutions"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["cost"], "high")

    def test_pick_uses_latest_legacy_score_and_a_distinct_runner_up(self) -> None:
        self.record("direct fix", "high")
        self.record("workaround", "low")
        self.record("prevention", "med")
        self.record("monitor only", "high")
        self.write_legacy_duplicate(1)
        result = self.pick()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pick: workaround", result.stdout)
        self.assertIn("what would change it: prevention", result.stdout)


class ServingConcurrencyTests(ScriptTestCase):
    def audit(self, *lane_caps: int) -> subprocess.CompletedProcess[str]:
        reference = {name: {"token_sha256": name * 64} for name in ("a", "b")}
        bursts = [
            {
                "level": 2, "rep": rep, "lane_cap": cap,
                "streams": [
                    {"name": name, **row, "output_tokens": 11 + 10 * rep,
                     "submit_s": 0, "first_token_s": 1, "last_token_s": 11}
                    for name, row in reference.items()
                ],
            }
            for rep, cap in enumerate(lane_caps)
        ]
        receipt = self.put("bursts.json", json.dumps({"reference": reference, "bursts": bursts}))
        return self.run_script("tensorfold/serving-concurrency/scripts/concurrency_audit.py", "audit", receipt, "--json", self.scratch / "audit.json")

    def test_rejects_mixed_lane_caps_at_one_concurrency(self) -> None:
        result = self.audit(1, 2)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("lane caps", result.stderr)
        self.assertFalse((self.scratch / "audit.json").exists())

    def test_repeated_bursts_at_one_lane_cap_remain_comparable(self) -> None:
        result = self.audit(2, 2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.scratch / "audit.json").read_text(encoding="utf-8"))
        summary = report["levels"][0]
        self.assertEqual((summary["lane_cap"], summary["reps"], summary["agg_decode_tps"]), (2, 2, 3.0))


class KernelContractTests(ScriptTestCase):
    def compare(self, reference: int, candidate: object) -> subprocess.CompletedProcess[str]:
        ref = self.put("ref.json", json.dumps({"dtype": "i32", "values": [reference]}))
        cand = self.put("cand.json", json.dumps({"dtype": "i32", "values": [candidate]}))
        return self.run_script("tensorfold/kernel-contract/scripts/kernel_gate.py", "compare", "--ref", ref, "--cand", cand, "--bit-exact")

    def test_rejects_invalid_i32_json_values(self) -> None:
        for value in (7.9, True, "7", 2**31, -(2**31) - 1):
            with self.subTest(value=value):
                result = self.compare(7, value)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("signed 32-bit integer", result.stderr)

    def test_accepts_valid_i32_edges_and_checks_exactness(self) -> None:
        for value in (-(2**31), 7, 2**31 - 1):
            with self.subTest(value=value):
                result = self.compare(value, value)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("control: ok", result.stdout)
        result = self.compare(7, 7.0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.compare(7, 8)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("integer mismatch", result.stdout)


def _pack_gguf_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _write_synthetic_gguf(
    path: Path,
    kv_items: list[tuple[str, int, bytes]],
    tensors: list[tuple[str, list[int], int, bytes]],
    alignment: int = 32,
) -> Path:
    buf = bytearray()
    buf.extend(b"GGUF")
    buf.extend(struct.pack("<I", 3))
    buf.extend(struct.pack("<Q", len(tensors)))
    buf.extend(struct.pack("<Q", len(kv_items)))
    for key, vtype, payload in kv_items:
        buf.extend(_pack_gguf_string(key))
        buf.extend(struct.pack("<I", vtype))
        buf.extend(payload)

    offsets = []
    cur = 0
    for _name, _dims, _dtype, data in tensors:
        offsets.append(cur)
        cur += len(data)
        pad = (alignment - (cur % alignment)) % alignment
        cur += pad

    for (name, dims, dtype_id, _data), off in zip(tensors, offsets):
        buf.extend(_pack_gguf_string(name))
        buf.extend(struct.pack("<I", len(dims)))
        for d in dims:
            buf.extend(struct.pack("<Q", d))
        buf.extend(struct.pack("<I", dtype_id))
        buf.extend(struct.pack("<Q", off))

    pad_hdr = (alignment - (len(buf) % alignment)) % alignment
    buf.extend(b"\x00" * pad_hdr)
    for _name, _dims, _dtype, data in tensors:
        buf.extend(data)
        pad = (alignment - (len(data) % alignment)) % alignment
        buf.extend(b"\x00" * pad)

    path.write_bytes(bytes(buf))
    return path


class _MockOpenAIServer:
    def __init__(self, handler_fn):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                status, resp = handler_fn(self.path, body)
                encoded = json.dumps(resp).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, fmt, *args):
                pass

        class ThreadedTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self._server = ThreadedTCPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class VramAlchemistTests(ScriptTestCase):
    def test_probe_reports_hardware_and_tensorfold_only(self) -> None:
        out_json = self.scratch / "probe.json"
        res = self.run_script("tensorfold/vram-alchemist/scripts/vram_solve.py", "probe", "--json", out_json)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        doc = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertIn("tensorfold", doc)
        self.assertNotIn("engines_installed", doc)
        for other in ("llama-server", "llama-bench", "vllm", "sglang", "ollama"):
            self.assertNotIn(other, res.stdout)

    def test_solve_local_checkpoint_and_moe_ssd_experts_parallel(self) -> None:
        ckpt = self.scratch / "ckpt"
        ckpt.mkdir()
        (ckpt / "config.json").write_text(
            json.dumps({
                "model_type": "qwen3_5",
                "num_hidden_layers": 48,
                "hidden_size": 4096,
                "intermediate_size": 12288,
                "moe_intermediate_size": 1536,
                "vocab_size": 151936,
                "num_attention_heads": 32,
                "num_key_value_heads": 4,
                "head_dim": 128,
                "num_experts": 64,
                "num_experts_per_tok": 8,
                "quantization": {"bits": 4, "group_size": 64},
            }),
            encoding="utf-8",
        )
        out_json = self.scratch / "solve.json"
        res = self.run_script(
            "tensorfold/vram-alchemist/scripts/vram_solve.py",
            "solve",
            "--config", ckpt / "config.json",
            "--gpus", "32,32",
            "--ram-gib", "128",
            "--backend", "cuda",
            "--context", "32768",
            "--kv-cache", "fp8",
            "--json", out_json,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        doc = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertIsNotNone(doc["recommended"])
        self.assertIn("tensorfold serve ", doc["commands"]["serve"])
        self.assertIn("--parallel 2", doc["commands"]["serve"])
        self.assertIn("--kv-cache fp8", doc["commands"]["serve"])
        self.assertIn("tensorfold benchmark ", doc["commands"]["benchmark"])
        ssd_rows = [c for c in doc["candidates"] if c["status"] == "FITS_SSD_EXPERTS"]
        self.assertTrue(ssd_rows, "expected larger MoE quant to route to FITS_SSD_EXPERTS")
        self.assertTrue(ssd_rows[0]["ssd_experts"])

    def test_solve_returns_1_on_impossible_budget(self) -> None:
        res = self.run_script(
            "tensorfold/vram-alchemist/scripts/vram_solve.py",
            "solve",
            "--params-b", "70",
            "--vram-gib", "4",
            "--ram-gib", "4",
            "--context", "32768",
        )
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("NO_FIT", res.stdout)


class QuantAutopsyTests(ScriptTestCase):
    def _make_mock_responder(self, mode: str, suite: dict):
        # Map exact prompt text -> expected case so mock server simulates Q8_0 (perfect),
        # Q4_K_M (fast sweet spot) and Q2_K (cooked: leaks tool XML, fails context & sycophancy).
        by_prompt = {c["messages"][-1]["content"]: c for c in suite["cases"]}

        def handle(_path, body):
            last_msg = body["messages"][-1]["content"]
            case = by_prompt[last_msg]
            cat = case["category"]
            exp = case["expected"]
            tps = {"Q8_0": 45.0, "Q4_K_M": 92.0, "Q2_K": 130.0}[mode]
            pred_n = 20
            pred_ms = (pred_n / tps) * 1000.0
            msg: dict = {"role": "assistant", "content": ""}

            if cat == "tool_call":
                if mode == "Q2_K":
                    msg["content"] = '<tool_call>{"name":"broken"}</tool_call>'
                else:
                    msg["tool_calls"] = [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c["args"])},
                        }
                        for i, c in enumerate(exp["calls"])
                    ]
            elif cat == "json_schema":
                if mode == "Q2_K":
                    msg["content"] = "```json\n" + json.dumps(exp) + "\n```"
                else:
                    msg["content"] = json.dumps(exp)
            elif cat in ("reasoning", "code_logic"):
                ans = exp["answer"] if mode != "Q2_K" else (exp["answer"] + 1)
                msg["content"] = f"FINAL_ANSWER: {ans}"
            elif cat == "context_cliff":
                if mode == "Q2_K" or (mode == "Q4_K_M" and case["context_tokens"] > 2048):
                    msg["content"] = "FINAL_ANSWER: 0 WRONG"
                else:
                    msg["content"] = f"FINAL_ANSWER: {exp['sum']} {exp['tag']}"
            elif cat == "slop_sycophancy":
                if case.get("subtype") == "sycophancy":
                    val = exp["wrong"] if mode == "Q2_K" else exp["right"]
                    msg["content"] = f"FINAL_ANSWER: {val}"
                else:
                    msg["content"] = (
                        "Great question! Let us delve into this robust seamless tapestry \u2014 a pivotal testament."
                        if mode == "Q2_K"
                        else "SIGKILL ends a process immediately in the kernel. Processes cannot intercept or ignore it."
                    )

            return 200, {
                "choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": pred_n},
                "timings": {"prompt_ms": 40.0, "predicted_n": pred_n, "predicted_ms": pred_ms},
            }

        return handle

    def test_all_quant_matrix_pareto_and_suite_mismatch_rejection(self) -> None:
        suite_path = self.scratch / "suite.json"
        gen = self.run_script(
            "tensorfold/quant-autopsy/scripts/quant_autopsy.py",
            "generate",
            "--seed", "42",
            "--contexts", "1024,2048,4096",
            "--per-category", "3",
            "--out", suite_path,
        )
        self.assertEqual(gen.returncode, 0, gen.stdout + gen.stderr)
        suite = json.loads(suite_path.read_text(encoding="utf-8"))

        runs_dir = self.scratch / "runs"
        for quant, bpw, vram in (("Q8_0", "8.5", "34.0"), ("Q4_K_M", "4.85", "20.0"), ("Q2_K", "2.96", "12.0")):
            srv = _MockOpenAIServer(self._make_mock_responder(quant, suite))
            self.addCleanup(srv.close)
            r = self.run_script(
                "tensorfold/quant-autopsy/scripts/quant_autopsy.py",
                "run",
                "--suite", suite_path,
                "--url", srv.url,
                "--model", f"mock-{quant}",
                "--quant", quant,
                "--bpw", bpw,
                "--vram-gib", vram,
                "--out", runs_dir / f"{quant}.json",
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        html_out = self.scratch / "report.html"
        json_out = self.scratch / "summary.json"
        rep = self.run_script(
            "tensorfold/quant-autopsy/scripts/quant_autopsy.py",
            "report",
            "--dir", runs_dir,
            "--baseline", "Q8_0",
            "--html", html_out,
            "--json", json_out,
        )
        self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
        summary = json.loads(json_out.read_text(encoding="utf-8"))
        by_q = {row["quant"]: row for row in summary["rows"]}
        self.assertEqual(by_q["Q8_0"]["verdict"], "BASELINE")
        self.assertEqual(by_q["Q4_K_M"]["verdict"], "SWEET_SPOT")
        self.assertEqual(by_q["Q2_K"]["verdict"], "COOKED")
        self.assertTrue(by_q["Q4_K_M"]["pareto"])
        self.assertIn("<svg", html_out.read_text(encoding="utf-8"))
        self.assertIn("Q2_K", summary["capability_cliffs"]["tool_call"])

        # Corrupt one receipt's suite_sha256 -> report must refuse with exit 2
        bad_path = runs_dir / "Q2_K.json"
        bad_doc = json.loads(bad_path.read_text(encoding="utf-8"))
        bad_doc["suite_sha256"] = "0" * 64
        bad_path.write_text(json.dumps(bad_doc), encoding="utf-8")
        bad_rep = self.run_script("tensorfold/quant-autopsy/scripts/quant_autopsy.py", "report", "--dir", runs_dir)
        self.assertEqual(bad_rep.returncode, 2, bad_rep.stdout + bad_rep.stderr)

    def test_sweep_dry_run_accepts_example_manifest(self) -> None:
        suite_path = self.scratch / "suite.json"
        self.run_script("tensorfold/quant-autopsy/scripts/quant_autopsy.py", "generate", "--contexts", "1024", "--per-category", "1", "--out", suite_path)
        res = self.run_script(
            "tensorfold/quant-autopsy/scripts/quant_autopsy.py",
            "sweep",
            "--suite", suite_path,
            "--manifest", REPO / "skills/tensorfold/quant-autopsy/assets/sweep.example.json",
            "--out-dir", self.scratch / "out",
            "--dry-run",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("DRY RUN:", res.stdout)
        self.assertIn("tensorfold serve", res.stdout)


class LocalCoderOverclockTests(ScriptTestCase):
    def test_lint_template_catches_cache_bust_and_passes_clean_gguf(self) -> None:
        bad_tpl = self.put("bad.jinja", "Today: {{ strftime_now('%Y-%m-%d') }}\n{% if messages %}hello")
        res_bad = self.run_script("local-coder-overclock/scripts/coder_audit.py", "lint-template", bad_tpl)
        self.assertEqual(res_bad.returncode, 1, res_bad.stdout + res_bad.stderr)
        for code in ("DYNAMIC_DATE_CACHE_BUST", "NO_TOOLS_SUPPORT", "UNBALANCED_JINJA_BLOCKS"):
            self.assertIn(code, res_bad.stdout)

        clean_template = (
            "{% if tools %}{{ tools }}{% endif %}"
            "{% for m in messages %}"
            "{% if m['role'] == 'system' %}{{ m['content'] }}{% endif %}"
            "{% if m['role'] == 'assistant' %}{% if m['tool_calls'] %}{{ m['tool_calls'] }}{% endif %}{% endif %}"
            "{% if m['role'] == 'tool' %}{{ m['content'] }}{% endif %}"
            "{% endfor %}"
            "{% if add_generation_prompt %}assistant{% endif %}"
        )
        gguf_path = _write_synthetic_gguf(
            self.scratch / "clean.gguf",
            kv_items=[("tokenizer.chat_template", 8, _pack_gguf_string(clean_template))],
            tensors=[],
        )
        res_ok = self.run_script("local-coder-overclock/scripts/coder_audit.py", "lint-template", gguf_path)
        self.assertEqual(res_ok.returncode, 0, res_ok.stdout + res_ok.stderr)
        self.assertIn("TEMPLATE: CLEAN", res_ok.stdout)

    def test_probe_tools_and_cache_live_pass_and_fail(self) -> None:
        def good_handler(_path, body):
            msgs = body["messages"]
            last = msgs[-1]
            if last["role"] == "tool":
                return 200, {"choices": [{"message": {"role": "assistant", "content": "DEFAULT_PORT is 9417."}, "finish_reason": "stop"}]}
            text = str(last["content"])
            if "two tool calls in parallel" in text:
                return 200, {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "src/config.py", "start_line": 1, "end_line": 25})}},
                                {"id": "c2", "type": "function", "function": {"name": "grep_search", "arguments": json.dumps({"query": "DEFAULT_PORT", "directory": "src"})}},
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }]
                }
            if "src/server.py" in text:
                return 200, {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"id": "c0", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "src/server.py", "start_line": 10, "end_line": 40})}},
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }]
                }
            # Cache probe responses
            sys_msg = str(msgs[0]["content"])
            is_warm = len(msgs) > 2
            is_bust = "SESSION_NONCE=" in sys_msg
            cached = 2800 if (is_warm and not is_bust) else 0
            p_ms = 25.0 if cached else 420.0
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": "997"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": cached}},
                "timings": {"prompt_ms": p_ms, "cache_n": cached},
            }

        srv = _MockOpenAIServer(good_handler)
        self.addCleanup(srv.close)
        r_tools = self.run_script("local-coder-overclock/scripts/coder_audit.py", "probe-tools", "--url", srv.url, "--model", "qwen3")
        self.assertEqual(r_tools.returncode, 0, r_tools.stdout + r_tools.stderr)
        self.assertIn("TOOLS: PASS", r_tools.stdout)

        r_cache = self.run_script("local-coder-overclock/scripts/coder_audit.py", "probe-cache", "--url", srv.url, "--model", "qwen3")
        self.assertEqual(r_cache.returncode, 0, r_cache.stdout + r_cache.stderr)
        self.assertIn("CACHE: HEALTHY", r_cache.stdout)

        # Broken server leaking raw XML tool call + zero cache reuse
        def bad_handler(_path, _body):
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": "<tool_call>{\"name\":\"read_file\"}</tool_call>"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": 0}},
                "timings": {"prompt_ms": 400.0, "cache_n": 0},
            }

        bad_srv = _MockOpenAIServer(bad_handler)
        self.addCleanup(bad_srv.close)
        r_bad_tools = self.run_script("local-coder-overclock/scripts/coder_audit.py", "probe-tools", "--url", bad_srv.url, "--model", "qwen3")
        self.assertEqual(r_bad_tools.returncode, 1, r_bad_tools.stdout + r_bad_tools.stderr)
        self.assertIn("TOOLS: FAIL", r_bad_tools.stdout)

        r_bad_cache = self.run_script("local-coder-overclock/scripts/coder_audit.py", "probe-cache", "--url", bad_srv.url, "--model", "qwen3")
        self.assertEqual(r_bad_cache.returncode, 1, r_bad_cache.stdout + r_bad_cache.stderr)
        self.assertIn("CACHE: BUSTED", r_bad_cache.stdout)


class ControlVectorForgeTests(ScriptTestCase):
    def test_all_builtin_presets_export_and_lint_clean(self) -> None:
        for preset in ("mythos-engineering", "slopchop", "anti-sycophancy", "concise-coder", "deep-reasoning"):
            with self.subTest(preset=preset):
                out_dir = self.scratch / preset
                exp = self.run_script("control-vector-forge/scripts/cvector_forge.py", "presets", "--export", preset, "--out-dir", out_dir)
                self.assertEqual(exp.returncode, 0, exp.stdout + exp.stderr)
                lint = self.run_script("control-vector-forge/scripts/cvector_forge.py", "lint-pairs", "--pos", out_dir / "positive.txt", "--neg", out_dir / "negative.txt")
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
                self.assertIn("PAIRS: CLEAN", lint.stdout)

    def test_lint_pairs_rejects_length_bias_and_duplicates(self) -> None:
        pairs = [{"pos": "Short.", "neg": "This is a much longer negative prompt designed to trigger length bias in PCA extraction."} for _ in range(12)]
        p = self.put("biased.json", json.dumps({"pairs": pairs}))
        res = self.run_script("control-vector-forge/scripts/cvector_forge.py", "lint-pairs", "--pairs-json", p)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("LENGTH_BIAS", res.stdout)
        self.assertIn("DUPLICATES", res.stdout)

    def test_inspect_gguf_detects_healthy_and_corrupt_control_vectors(self) -> None:
        healthy = _write_synthetic_gguf(
            self.scratch / "healthy.gguf",
            kv_items=[("general.architecture", 8, _pack_gguf_string("qwen3"))],
            tensors=[
                ("direction.10", [4], 0, struct.pack("<4f", 0.5, 0.5, 0.5, 0.5)),
                ("direction.11", [4], 0, struct.pack("<4f", 0.6, 0.5, 0.4, 0.5)),
                ("direction.12", [4], 1, struct.pack("<4e", 0.5, 0.5, 0.5, 0.5)),
            ],
        )
        r_ok = self.run_script("control-vector-forge/scripts/cvector_forge.py", "inspect-gguf", healthy)
        self.assertEqual(r_ok.returncode, 0, r_ok.stdout + r_ok.stderr)
        self.assertIn("CVECTOR: HEALTHY", r_ok.stdout)

        corrupt = _write_synthetic_gguf(
            self.scratch / "corrupt.gguf",
            kv_items=[("general.architecture", 8, _pack_gguf_string("qwen3"))],
            tensors=[
                ("direction.10", [4], 0, struct.pack("<4f", 0.5, 0.5, 0.5, 0.5)),
                ("direction.11", [4], 0, struct.pack("<4f", float("nan"), 0.5, 0.5, 0.5)),
            ],
        )
        r_bad = self.run_script("control-vector-forge/scripts/cvector_forge.py", "inspect-gguf", corrupt)
        self.assertEqual(r_bad.returncode, 1, r_bad.stdout + r_bad.stderr)
        self.assertIn("CVECTOR: CORRUPT", r_bad.stdout)

    def test_score_sweep_gates_perplexity_and_inverted_control(self) -> None:
        good_sweep = self.put(
            "good_sweep.json",
            json.dumps({
                "vector_file": "mythos-engineering.gguf",
                "runs": [
                    {"layer_range": "14..34", "scale": 0.0, "ppl": 6.00, "task_score": 0.90, "behavior_score": 0.50},
                    {"layer_range": "14..34", "scale": -0.5, "ppl": 6.10, "task_score": 0.89, "behavior_score": 0.32},
                    {"layer_range": "14..34", "scale": 0.5, "ppl": 6.09, "task_score": 0.89, "behavior_score": 0.82},
                    {"layer_range": "14..34", "scale": 1.25, "ppl": 6.80, "task_score": 0.72, "behavior_score": 0.95},
                ],
            }),
        )
        r_ok = self.run_script("control-vector-forge/scripts/cvector_forge.py", "score-sweep", good_sweep)
        self.assertEqual(r_ok.returncode, 0, r_ok.stdout + r_ok.stderr)
        self.assertIn("--control-vector-scaled mythos-engineering.gguf 0.5 --control-vector-layer-range 14 34", r_ok.stdout)

        bad_dir = self.put(
            "bad_dir.json",
            json.dumps({
                "vector_file": "noise.gguf",
                "runs": [
                    {"layer_range": "14..34", "scale": 0.0, "ppl": 6.00, "task_score": 0.90, "behavior_score": 0.50},
                    {"layer_range": "14..34", "scale": -0.5, "ppl": 6.05, "task_score": 0.89, "behavior_score": 0.55},
                    {"layer_range": "14..34", "scale": 0.5, "ppl": 6.05, "task_score": 0.89, "behavior_score": 0.70},
                ],
            }),
        )
        r_bad = self.run_script("control-vector-forge/scripts/cvector_forge.py", "score-sweep", bad_dir)
        self.assertEqual(r_bad.returncode, 1, r_bad.stdout + r_bad.stderr)
        self.assertIn("FAILED_DIRECTION", r_bad.stdout)


class DrafterForgeTests(ScriptTestCase):
    def _make_tok_dir(self, name: str, vocab: dict, vocab_size: int, eos_id: int = 2) -> Path:
        d = self.scratch / name
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"vocab_size": vocab_size, "bos_token_id": 1, "eos_token_id": eos_id}), encoding="utf-8")
        (d / "tokenizer.json").write_text(json.dumps({"model": {"vocab": vocab}, "added_tokens": [{"id": eos_id, "content": "<eos>"}]}), encoding="utf-8")
        return d

    def test_check_vocab_accepts_padding_and_rejects_shifted_ids(self) -> None:
        base_vocab = {"<pad>": 0, "<bos>": 1, "<eos>": 2, "def": 3, "return": 4}
        t_dir = self._make_tok_dir("target", base_vocab, vocab_size=128)
        d_ok = self._make_tok_dir("drafter_ok", base_vocab, vocab_size=64)
        res_ok = self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "check-vocab", t_dir, d_ok)
        self.assertEqual(res_ok.returncode, 0, res_ok.stdout + res_ok.stderr)
        self.assertIn("VOCAB: COMPATIBLE", res_ok.stdout)

        bad_vocab = {"<pad>": 0, "<bos>": 1, "<eos>": 2, "return": 3, "def": 4}
        d_bad = self._make_tok_dir("drafter_bad", bad_vocab, vocab_size=128)
        res_bad = self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "check-vocab", t_dir, d_bad)
        self.assertEqual(res_bad.returncode, 1, res_bad.stdout + res_bad.stderr)
        self.assertIn("VOCAB: INCOMPATIBLE", res_bad.stdout)

    def test_analyze_acceptance_and_gate_parity(self) -> None:
        good_acc = self.put(
            "acc_good.json",
            json.dumps({
                "base_step_ms": 20.0,
                "draft_step_ms": 1.2,
                "position_probs": [0.88, 0.82, 0.76, 0.68, 0.55],
                "verify_ms_by_width": {"2": 20.5, "3": 21.0, "4": 21.5, "5": 22.0, "6": 23.0},
            }),
        )
        r_good = self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "analyze-acceptance", good_acc)
        self.assertEqual(r_good.returncode, 0, r_good.stdout + r_good.stderr)
        self.assertIn("DRAFTER: VIABLE", r_good.stdout)

        slow_acc = self.put(
            "acc_slow.json",
            json.dumps({
                "base_step_ms": 10.0,
                "draft_step_ms": 4.0,
                "position_probs": [0.25, 0.15, 0.10],
                "verify_ms_by_width": {"2": 14.0, "3": 19.0, "4": 25.0},
            }),
        )
        r_slow = self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "analyze-acceptance", slow_acc)
        self.assertEqual(r_slow.returncode, 1, r_slow.stdout + r_slow.stderr)
        self.assertIn("SLOWER_THAN_SERIAL", r_slow.stdout)

        sha = "a" * 64
        serial = self.put("serial.json", json.dumps({"cells": {"code": {"token_sha256": sha, "decode_tps": 50.0}}}))
        drafted = self.put("drafted.json", json.dumps({"cells": {"code": {"token_sha256": sha, "decode_tps": 110.0}}}))
        r_gate = self.run_script(
            "tensorfold/drafter-forge/scripts/drafter_forge.py",
            "gate-parity", serial, drafted, "--model", "org/target", "--drafter", "org/draft",
        )
        self.assertEqual(r_gate.returncode, 0, r_gate.stdout + r_gate.stderr)
        self.assertIn("DRAFTER GATE: PASS", r_gate.stdout)
        self.assertIn("tensorfold serve org/target --drafter org/draft", r_gate.stdout)

        bad_drafted = self.put("bad_drafted.json", json.dumps({"cells": {"code": {"token_sha256": "b" * 64, "decode_tps": 110.0}}}))
        r_bad = self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "gate-parity", serial, bad_drafted)
        self.assertEqual(r_bad.returncode, 1, r_bad.stdout + r_bad.stderr)
        self.assertIn("REJECTED_EXACTNESS", r_bad.stdout)


class MoeXrayTests(ScriptTestCase):
    def test_profile_residency_prune_and_router_drift(self) -> None:
        lines = []
        # Skewed trace: experts 0,1,2 handle >90% of traffic across 2 layers; experts 5..7 are dead.
        for _ in range(95):
            lines.append(json.dumps({"layer": 0, "experts": [0, 1], "domain": "code"}))
            lines.append(json.dumps({"layer": 1, "experts": [1, 2], "domain": "code"}))
        for _ in range(5):
            lines.append(json.dumps({"layer": 0, "experts": [2, 3], "domain": "prose"}))
            lines.append(json.dumps({"layer": 1, "experts": [3, 4], "domain": "prose"}))
        trace_a = self.put("trace_a.jsonl", "\n".join(lines) + "\n")
        prof_a = self.scratch / "prof_a.json"
        r_prof = self.run_script(
            "tensorfold/moe-xray/scripts/moe_xray.py",
            "profile", trace_a, "--num-experts", "8", "--json", prof_a,
        )
        self.assertEqual(r_prof.returncode, 0, r_prof.stdout + r_prof.stderr)
        pa = json.loads(prof_a.read_text(encoding="utf-8"))
        self.assertEqual(pa["layers"]["0"]["zero_hit_count"], 4)

        res_json = self.scratch / "res.json"
        r_res = self.run_script(
            "tensorfold/moe-xray/scripts/moe_xray.py",
            "residency", prof_a, "--vram-budget-gib", "0.5", "--expert-size-mib", "128", "--json", res_json,
        )
        self.assertEqual(r_res.returncode, 0, r_res.stdout + r_res.stderr)
        rdoc = json.loads(res_json.read_text(encoding="utf-8"))
        self.assertGreater(rdoc["gpu_hit_rate_pct"], 90.0)
        self.assertIn("--ssd-experts", rdoc["serve_hint"])

        prune_json = self.scratch / "prune.json"
        r_pr = self.run_script(
            "tensorfold/moe-xray/scripts/moe_xray.py",
            "prune-plan", prof_a, "--max-route-loss-pct", "2.0", "--min-keep-per-layer", "2", "--json", prune_json,
        )
        self.assertEqual(r_pr.returncode, 0, r_pr.stdout + r_pr.stderr)
        pdoc = json.loads(prune_json.read_text(encoding="utf-8"))
        self.assertGreater(pdoc["dropped_slots"], 0)

        # Shifted trace on different experts -> must trigger DRIFT_DETECTED
        shifted = "\n".join(
            json.dumps({"layer": l, "experts": [6, 7]}) for _ in range(50) for l in (0, 1)
        ) + "\n"
        trace_b = self.put("trace_b.jsonl", shifted)
        prof_b = self.scratch / "prof_b.json"
        self.run_script("tensorfold/moe-xray/scripts/moe_xray.py", "profile", trace_b, "--num-experts", "8", "--json", prof_b)
        r_diff = self.run_script("tensorfold/moe-xray/scripts/moe_xray.py", "diff", prof_a, prof_b, "--max-jsd", "0.05")
        self.assertEqual(r_diff.returncode, 1, r_diff.stdout + r_diff.stderr)
        self.assertIn("DRIFT_DETECTED", r_diff.stdout)


class ThinkGovernorTests(ScriptTestCase):
    def test_ladder_profile_tune_and_trace_loop_scanner(self) -> None:
        ladder_path = self.scratch / "ladder.json"
        r_gen = self.run_script("think-governor/scripts/think_governor.py", "generate", "--seed", "7", "--per-tier", "2", "--out", ladder_path)
        self.assertEqual(r_gen.returncode, 0, r_gen.stdout + r_gen.stderr)
        ladder = json.loads(ladder_path.read_text(encoding="utf-8"))
        by_prompt = {c["prompt"]: c for c in ladder["cases"]}

        def make_handler(arm: str):
            def handle(_path, body):
                case = by_prompt[body["messages"][-1]["content"]]
                tier = case["tier"]
                exp = case["expected"]
                if arm == "no-thinking":
                    ans = exp if tier in ("trivial", "loop_trap") else "0"
                    return 200, {"choices": [{"message": {"role": "assistant", "content": f"FINAL_ANSWER: {ans}"}, "finish_reason": "stop"}]}
                if arm == "budget-1024":
                    return 200, {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "reasoning_content": "Step 1 compute carefully. Step 2 verify result.",
                                "content": f"FINAL_ANSWER: {exp}",
                            },
                            "finish_reason": "stop",
                        }],
                        "usage": {"completion_tokens": 220, "completion_tokens_details": {"reasoning_tokens": 200}},
                    }
                # unbounded arm loops on loop_trap and exhausts tokens
                if tier == "loop_trap":
                    loop_txt = "Wait, let me double check whether the parallel servers finish together. " * 6
                    return 200, {
                        "choices": [{"message": {"role": "assistant", "reasoning_content": loop_txt, "content": ""}, "finish_reason": "length"}],
                        "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}},
                    }
                return 200, {
                    "choices": [{"message": {"role": "assistant", "reasoning_content": "Thinking " * 200, "content": f"FINAL_ANSWER: {exp}"}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 1200, "completion_tokens_details": {"reasoning_tokens": 1150}},
                }
            return handle

        runs = self.scratch / "gov_runs"
        for arm, bgt in (("no-thinking", None), ("budget-1024", "1024"), ("unbounded", None)):
            srv = _MockOpenAIServer(make_handler(arm))
            self.addCleanup(srv.close)
            args = ["think-governor/scripts/think_governor.py", "profile", "--ladder", ladder_path, "--url", srv.url, "--model", "thinker", "--arm", arm, "--out", runs / f"{arm}.json"]
            if bgt:
                args.extend(["--thinking-budget", bgt])
            r = self.run_script(*args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        pol_path = self.scratch / "policy.json"
        r_tune = self.run_script("think-governor/scripts/think_governor.py", "tune", "--dir", runs, "--policy-out", pol_path)
        self.assertEqual(r_tune.returncode, 0, r_tune.stdout + r_tune.stderr)
        self.assertIn("GOVERNOR: POLICY_READY", r_tune.stdout)
        pol = json.loads(pol_path.read_text(encoding="utf-8"))
        self.assertEqual(pol["policy"]["trivial"]["selected_arm"], "no-thinking")
        self.assertEqual(pol["policy"]["hard"]["selected_arm"], "budget-1024")

        stuck = self.put("stuck.txt", "Wait, let me double check the register update before answering cleanly. " * 5)
        r_scan = self.run_script("think-governor/scripts/think_governor.py", "scan-trace", stuck)
        self.assertEqual(r_scan.returncode, 1, r_scan.stdout + r_scan.stderr)
        self.assertIn("TRACE: LOOP_DETECTED", r_scan.stdout)


class DistillFoundryTests(ScriptTestCase):
    def test_verify_curate_split_and_audit_manifest(self) -> None:
        sft_out = self.scratch / "sft.jsonl"
        dpo_out = self.scratch / "dpo.jsonl"
        vrep = self.scratch / "vrep.json"
        r_ver = self.run_script(
            "distill-foundry/scripts/foundry.py",
            "verify",
            "--in", REPO / "skills/distill-foundry/assets/trace.example.jsonl",
            "--out-sft", sft_out,
            "--out-dpo", dpo_out,
            "--report", vrep,
        )
        self.assertEqual(r_ver.returncode, 0, r_ver.stdout + r_ver.stderr)
        rep = json.loads(vrep.read_text(encoding="utf-8"))
        self.assertEqual((rep["verified_pass"], rep["verified_fail"], rep["dpo_pairs_built"]), (3, 1, 1))

        # Add slop + near-dup + eval-contaminated traces and verify curate drops them
        extra = [
            {"prompt_id": "p_slop", "finish_reason": "stop", "prompt": "Explain mutex locks clearly.", "response": "Great question! Let us delve into this robust seamless tapestry \u2014 a pivotal testament."},
            {"prompt_id": "p_contam", "finish_reason": "stop", "prompt": "Held out secret benchmark prompt about reversing linked lists in constant memory.", "response": "Use three pointers prev, curr and nxt."},
            {"prompt_id": "p_clean_4", "finish_reason": "stop", "prompt": "Compute 9 squared cleanly.", "response": "9 squared is 81."},
            {"prompt_id": "p_clean_5", "finish_reason": "stop", "prompt": "Name the default HTTP port.", "response": "HTTP defaults to TCP port 80."},
        ]
        combined = self.put("combined.jsonl", sft_out.read_text(encoding="utf-8") + "\n".join(json.dumps(x) for x in extra) + "\n")
        eval_file = self.put("eval.jsonl", json.dumps({"prompt": "Held out secret benchmark prompt about reversing linked lists in constant memory."}) + "\n")
        cur_out = self.scratch / "curated.jsonl"
        crep = self.scratch / "crep.json"
        r_cur = self.run_script(
            "distill-foundry/scripts/foundry.py",
            "curate", "--in", combined, "--out", cur_out, "--eval-prompts", eval_file, "--report", crep,
        )
        self.assertEqual(r_cur.returncode, 0, r_cur.stdout + r_cur.stderr)
        cdoc = json.loads(crep.read_text(encoding="utf-8"))
        self.assertEqual(cdoc["drop_reasons"].get("slop_filtered"), 1)
        self.assertEqual(cdoc["drop_reasons"].get("eval_contaminated"), 1)

        splits_dir = self.scratch / "splits"
        r_spl = self.run_script(
            "distill-foundry/scripts/foundry.py",
            "split", "--in", cur_out, "--dpo", dpo_out, "--out-dir", splits_dir, "--val-ratio", "0.2", "--test-ratio", "0.2",
        )
        self.assertEqual(r_spl.returncode, 0, r_spl.stdout + r_spl.stderr)
        r_aud = self.run_script("distill-foundry/scripts/foundry.py", "audit-manifest", splits_dir / "manifest.json")
        self.assertEqual(r_aud.returncode, 0, r_aud.stdout + r_aud.stderr)
        self.assertIn("FOUNDRY: VALID", r_aud.stdout)

        # Mutate train.jsonl to simulate drift -> audit-manifest must fail with exit 1
        with (splits_dir / "train.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"prompt_id": "tampered", "prompt": "x", "response": "y"}) + "\n")
        r_bad = self.run_script("distill-foundry/scripts/foundry.py", "audit-manifest", splits_dir / "manifest.json")
        self.assertEqual(r_bad.returncode, 1, r_bad.stdout + r_bad.stderr)
        self.assertIn("LEAK_OR_DRIFT", r_bad.stdout)


class CriticalPathXrayTests(ScriptTestCase):
    def test_audit_json_fz_log_dispatch_lie_and_bounds(self) -> None:
        trace = self.put(
            "trace.json",
            json.dumps({
                "wall_ms": 10.0,
                "gpu_ms": 7.5,
                "host_ms": 1.5,
                "idle_ms": 1.0,
                "kernels": [
                    {"name": "qmm_group_fp4", "gpu_ms": 6.0, "bytes": 4_000_000_000, "flops": 200_000_000_000, "launches": 48},
                    {"name": "rmsnorm_small", "gpu_ms": 0.4, "bytes": 50_000_000, "flops": 1_000_000_000, "launches": 64},
                ],
            }),
        )
        out_json = self.scratch / "xray.json"
        r_ok = self.run_script(
            "tensorfold/critical-path-xray/scripts/xray_trace.py",
            "audit", trace, "--chip", "rtx5090", "--expect-kernel", "qmm_group_fp4", "--json", out_json,
        )
        self.assertEqual(r_ok.returncode, 0, r_ok.stdout + r_ok.stderr)
        doc = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(doc["top_bottleneck"], "BETWEEN_PASS_HOST_OR_IDLE")
        self.assertEqual(doc["kernels"][1]["regime"], "LAUNCH_BOUND")

        r_miss = self.run_script(
            "tensorfold/critical-path-xray/scripts/xray_trace.py",
            "audit", trace, "--expect-kernel", "never_dispatched_kernel",
        )
        self.assertEqual(r_miss.returncode, 1, r_miss.stdout + r_miss.stderr)
        self.assertIn("EXPECTED_KERNEL_MISSING", r_miss.stdout)

        fz = self.put("fz.log", "round=1 gpu_ms=8.0 host_ms=1.2 idle_ms=0.8\nround=2 gpu_ms=8.2 host_ms=1.1 idle_ms=0.7\n")
        r_fz = self.run_script("tensorfold/critical-path-xray/scripts/xray_trace.py", "audit", fz)
        self.assertEqual(r_fz.returncode, 0, r_fz.stdout + r_fz.stderr)

        cands = self.put(
            "cands.json",
            json.dumps({
                "wall_ms": 10.0,
                "candidates": [
                    {"name": "fix_host_sync", "target_ms": 2.0, "expected_reduction_pct": 80, "overlap_group": "host"},
                    {"name": "alt_host_fix", "target_ms": 2.0, "expected_reduction_pct": 50, "overlap_group": "host"},
                    {"name": "tiny_tweak", "target_ms": 0.2, "expected_reduction_pct": 20, "overlap_group": "tiny"},
                ],
            }),
        )
        b_out = self.scratch / "bounds.json"
        r_bnd = self.run_script("tensorfold/critical-path-xray/scripts/xray_trace.py", "bound", cands, "--json", b_out)
        self.assertEqual(r_bnd.returncode, 0, r_bnd.stdout + r_bnd.stderr)
        bdoc = json.loads(b_out.read_text(encoding="utf-8"))
        # Overlapping savings in group 'host' counted once: max saved 1.6 ms -> 10/(10-1.6)-1 = +19.05%
        self.assertAlmostEqual(bdoc["combined_non_overlapping_gain_pct"], 19.05, places=1)


class SassAndAirDiffTests(ScriptTestCase):
    def test_ptxas_and_sass_diff_guards_untouched_kernels_and_spills(self) -> None:
        base_ptx = self.put(
            "base.log",
            "ptxas info : Compiling entry function 'qmm_fp4' for 'sm_120'\n"
            "ptxas info : Used 64 registers, 0 bytes spill stores, 0 bytes spill loads, 16384 bytes smem\n"
            "ptxas info : Compiling entry function 'rmsnorm_f32' for 'sm_120'\n"
            "ptxas info : Used 32 registers, 0 bytes spill stores, 0 bytes spill loads, 0 bytes smem\n",
        )
        cand_ok_ptx = self.put(
            "cand_ok.log",
            "ptxas info : Compiling entry function 'qmm_fp4' for 'sm_120'\n"
            "ptxas info : Used 60 registers, 0 bytes spill stores, 0 bytes spill loads, 16384 bytes smem\n"
            "ptxas info : Compiling entry function 'rmsnorm_f32' for 'sm_120'\n"
            "ptxas info : Used 32 registers, 0 bytes spill stores, 0 bytes spill loads, 0 bytes smem\n",
        )
        cand_reg_ptx = self.put(
            "cand_reg.log",
            "ptxas info : Compiling entry function 'qmm_fp4' for 'sm_120'\n"
            "ptxas info : Used 60 registers, 16 bytes spill stores, 16 bytes spill loads, 16384 bytes smem\n"
            "ptxas info : Compiling entry function 'rmsnorm_f32' for 'sm_120'\n"
            "ptxas info : Used 40 registers, 0 bytes spill stores, 0 bytes spill loads, 0 bytes smem\n",
        )
        b_j, ok_j, reg_j = self.scratch / "b.json", self.scratch / "ok.json", self.scratch / "reg.json"
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "parse-ptxas", base_ptx, "--out", b_j).returncode, 0)
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "parse-ptxas", cand_ok_ptx, "--out", ok_j).returncode, 0)
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "parse-ptxas", cand_reg_ptx, "--out", reg_j).returncode, 0)

        r_pass = self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "diff", b_j, ok_j, "--allow-changed", "qmm_fp4", "--forbid-new-spills")
        self.assertEqual(r_pass.returncode, 0, r_pass.stdout + r_pass.stderr)
        self.assertIn("BINARY GATE: PASS", r_pass.stdout)

        r_fail = self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "diff", b_j, reg_j, "--allow-changed", "qmm_fp4", "--forbid-new-spills")
        self.assertEqual(r_fail.returncode, 1, r_fail.stdout + r_fail.stderr)
        self.assertIn("BINARY GATE: REGRESSION", r_fail.stdout)
        self.assertIn("rmsnorm_f32", r_fail.stdout)


class OptimizationLedgerTests(ScriptTestCase):
    def test_preflight_blocks_repeat_and_score_ab_enforces_mirrored_protocol(self) -> None:
        led = self.scratch / "ledger.jsonl"
        r_builtin = self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "preflight", "--ledger", led, "--mechanism", "dispatch_count_only")
        self.assertEqual(r_builtin.returncode, 1, r_builtin.stdout + r_builtin.stderr)
        self.assertIn("BLOCKED_REPEAT", r_builtin.stdout)

        r_just = self.run_script(
            "tensorfold/optimization-ledger/scripts/ledger.py",
            "preflight", "--ledger", led, "--mechanism", "dispatch_count_only", "--changed-assumption", "fused kernel removes 4 ms L2 round trip",
        )
        self.assertEqual(r_just.returncode, 0, r_just.stdout + r_just.stderr)

        sha = "c" * 64
        good_ab = self.put(
            "ab.json",
            json.dumps({
                "target_cell": "decode_code",
                "cells": {
                    "decode_code": {
                        "order": ["B", "C", "C", "B"],
                        "base_vs_base": [80.0, 80.8, 80.4],
                        "rounds": [
                            {"base_tps": 80.2, "cand_tps": 90.0, "base_token_sha256": sha, "cand_token_sha256": sha, "temp_c": 70, "throttled": False},
                            {"base_tps": 80.5, "cand_tps": 90.4, "base_token_sha256": sha, "cand_token_sha256": sha, "temp_c": 72, "throttled": False},
                            {"base_tps": 80.1, "cand_tps": 70.0, "base_token_sha256": sha, "cand_token_sha256": sha, "temp_c": 92, "throttled": True},
                        ],
                    }
                },
            }),
        )
        r_ab = self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "score-ab", good_ab)
        self.assertEqual(r_ab.returncode, 0, r_ab.stdout + r_ab.stderr)
        self.assertIn("AB: CONFIRMED_GAIN", r_ab.stdout)

        bad_order = self.put(
            "bad_order.json",
            json.dumps({"target_cell": "c", "cells": {"c": {"order": ["B", "B", "C", "C"], "base_vs_base": [10, 10], "rounds": []}}}),
        )
        self.assertEqual(self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "score-ab", bad_order).returncode, 2)

        self.assertEqual(
            self.run_script(
                "tensorfold/optimization-ledger/scripts/ledger.py",
                "record", "--ledger", led, "--id", "exp-1", "--mechanism", "paired_fp4",
                "--backend", "cuda", "--family", "qwen3_5", "--hardware", "rtx5090",
                "--predicted-gain-pct", "10", "--measured-gain-pct", "12.3", "--noise-pct", "1.0",
                "--status", "KEPT", "--owed-ports", "metal:qwen3_5,cuda:gemma4",
            ).returncode,
            0,
        )
        r_owed = self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "owed", "--ledger", led)
        self.assertIn("metal:qwen3_5", r_owed.stdout)
        self.assertEqual(self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "mark-ported", "--ledger", led, "--id", "exp-1", "--target", "metal:qwen3_5").returncode, 0)
        r_owed2 = self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "owed", "--ledger", led)
        self.assertNotIn("metal:qwen3_5", r_owed2.stdout)


class GraphAndDispatchSurgeonTests(ScriptTestCase):
    def test_audit_gaps_and_verify_invariance(self) -> None:
        storm = self.put(
            "storm.jsonl",
            "\n".join(
                json.dumps({"rows": 4 if i % 2 == 0 else 8, "gpu_ms": 6.0, "host_sync_ms": 2.5, "enqueue_gap_ms": 0.5, "graph_recaptured": (i % 2 == 1)})
                for i in range(10)
            ) + "\n",
        )
        r_st = self.run_script("tensorfold/graph-and-dispatch-surgeon/scripts/dispatch_audit.py", "audit-gaps", storm)
        self.assertEqual(r_st.returncode, 1, r_st.stdout + r_st.stderr)
        self.assertIn("GRAPH_RECAPTURE_STORM", r_st.stdout)

        sha = "d" * 64
        mat = self.put(
            "mat.json",
            json.dumps({
                "reference_token_sha256": sha,
                "runs": [
                    {"label": "rows1_graph_on", "rows": 1, "token_sha256": sha, "post_warmup_recaptures": 0},
                    {"label": "rows7_graph_on", "rows": 7, "token_sha256": sha, "post_warmup_recaptures": 0},
                ],
            }),
        )
        r_inv = self.run_script("tensorfold/graph-and-dispatch-surgeon/scripts/dispatch_audit.py", "verify-invariance", mat)
        self.assertEqual(r_inv.returncode, 0, r_inv.stdout + r_inv.stderr)
        self.assertIn("INVARIANCE: PASS", r_inv.stdout)


class TensorfoldSpeedHuntTests(ScriptTestCase):
    def test_full_7_gate_closed_loop_and_tamper_detection(self) -> None:
        run_json = self.scratch / "run.json"
        driver = "tensorfold/tensorfold-speed-hunt/scripts/loop_driver.py"
        self.assertEqual(
            self.run_script(
                driver, "init", "--run", run_json, "--commit", "abc1234",
                "--hardware", "rtx5090", "--backend", "cuda", "--family", "qwen3_5",
                "--target-cell", "decode_code", "--baseline-tps", "80.0", "--allow-temp",
            ).returncode,
            0,
        )
        # Skipping ahead out of order must fail with exit 2
        self.assertEqual(
            self.run_script(driver, "attach", "--run", run_json, "--stage", "build", "--branch", "feat/x", "--mechanism", "paired_fp4").returncode,
            2,
        )

        # Stage 1: xray
        trace = self.put("t.json", json.dumps({"wall_ms": 10.0, "gpu_ms": 9.2, "host_ms": 0.5, "idle_ms": 0.3, "kernels": [{"name": "qmm_fp4", "gpu_ms": 8.0, "bytes": 3_000_000_000, "flops": 50_000_000_000, "launches": 48}]}))
        xray_j = self.scratch / "xray.json"
        self.assertEqual(self.run_script("tensorfold/critical-path-xray/scripts/xray_trace.py", "audit", trace, "--chip", "rtx5090", "--json", xray_j).returncode, 0)
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "xray", "--receipt", xray_j).returncode, 0)

        # Stage 2: preflight
        pre_j = self.scratch / "pre.json"
        self.assertEqual(self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "preflight", "--ledger", self.scratch / "led.jsonl", "--mechanism", "paired_fp4", "--json", pre_j).returncode, 0)
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "preflight", "--receipt", pre_j).returncode, 0)

        # Stage 3: build
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "build", "--branch", "feat/fp4", "--mechanism", "paired_fp4").returncode, 0)

        # Stage 4: binary_gate
        sass_b = self.put("sb.txt", "Function : qmm_fp4\n/*0000*/ HMMA.16816.F32 R0, R2, R4, R0 ;\nFunction : norm\n/*0000*/ FADD R0, R1, R2 ;\n")
        sass_c = self.put("sc.txt", "Function : qmm_fp4\n/*0000*/ HMMA.16816.F32 R0, R2, R4, R0 ;\n/*0010*/ FADD R0, R0, R1 ;\nFunction : norm\n/*0000*/ FADD R0, R1, R2 ;\n")
        sb_j, sc_j, bg_j = self.scratch / "sb.json", self.scratch / "sc.json", self.scratch / "bg.json"
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "parse-sass", sass_b, "--out", sb_j).returncode, 0)
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "parse-sass", sass_c, "--out", sc_j).returncode, 0)
        self.assertEqual(self.run_script("tensorfold/sass-and-air-diff/scripts/binary_gate.py", "diff", sb_j, sc_j, "--allow-changed", "qmm_fp4", "--json", bg_j).returncode, 0)
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "binary_gate", "--receipt", bg_j).returncode, 0)

        # Stage 5: parity_gate
        par_j = self.put("parity.json", json.dumps({"verdict": "IDENTICAL", "compared": 16, "mismatches": {}}))
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "parity_gate", "--receipt", par_j).returncode, 0)

        # Stage 6: bench_gate
        sha = "e" * 64
        ab_in = self.put(
            "ab_in.json",
            json.dumps({
                "target_cell": "decode_code",
                "cells": {
                    "decode_code": {
                        "order": ["B", "C", "C", "B"],
                        "base_vs_base": [80.0, 80.5],
                        "rounds": [
                            {"base_tps": 80.1, "cand_tps": 92.0, "base_token_sha256": sha, "cand_token_sha256": sha, "temp_c": 70},
                            {"base_tps": 80.3, "cand_tps": 92.4, "base_token_sha256": sha, "cand_token_sha256": sha, "temp_c": 71},
                        ],
                    }
                },
            }),
        )
        ab_j = self.scratch / "ab_out.json"
        self.assertEqual(self.run_script("tensorfold/optimization-ledger/scripts/ledger.py", "score-ab", ab_in, "--json", ab_j).returncode, 0)
        self.assertEqual(self.run_script(driver, "attach", "--run", run_json, "--stage", "bench_gate", "--receipt", ab_j).returncode, 0)

        # Stage 7: referee
        self.assertEqual(
            self.run_script(
                driver, "attach", "--run", run_json, "--stage", "referee",
                "--verdict", "CONFIRMED", "--referee-session", "fresh-ref-42", "--owed-ports", "metal:qwen3_5",
            ).returncode,
            0,
        )

        # check-land passes
        r_land = self.run_script(driver, "check-land", "--run", run_json)
        self.assertEqual(r_land.returncode, 0, r_land.stdout + r_land.stderr)
        self.assertIn("SPEED HUNT: READY_TO_LAND", r_land.stdout)
        self.assertIn("metal:qwen3_5", r_land.stdout)

        # Tamper with parity.json after attaching -> check-land must detect hash drift!
        par_j.write_text(json.dumps({"verdict": "IDENTICAL", "tampered": True}), encoding="utf-8")
        r_tamper = self.run_script(driver, "check-land", "--run", run_json)
        self.assertEqual(r_tamper.returncode, 1, r_tamper.stdout + r_tamper.stderr)
        self.assertIn("receipt_hash_drift", r_tamper.stdout)


if __name__ == "__main__":
    unittest.main()
