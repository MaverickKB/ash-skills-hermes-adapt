import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from test_skill_regressions import ScriptTestCase


class DrafterVocabGateTests(ScriptTestCase):
    def make_tokenizer(self, name: str, vocab: dict[str, int], merges: list) -> Path:
        folder = self.scratch / name
        folder.mkdir()
        self.put(
            f"{name}/tokenizer.json",
            json.dumps({"model": {"type": "BPE", "vocab": vocab, "merges": merges}}),
        )
        return folder

    def check_vocab(self, target: Path, draft: Path):
        return self.run_script("tensorfold/drafter-forge/scripts/drafter_forge.py", "check-vocab", target, draft)

    def test_rejects_incomplete_id_overlap(self):
        target = self.make_tokenizer("target", {"a": 0, "b": 1}, [])
        for name, ids in (("disjoint", {"a": 2, "b": 3}), ("partial", {"a": 0, "b": 3})):
            with self.subTest(name=name):
                draft = self.make_tokenizer(name, ids, [])
                result = self.check_vocab(target, draft)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_compares_embedded_merges_in_rank_order(self):
        vocab = {"a": 0, "b": 1, "ab": 2, "ba": 3}
        target = self.make_tokenizer("target", vocab, ["a b", "b a"])
        wrong = self.make_tokenizer("wrong", vocab, [["b", "a"], ["a", "b"]])
        same = self.make_tokenizer("same", vocab, [["a", "b"], ["b", "a"]])
        bad = self.check_vocab(target, wrong)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("merge", bad.stdout.lower())
        good = self.check_vocab(target, same)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)


class MoePruningGateTests(ScriptTestCase):
    def test_rejects_invalid_drop_fractions_before_writing_a_plan(self):
        script = "tensorfold/moe-xray/scripts/moe_xray.py"
        trace = self.put("trace.jsonl", json.dumps({"layer": 0, "experts": [0, 1]}) + "\n")
        profile = self.scratch / "profile.json"
        result = self.run_script(script, "profile", trace, "--num-experts", 8, "--json", profile)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = self.scratch / "plan.json"
        for fraction in ("-0.125", "1.1", "nan"):
            with self.subTest(fraction=fraction):
                result = self.run_script(script, "prune-plan", profile, "--drop-fraction", fraction, "--json", plan)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("--drop-fraction", result.stderr)
                self.assertFalse(plan.exists())


class GovernorCoverageGateTests(ScriptTestCase):
    def test_requires_all_four_tiers(self):
        receipt = self.put(
            "receipt.json",
            json.dumps({
                "format": "think-governor-receipt-v1",
                "ladder_sha256": "a" * 64,
                "arm": "mock",
                "tiers": {"trivial": {
                    "accuracy_pct": 100, "truncated_empty": 0, "loops": 0,
                    "median_think_tokens": 0, "median_wall_s": 0.2,
                }},
            }),
        )
        policy = self.scratch / "policy.json"
        result = self.run_script("think-governor/scripts/think_governor.py", "tune", receipt, "--policy-out", policy)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("GOVERNOR: INCOMPLETE", result.stdout)
        output = json.loads(policy.read_text(encoding="utf-8"))
        self.assertEqual(output["unmet_tiers"], ["medium", "hard", "loop_trap"])


class FoundryManifestGateTests(ScriptTestCase):
    def audit(self, splits: dict):
        manifest = self.put("manifest.json", json.dumps({"format": "distill-foundry-manifest-v1", "splits": splits}))
        return self.run_script("distill-foundry/scripts/foundry.py", "audit-manifest", manifest)

    def entry(self, name: str, rows: list[dict]) -> dict:
        path = self.put(name + ".jsonl", "\n".join(map(json.dumps, rows)))
        return {"file": path.name, "rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_refuses_empty_manifest(self):
        result = self.audit({})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing", result.stdout.lower())

    def test_refuses_an_empty_sealed_split(self):
        result = self.audit({
            "train": self.entry("train", [{"prompt_id": "train", "prompt": "Training example", "response": "Answer"}]),
            "val": self.entry("val", []),
            "sealed_test": self.entry("sealed_test", []),
        })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sealed_test", result.stdout)

    def test_audits_dpo_train_for_sealed_leakage(self):
        sealed = {"prompt_id": "heldout", "prompt": "Test example", "response": "Answer"}
        result = self.audit({
            "train": self.entry("train", [{"prompt_id": "train", "prompt": "Training example", "response": "Answer"}]),
            "val": self.entry("val", []),
            "sealed_test": self.entry("sealed_test", [sealed]),
            "dpo_train": self.entry("dpo_train", [sealed]),
        })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("prompt_id leak", result.stdout)


class FoundryPythonSandboxTests(ScriptTestCase):
    def verify(self, record: dict):
        source = self.put("trace.jsonl", json.dumps(record) + "\n")
        output = self.scratch / "sft.jsonl"
        result = self.run_script("distill-foundry/scripts/foundry.py", "verify", "--in", source, "--out-sft", output)
        return result, output

    def python_record(self, response: str, tests: str = "assert True") -> dict:
        return {
            "prompt_id": "fixture", "prompt": "Synthetic sandbox check",
            "response": response, "verifier": {"type": "python_tests", "tests": tests},
        }

    def test_isolates_generated_python_from_host_state(self):
        outside = self.put("host-only.txt", "Synthetic data")
        program = f"""
import os
from pathlib import Path

assert not Path({str(outside)!r}).exists()
assert "FOUNDRY_TEST_SENTINEL" not in os.environ
assert os.geteuid() != 0
assert not Path("/proc/net/route").read_text().splitlines()[1:]
Path("/tmp/result.txt").write_text("works")
"""
        with patch.dict(os.environ, {"FOUNDRY_TEST_SENTINEL": "synthetic"}):
            result, out = self.verify(self.python_record(program, 'assert Path("/tmp/result.txt").read_text() == "works"'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["verified"])

    def test_missing_sandbox_refuses_to_execute_python(self):
        marker = self.scratch / "execution-marker"
        with patch.dict(os.environ, {"PATH": str(self.scratch)}):
            result, out = self.verify(self.python_record(f"from pathlib import Path; Path({str(marker)!r}).touch()"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("local Docker", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(out.exists())

    def test_non_python_verification_needs_no_sandbox(self):
        with patch.dict(os.environ, {"PATH": str(self.scratch)}):
            result, out = self.verify({
                "prompt_id": "answer", "prompt": "Two plus two", "response": "4",
                "verifier": {"type": "exact_answer", "expected": "4"},
            })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["verified"])
