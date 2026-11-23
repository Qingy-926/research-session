"""Offline structural checks for the GitHub Actions workflow.

These checks do not prove a remote run. They prevent the two regressions that
made the first three remote runs fail or become unenforceable:

* a workflow-level ``env`` value that uses the ``runner`` context;
* an aggregate gate that can pass when a matrix job fails, is cancelled, or
  is skipped.
"""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def read_workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def block(text, start, end=None):
    begin = text.index(start)
    stop = text.index(end, begin + len(start)) if end else len(text)
    return text[begin:stop]


def test_workflow_level_env_does_not_use_runner_context():
    text = read_workflow()
    env_block = block(text, "\nenv:\n", "\njobs:\n")
    assert "runner." not in env_block
    # The step that needs a cache prefix keeps it at step scope.
    compile_block = block(text, "      - name: Compile Python sources\n",
                          "      - name: Skill discovery and contracts\n")
    assert "PYTHONPYCACHEPREFIX: ${{ runner.temp }}/pycache" in compile_block


def test_offline_matrix_cannot_be_skipped_or_shrunk():
    text = read_workflow()
    offline = block(text, "  offline:\n", "  ci-ok:\n")
    assert not re.search(r"^    if:", offline, re.M), "offline job must not be conditionally skipped"
    assert "os: [windows-latest, ubuntu-latest]" in offline
    assert 'python-version: ["3.10", "3.13"]' in offline
    assert "fail-fast: false" in offline


def test_ci_ok_requires_success_and_runs_on_failure_or_cancellation():
    text = read_workflow()
    aggregate = block(text, "  ci-ok:\n")
    assert "needs: offline" in aggregate
    assert "if: always()" in aggregate
    assert "needs.offline.result" in aggregate
    assert '!= "success"' in aggregate
    assert "exit 1" in aggregate
