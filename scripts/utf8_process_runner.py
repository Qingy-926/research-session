"""Run a fixed child process while preserving UTF-8 stdout/stderr."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_utf8_process(argv: list[str], log_path: Path, cwd: Path,
                     env: dict[str, str] | None = None) -> int:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env.update(env or {})
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    with log_path.open("w", encoding="utf-8", buffering=1, newline="") as out:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=child_env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            text = raw.decode("utf-8", errors="replace")
            out.write(text)
            out.flush()
            print(text, end="", flush=True)
        return proc.wait()
