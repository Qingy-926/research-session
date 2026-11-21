"""Encoding detection and conservative mojibake repair helpers."""
from __future__ import annotations

import re

MOJIBAKE = re.compile(r"(?:[ÃÂ][\x80-\xbf]|[璇鍏浼鏃鐩寮鍚绛]|�)")


def mojibake_score(text: str) -> int:
    latin_artifacts = sum("\u00c0" <= ch <= "\u00ff" for ch in text)
    return len(MOJIBAKE.findall(text)) * 3 + latin_artifacts + text.count("\ufffd") * 5


def detect_encoding(data: bytes) -> dict:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16")
        return {"encoding": "utf-16", "text": text, "confidence": 1.0,
                "mojibake_score": mojibake_score(text)}
    try:
        utf8 = data.decode("utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
        # A valid UTF-8 stream is the preferred interpretation; utf-16/latin1
        # are permissive fallbacks and often turn bytes into plausible garbage.
        return {"encoding": "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8",
                "text": utf8, "confidence": 1.0,
                "mojibake_score": mojibake_score(utf8)}
    except UnicodeDecodeError:
        pass
    candidates = []
    for enc in ("gb18030", "latin1"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        penalty = mojibake_score(text)
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        candidates.append((penalty, -printable, enc, text))
    if not candidates:
        return {"encoding": "unknown", "text": data.decode("utf-8", errors="replace"),
                "confidence": 0.0, "mojibake_score": 99}
    # latin1 is a lossless fallback, not a preferred detector when UTF-8/GB18030
    # yields CJK text. Prefer the candidate with CJK characters, then score.
    candidates.sort(key=lambda x: (-(sum("\u3400" <= ch <= "\u9fff" for ch in x[3])),
                                   x[0], x[1]))
    penalty, neg_printable, enc, text = candidates[0]
    if enc == "latin1" and any(b >= 0x80 for b in data):
        try:
            utf8 = data.decode("utf-8")
            if sum("\u3400" <= ch <= "\u9fff" for ch in utf8) == 0 and any(
                    b in data for b in (0xFF, 0xFE, 0xFD)):
                enc, text = "unknown", utf8
        except UnicodeDecodeError:
            enc, text = "unknown", data.decode("utf-8", errors="replace")
    confidence = 1.0 if enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16") else 0.35
    return {"encoding": enc, "text": text, "confidence": confidence,
            "mojibake_score": penalty}


def clean_bytes(data: bytes) -> dict:
    detected = detect_encoding(data)
    text = detected["text"]
    # Repair UTF-8 decoded as a single-byte encoding only if the score improves.
    repaired = text
    try:
        candidate = text.encode("latin1").decode("utf-8")
        if mojibake_score(candidate) < mojibake_score(text):
            repaired = candidate
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    repaired = repaired.replace("\ufffd", "[乱码未能可靠恢复]")
    if detected["encoding"] == "unknown" and not repaired.endswith("[乱码未能可靠恢复]"):
        repaired += " [乱码未能可靠恢复]"
    if detected["encoding"] == "unknown":
        repaired = re.sub(r"[\udc80-\udcff]", "[乱码未能可靠恢复]", repaired)
    return {**detected, "text": repaired, "changed": repaired != text}
