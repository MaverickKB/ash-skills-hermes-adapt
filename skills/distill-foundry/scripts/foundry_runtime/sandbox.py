from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

IMAGE = "docker.io/library/python:3.12-slim"
CLIENT_ENV = {"PATH": os.defpath, "LC_ALL": "C"}


class SandboxError(Exception):
    """Python verification requires a working local sandbox."""


@dataclass(frozen=True)
class PythonSandbox:
    client: tuple[str, ...]
    image_id: str

    def run(self, script: str, timeout_s: float) -> tuple[bool, str]:
        name = "foundry-" + uuid.uuid4().hex
        command = [
            *self.client, "run", "--rm", "--interactive", "--init",
            "--name", name, "--pull=never", "--network=none", "--ipc=none",
            "--user", "65534:65534", "--read-only", "--cap-drop=ALL",
            "--security-opt", "no-new-privileges", "--pids-limit=32",
            "--memory=256m", "--memory-swap=256m", "--cpus=1", "--ulimit", "core=0:0",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            "--workdir", "/tmp", "--log-driver=none", "--no-healthcheck",
            "--entrypoint", "/usr/bin/timeout", self.image_id,
            "--kill-after=1s", f"{timeout_s}s", "/usr/local/bin/python", "-I", "-B", "-S", "-",
        ]
        try:
            result = subprocess.run(
                command,
                input=script,
                encoding="utf-8",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=CLIENT_ENV,
                timeout=timeout_s + 10,
            )
        except subprocess.TimeoutExpired:
            self.remove(name)
            return False, f"python_tests timed out after {timeout_s}s"
        except KeyboardInterrupt:
            self.remove(name)
            raise
        except OSError as exc:
            raise SandboxError(f"could not run the local Docker sandbox: {exc.strerror}") from None
        if result.returncode in (125, 126, 127):
            raise SandboxError(f"Docker could not start the Python sandbox (exit {result.returncode})")
        if result.returncode == 0:
            return True, "ok"
        if result.returncode in (124, 137):
            return False, "python_tests hit the sandbox time or memory limit"
        return False, f"python_tests failed (sandbox exit {result.returncode})"

    def remove(self, name: str) -> None:
        try:
            result = subprocess.run(
                [*self.client, "rm", "--force", name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                env=CLIENT_ENV,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SandboxError("could not confirm cleanup of the timed-out Python sandbox") from None
        if result.returncode and "no such container" not in result.stderr.lower():
            raise SandboxError("could not confirm cleanup of the timed-out Python sandbox")


@contextmanager
def local_python_sandbox() -> Iterator[PythonSandbox]:
    docker = shutil.which("docker")
    if docker is None:
        raise SandboxError("Python tests require a local Docker CLI and daemon")
    sockets = [Path.home() / ".docker/run/docker.sock", Path("/var/run/docker.sock")]
    if hasattr(os, "getuid"):
        sockets.insert(0, Path(f"/run/user/{os.getuid()}/docker.sock"))
    socket = next((path for path in sockets if path.is_socket()), None)
    if socket is None:
        raise SandboxError("Python tests require a local Docker daemon on a Unix socket")

    with tempfile.TemporaryDirectory(prefix="foundry-docker-") as config:
        client = (docker, "--config", config, "--host", f"unix://{socket}")
        try:
            inspected = subprocess.run(
                [*client, "image", "inspect", "--format", "{{.Id}}", IMAGE],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                env=CLIENT_ENV,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SandboxError("could not inspect the sandbox image on the local Docker daemon") from None
        image_id = inspected.stdout.strip()
        if inspected.returncode or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise SandboxError(f"start the local Docker daemon and run docker pull {IMAGE}")
        yield PythonSandbox(client, image_id)
