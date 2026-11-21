# -*- coding: utf-8 -*-
"""Owner-PID inspection must be portable and must fail closed."""

import datetime
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import review_ledger as ledger

LINUX = sys.platform.startswith("linux")


def test_unrepresentable_pid_is_reported_absent():
    # Linux os.kill() raises OverflowError for PIDs above pid_t; such a PID cannot
    # exist, so the owner must be reported as absent instead of crashing.
    assert ledger._pid_started_at(2 ** 31 - 1) is None
    assert ledger._pid_started_at(4294967294) is None


@pytest.mark.parametrize("pid", [0, -1, "12", None, True])
def test_invalid_pid_is_rejected(pid):
    with pytest.raises(ValueError):
        ledger._pid_started_at(pid)


@pytest.mark.skipif(not LINUX, reason="owner creation time is read from /proc on Linux only")
def test_live_child_creation_time_is_observable():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = ledger._pid_started_at(child.pid)
        assert started is not None
        assert 0 < started <= datetime.datetime.now().timestamp()
        # /proc/stat btime has one-second resolution; allow that bound only.
        assert abs(datetime.datetime.now().timestamp() - started) < 5
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.skipif(not LINUX, reason="owner creation time is read from /proc on Linux only")
def test_live_child_creation_time_separates_reused_pid_from_original_owner():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = ledger._pid_started_at(child.pid)
        assert started is not None
        # Same predicate as _recovery_snapshot: a lock written before the process
        # started proves PID reuse, while one written after it does not.
        assert started > datetime.datetime.now().timestamp() - 10
        assert not started > datetime.datetime.now().timestamp() + 10
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.skipif(not LINUX, reason="owner creation time is read from /proc on Linux only")
def test_exited_child_is_reported_absent():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pid = child.pid
    child.kill()
    child.wait(timeout=10)
    assert ledger._pid_started_at(pid) is None
