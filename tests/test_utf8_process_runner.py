import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from utf8_process_runner import run_utf8_process


def test_runner_preserves_utf8_output_and_exit_code(tmp_path):
    log = tmp_path / "run.log"
    code = run_utf8_process(
        [sys.executable, "-c", "import sys; print('训练✓'); print('错误', file=sys.stderr); sys.exit(3)"],
        log,
        tmp_path,
    )
    assert code == 3
    text = log.read_text(encoding="utf-8")
    assert "训练✓" in text and "错误" in text
