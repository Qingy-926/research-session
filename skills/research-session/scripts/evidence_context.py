"""Assemble selected, locally bound evidence excerpts within a character budget.

Reuses the citation guard's sources list (or {"sources": [...]}); each selected
row needs id, verified, path, quote, locator and access. Reads local UTF-8 text
only, never writes or calls a provider. This does not verify semantic support.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def normalize(value):
    return " ".join(value.split())


def build_context(sources_path, identifiers, max_chars):
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    source_file = Path(sources_path).resolve()
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("sources must be a list")
    index = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("each source needs a nonempty id")
        identifier = row["id"]
        if (identifier != identifier.strip()
                or any(char in "[]" or ord(char) < 32 or ord(char) == 127 for char in identifier)):
            raise ValueError("source id cannot round-trip through citation markers: " + repr(identifier))
        if identifier in index:
            raise ValueError("duplicate source id: " + identifier)
        index[identifier] = row
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError("select distinct source ids explicitly")
    result = {"ok": True, "context": "", "selected": [], "duplicates": [], "omitted": [],
              "semantic_support_verified": False}
    seen = {}
    for identifier in identifiers:
        if identifier not in index:
            raise ValueError("unknown source id: " + identifier)
        row = index[identifier]
        if row.get("verified") is not True:
            raise ValueError("source is not verified: " + identifier)
        for key in ("path", "quote", "locator"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("source %s needs %s" % (identifier, key))
        access = row.get("access")
        if access not in ("full_text", "abstract", "snippet"):
            raise ValueError("source access must be explicit: " + identifier)
        target = Path(row["path"])
        if not target.is_absolute():
            target = source_file.parent / target
        raw = target.read_bytes()
        if "source_sha256" in row and hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            raise ValueError("source hash changed: " + identifier)
        quote = normalize(row["quote"])
        if quote not in normalize(raw.decode("utf-8")):
            raise ValueError("quote does not occur in source: " + identifier)
        key = (str(target.resolve()), quote)
        if key in seen:
            result["duplicates"].append({"id": identifier, "duplicate_of": seen[key]})
            continue
        chunk = "[[cite:%s]] access=%s; locator=%s\n%s\n\n" % (
            identifier, access, row["locator"], row["quote"])
        if len(result["context"]) + len(chunk) > max_chars:
            result["omitted"].append({"id": identifier, "reason": "context_budget"})
            result["ok"] = False
            continue
        result["context"] += chunk
        result["selected"].append(identifier)
        seen[key] = identifier
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True)
    parser.add_argument("--id", dest="identifiers", action="append", required=True)
    parser.add_argument("--max-chars", type=int, default=16000)
    args = parser.parse_args(argv)
    try:
        result = build_context(args.sources, args.identifiers, args.max_chars)
    except (OSError, ValueError, TypeError) as exc:
        result = {"ok": False, "error": str(exc), "semantic_support_verified": False}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
