"""Offline citation and numeric-claim guard for drafts; standard library only.

Checks are mechanical and local: no network, no model, no writes.
Exit 0 = every check passed; exit 2 = refused. stdout is one JSON line with
stage/ok/code/first_problem.

  --sources  JSON list (or {"sources": [...]}) of {id, verified, path?, quote?}
  --evidence JSON list (or {"results": [...]}) of {id, ...}
  --require-search-note  refuse citations without a search-limitation note
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STAGE = "citation_guard"
CITE = re.compile(r"\[\[cite:([^\]]+)\]\]")
RESULT = re.compile(r"\[\[result:([^\]]+)\]\]")
CONCLUSION = re.compile(r"(提升|下降|提高|降低|准确率|召回率|提升到|下降到)")
DIGIT = re.compile(r"\d")
SEARCH_NOTE = re.compile(
    r"(检索限制|检索边界|检索范围|未检出|未检索到|search\s+limit|not\s+detected|no\s+relevant\s+result)", re.I)


def refuse(code, detail):
    return {"stage": STAGE, "ok": False, "code": code, "first_problem": detail}


def load_rows(path, list_key):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get(list_key) if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("%s must be a list of objects or {%r: [...]}" % (path, list_key))
    return {str(row["id"]): row for row in rows if isinstance(row.get("id"), str) and row["id"].strip()}


def normalize(text):
    return " ".join(text.split())


def check_sources(draft, sources_path):
    rows = load_rows(sources_path, "sources")
    base = Path(sources_path).resolve().parent
    for citation in CITE.findall(draft):
        source_id = citation.strip()
        row = rows.get(source_id)
        if row is None:
            return refuse("unknown_citation", "citation %s is not in sources" % source_id)
        if row.get("verified") is not True:
            return refuse("unverified_source", "source %s must be verified: true" % source_id)
        path, quote = row.get("path"), row.get("quote")
        if path is None and quote is None:
            continue
        if not isinstance(path, str) or not path.strip() or not isinstance(quote, str) or not quote.strip():
            return refuse("incomplete_source", "source %s needs both path and quote" % source_id)
        target = Path(path)
        if not target.is_absolute():
            target = base / target
        try:
            text = normalize(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return refuse("source_file_missing", "source %s file is unreadable: %s" % (source_id, path))
        if normalize(quote) not in text:
            return refuse("quote_not_found", "quote of %s does not occur in %s" % (source_id, path))
    return None


def check_evidence(draft, evidence_path):
    rows = load_rows(evidence_path, "results")
    for result_id in RESULT.findall(draft):
        if result_id.strip() not in rows:
            return refuse("unknown_result", "result %s is not in evidence" % result_id.strip())
    for sentence in re.split(r"(?<=[。！？!?])|\n", draft):
        if CONCLUSION.search(sentence) and DIGIT.search(sentence) and not RESULT.search(sentence):
            return refuse("unbound_claim", "conclusion number without [[result:...]]: %s" % sentence.strip()[:160])
    return None


def check_search_note(draft):
    if CITE.search(draft) and not SEARCH_NOTE.search(draft):
        return refuse("missing_search_note", "citations need an explicit search-limitation or not-detected note")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--sources")
    parser.add_argument("--evidence")
    parser.add_argument("--require-search-note", action="store_true")
    args = parser.parse_args(argv)
    try:
        draft = Path(args.draft).read_text(encoding="utf-8")
        problem = check_sources(draft, args.sources) if args.sources else None
        if problem is None and args.evidence:
            problem = check_evidence(draft, args.evidence)
        if problem is None and args.require_search_note:
            problem = check_search_note(draft)
    except (OSError, ValueError) as exc:
        problem = refuse("io_error", str(exc))
    if problem is not None:
        print(json.dumps(problem, ensure_ascii=True))
        return 2
    print(json.dumps({"stage": STAGE, "ok": True, "code": None, "first_problem": None}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
