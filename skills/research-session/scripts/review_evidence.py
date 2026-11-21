"""Render byte-bound source and record explicit local verification commands.

CLI: --source PATH [--lines START END], or --verify RECORD --input PATH
     [--cwd DIR] -- COMMAND ARG ... . Linting never executes supplied commands.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


STATUSES = ("verified_local", "verified_external", "unverified", "absent")
RESULT_PATTERN = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    return os.path.normcase(os.path.realpath(path))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO timestamp with timezone")
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp timezone is required")
    return parsed


def check_status(row):
    if row.get("verification_status") not in STATUSES:
        raise ValueError("verification_status must be one of %s" % (STATUSES,))


def check_reproduction(value, files=None):
    if not isinstance(value, dict) or not isinstance(value.get("artifact"), str) or not value["artifact"]:
        raise ValueError("reproduction.artifact is required")
    command, pointer = value.get("command"), value.get("json_pointer")
    command_ok = isinstance(command, list) and bool(command) and all(isinstance(v, str) and v for v in command)
    pointer_ok = isinstance(pointer, str) and (pointer == "" or pointer.startswith("/"))
    if not command_ok and not pointer_ok:
        raise ValueError("reproduction requires an argv command or JSON pointer (not executed by lint)")
    if "observed" not in value or "expected" not in value:
        raise ValueError("reproduction requires observed and expected")
    if files is not None and identity(value["artifact"]) not in {identity(r["path"]) for r in files}:
        raise ValueError("reproduction artifact is not bound in files")


def validate_metadata(data, files=None):
    for key in ("timeline", "closures"):
        rows = data.get(key, [])
        if not isinstance(rows, list):
            raise ValueError("%s must be an array" % key)
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
                raise ValueError("%s requires unique nonempty IDs" % key)
            seen.add(row["id"])
            check_status(row)
            if not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
                raise ValueError("%s requires evidence or an explicit missing-record explanation" % key)
            if key == "timeline":
                if not isinstance(row.get("event"), str) or not row["event"].strip():
                    raise ValueError("timeline.event is required")
                if row.get("timestamp") is not None:
                    timestamp(row["timestamp"])
                if row["verification_status"] == "absent" and row.get("timestamp") is not None:
                    raise ValueError("absent timeline records cannot invent a timestamp")
    disputes = data.get("disputes", [])
    if not isinstance(disputes, list):
        raise ValueError("disputes must be an array")
    seen = set()
    for row in disputes:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip()
                                            for k in ("id", "finding_id", "claim")) or row["id"] in seen:
            raise ValueError("disputes require unique id, finding_id and claim")
        seen.add(row["id"])
        check_reproduction(row.get("reproduction"), files)


def source_bytes(path, start=None, end=None):
    raw = Path(path).read_bytes()
    raw.decode("utf-8")
    if start is None and end is None:
        return raw, raw
    lines = raw.splitlines(keepends=True)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        raise ValueError("invalid source line range")
    return raw, b"".join(lines[start - 1:end])


def render_source(path, start=None, end=None):
    path = Path(path).resolve()
    raw, excerpt = source_bytes(path, start, end)
    metadata = {"path": str(path), "sha256": sha(raw), "bytes": len(excerpt),
                "mode": "full" if start is None else "lines", "start_line": start, "end_line": end}
    runs = re.findall(rb"`+", excerpt)
    fence = "`" * max(3, 1 + max((len(v) for v in runs), default=0))
    language = {".py": "python", ".json": "json", ".md": "markdown"}.get(path.suffix, "text")
    return ("<!-- research-source " + json.dumps(metadata, ensure_ascii=True) + " -->\n" + fence + language + "\n"
            + excerpt.decode("utf-8") + "\n" + fence + "\n")


def result_counts(text):
    result = {}
    for count, kind in RESULT_PATTERN.findall(text):
        kind = "errors" if kind == "error" else kind
        if kind in result and result[kind] != int(count):
            raise ValueError("contradictory result counts on one summary line")
        result[kind] = int(count)
    return result


def pytest_summary(text):
    summaries = [result_counts(line) for line in text.splitlines()
                 if RESULT_PATTERN.search(line) and re.search(r"\bin \d+(?:\.\d+)?s\b", line)]
    return summaries[-1] if summaries else {}


def run_verification(output, command, inputs, cwd):
    """Execute only the explicit CLI argv; never run commands read from a pack."""
    output = Path(output).resolve()
    log_path = output.with_suffix(".log")
    if output.exists() or log_path.exists() or not inputs or not command:
        raise ValueError("verification needs new output/log paths, inputs and an explicit command")
    rows = [{"path": str(Path(p).resolve()), "sha256": sha(Path(p).read_bytes())} for p in inputs]
    started = datetime.datetime.now(datetime.timezone.utc)
    with log_path.open("xb") as log:
        run = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=False)
    finished = datetime.datetime.now(datetime.timezone.utc)
    for row in rows:
        row["after_sha256"] = sha(Path(row["path"]).read_bytes())
    value = {"schema": "research-session/verification/v1", "command": list(command), "cwd": str(Path(cwd).resolve()),
             "started_at": started.isoformat(), "finished_at": finished.isoformat(), "exit_code": run.returncode,
             "inputs": rows, "log": str(log_path),
             "artifacts": [{"path": str(log_path), "sha256": sha(log_path.read_bytes())}],
             "pytest_summary": pytest_summary(log_path.read_text(encoding="utf-8", errors="replace"))}
    with output.open("x", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=True, indent=2)
        fh.write("\n")
    return value


def bound_file(path, files, expected=None):
    row = files.get(identity(path))
    if row is None:
        raise ValueError("evidence file is not bound in inputs: %s" % path)
    raw = Path(path).read_bytes()
    if sha(raw) != row["sha256"] or (expected is not None and sha(raw) != expected):
        raise ValueError("evidence SHA256 mismatch: %s" % path)
    return raw


def validate_verification(path, files):
    raw = bound_file(path, files)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != "research-session/verification/v1":
        raise ValueError("invalid verification schema")
    if (not isinstance(value.get("command"), list) or not value["command"]
            or not all(isinstance(v, str) and v for v in value["command"])
            or not isinstance(value.get("cwd"), str) or not value["cwd"] or type(value.get("exit_code")) is not int):
        raise ValueError("verification requires command, cwd and integer exit_code")
    started, finished = timestamp(value.get("started_at")), timestamp(value.get("finished_at"))
    if finished < started or finished > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1):
        raise ValueError("invalid verification time interval")
    if not isinstance(value.get("inputs"), list) or not value["inputs"]:
        raise ValueError("verification.inputs cannot be empty")
    for row in value["inputs"]:
        if not isinstance(row, dict) or row.get("after_sha256") != row.get("sha256"):
            raise ValueError("verification inputs changed during the run")
        bound_file(row["path"], files, row["sha256"])
        if Path(row["path"]).stat().st_mtime_ns > int(started.timestamp() * 1e9):
            raise ValueError("verification is older than a current input modification")
    if not isinstance(value.get("artifacts"), list) or not value["artifacts"]:
        raise ValueError("verification.artifacts cannot be empty")
    for row in value["artifacts"]:
        bound_file(row["path"], files, row["sha256"])
    if identity(value["log"]) not in {identity(row["path"]) for row in value["artifacts"]}:
        raise ValueError("verification log is not in artifacts")
    summary = pytest_summary(bound_file(value["log"], files).decode("utf-8", errors="replace"))
    if value.get("pytest_summary", {}) != summary:
        raise ValueError("verification summary contradicts its log")
    if value["exit_code"] == 0 and (summary.get("failed", 0) or summary.get("errors", 0)):
        raise ValueError("successful exit contradicts failed tests in the log")
    return {"path": str(path), "sha256": sha(raw), "command": value["command"], "cwd": value["cwd"],
            "exit_code": value["exit_code"], "started_at": value["started_at"], "finished_at": value["finished_at"],
            "pytest_summary": summary, "verification_status": "verified_local"}


def lint_pack(raw, inputs):
    files = {identity(row["path"]): row for row in inputs["files"]}
    source_blocks, example_blocks, offset, narrative = 0, 0, 0, []
    # Parse fences by byte offsets: CRLF and missing final newline are evidence, not formatting.
    opening = re.compile(rb"(?m)^ {0,3}([`]{3,}|[~]{3,})[^\r\n]*\r?\n")
    while True:
        match = opening.search(raw, offset)
        if match is None:
            narrative.append(raw[offset:])
            break
        narrative.append(raw[offset:match.start()])
        before = raw[:match.start()].splitlines()
        marker = before[-1] if before else b""
        if marker.startswith(b"<!-- research-source ") and marker.endswith(b" -->"):
            metadata = json.loads(marker[len(b"<!-- research-source "):-4])
            original = bound_file(metadata["path"], files, metadata["sha256"])
            if metadata.get("mode") == "full":
                expected = original
            elif metadata.get("mode") == "lines":
                _, expected = source_bytes(metadata["path"], metadata.get("start_line"), metadata.get("end_line"))
            else:
                raise ValueError("source block must declare mode full or lines")
            if type(metadata.get("bytes")) is not int or metadata["bytes"] != len(expected):
                raise ValueError("source block length mismatch")
            end = match.end() + len(expected)
            closing = b"\n" + match.group(1) + b"\n"
            if raw[match.end():end] != expected or raw[end:end + len(closing)] != closing:
                raise ValueError("source block does not match the bound file bytes")
            offset = end + len(closing)
            source_blocks += 1
        else:
            if marker != b"<!-- research-example -->":
                raise ValueError("unbound code block: render source, or label a non-evidence example research-example")
            close = re.compile(rb"(?m)^ {0,3}" + re.escape(match.group(1)) + rb"[ \t]*\r?(?:\n|$)").search(raw, match.end())
            if close is None:
                raise ValueError("unclosed example fence")
            offset = close.end()
            example_blocks += 1
    paths = inputs.get("verifications", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
        raise ValueError("verifications must be an array of bound record paths")
    verified = [validate_verification(path, files) for path in paths]
    for line in b"".join(narrative).decode("utf-8").splitlines():
        counts = result_counts(line)
        if counts and not any(all(v["pytest_summary"].get(k) == n for k, n in counts.items()) for v in verified):
            raise ValueError("unbound or contradictory pytest result claim; attach current verification.json")
    validate_metadata(inputs, inputs["files"])
    return {"valid": True, "source_blocks": source_blocks, "example_blocks": example_blocks,
            "verifications": verified, "coverage": "marked source bytes and declared verification only; not semantic completeness"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source")
    mode.add_argument("--verify")
    parser.add_argument("--lines", nargs=2, type=int)
    parser.add_argument("--output", help="new UTF-8 source-block file; avoids shell redirection changing bytes")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.source:
            text = render_source(args.source, *(args.lines or (None, None)))
            if args.output:
                with Path(args.output).open("xb") as fh:
                    fh.write(text.encode("utf-8"))
            else:
                sys.stdout.buffer.write(text.encode("utf-8"))
        else:
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            value = run_verification(args.verify, command, args.input, args.cwd)
            print(json.dumps(value, ensure_ascii=True))
            return 0 if value["exit_code"] == 0 else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "gate_passed": False}, ensure_ascii=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
