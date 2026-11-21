"""Conservative extraction of training/evaluation facts from logs and JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

PATTERNS = {
    "epoch": re.compile(r"\bepoch\s*[=:]?\s*(\d+)", re.I),
    "loss": re.compile(r"\bloss\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)", re.I),
    "train_acc": re.compile(r"\btrain_acc(?:uracy)?\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)%?", re.I),
    "eer": re.compile(r"\b(?:open(?:set)?[_ ]?eer|eer)\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)%?", re.I),
    "verification_eer": re.compile(r"\bverification[_ ]?eer\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)%?", re.I),
    "top1": re.compile(r"\btop[- ]?1\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)%?", re.I),
}


def extract_text_summary(text: str) -> dict:
    values = {}
    for name, pattern in PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            values[name] = [float(x) if "." in x else int(x) for x in matches]
    checkpoints = re.findall(r"[\w./\\:-]+\.(?:pt|pth)\b", text, re.I)
    return {"metrics": values, "checkpoints": sorted(set(checkpoints)),
            "has_traceback": "traceback" in text.lower(),
            "completed": any(x in text.lower() for x in ("完成", "completed", "finished"))}


def summarize_file(path: Path) -> dict:
    path = Path(path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"path": str(path), "kind": "json", "data": data}
        except Exception:
            pass
    return {"path": str(path), "kind": "log",
            **extract_text_summary(path.read_text(encoding="utf-8", errors="replace"))}
