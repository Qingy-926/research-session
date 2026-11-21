import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from encoding_utils import clean_bytes, detect_encoding, mojibake_score


def test_detects_utf8_and_gb18030():
    assert detect_encoding("训练 epoch 1".encode("utf-8"))["encoding"] == "utf-8"
    assert detect_encoding("训练 epoch 1".encode("gb18030"))["encoding"] == "gb18030"


def test_repairs_common_mojibake_without_changing_numbers():
    bad = "训练".encode("utf-8").decode("latin1")
    result = clean_bytes(bad.encode("utf-8"))
    assert "训练" in result["text"]
    assert "训练" in result["text"]


def test_unrecoverable_bytes_are_marked():
    result = clean_bytes(b"epoch 2 loss 0.12 \xff\xfe")
    assert "[乱码未能可靠恢复]" in result["text"]


def test_mojibake_score_prefers_clean_text():
    assert mojibake_score("璇氬疄") > mojibake_score("真实")
