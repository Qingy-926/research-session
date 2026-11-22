"""Validate task-bounded review continuation; records do not authenticate approval."""

from __future__ import annotations

import datetime as dt
import json
import os

import review_evidence


def identity(path):
    return os.path.normcase(os.path.realpath(path))


def timestamp(value):
    result = dt.datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("task timestamps require a timezone")
    return result


def request_count(entries, scope, start):
    import review_ledger

    project_rows = [row for row in entries if row.get("project_id") == scope["project_id"]]
    # A row already registered before task start cannot represent a new request.
    window = [row for row in project_rows if row.get("kind") in ("send_intent", "attempt", "receipt")
              and timestamp(row["created_at"]) >= start]
    histories = {}

    def checked_history(row):
        key = (row["review_id"], row["point"])
        if key not in histories:
            summary = review_ledger.summarize(entries, scope["project_id"], *key, scope["canonical"])
            blocked = review_ledger.blocking_reason(summary)
            if blocked is not None:
                raise ValueError("task has unresolved send history: " + blocked[1])
            histories[key] = {entry["entry_id"] for entry in summary["group"]}
        return histories[key]

    intents = {row["intent_id"]: row for row in window if row["kind"] == "send_intent"}
    for row in window:
        if row["kind"] == "send_intent":
            continue
        if row["entry_id"] not in checked_history(row):
            continue
        key = row.get("intent_id") or row.get("receipt_sha256") or row["attempt_sha256"]
        if key in intents:
            continue
        record_path = row.get("receipt") or row.get("attempt")
        # The actual request may predate a scan that registered it during the task.
        try:
            with open(record_path, "rb") as handle:
                raw = handle.read()
            if review_evidence.sha(raw) != (row.get("receipt_sha256") or row.get("attempt_sha256")):
                raise ValueError("history payload no longer matches the ledger")
            when = timestamp(json.loads(raw)["created_at"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("cannot determine request time for ledger entry " + row["entry_id"]) from exc
        if when >= start:
            intents[key] = row
    used = 0
    for intent_id, intent in intents.items():
        group = checked_history(intent)
        attempts = {row["attempt_sha256"] for row in project_rows
                    if row.get("kind") == "attempt" and row["entry_id"] in group
                    and (row.get("intent_id") or row.get("attempt_sha256")) == intent_id
                    and row.get("attempt_sha256")}
        has_receipt = any(row["kind"] == "receipt" and row["entry_id"] in group
                          and (row.get("intent_id") or row.get("receipt_sha256")) == intent_id
                          for row in project_rows)
        # A valid-response receipt is one HTTP in addition to preceding failed
        # attempts. Explicit legacy truncation retries share their original intent.
        used += max(1, len(attempts) + has_receipt)
    return used


def approval_record(path, files, expected=None):
    raw = review_evidence.bound_file(path, files, expected)
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema") != "research-session/review-task/v1"
            or data.get("mode") not in ("autonomous", "unattended")
            or not isinstance(data.get("task_id"), str) or not data["task_id"].strip()
            or data.get("revoked") is not False):
        raise ValueError("task approval is invalid or revoked")
    source = data["approval_source"]
    if identity(source["path"]) == identity(path):
        raise ValueError("task approval source must be a separate user decision record")
    if not review_evidence.bound_file(source["path"], files, source["sha256"]).strip():
        raise ValueError("task approval source is empty")
    for key in ("max_requests", "reserved_requests", "closeout_seconds"):
        if type(data.get(key)) is not int or data[key] < 0:
            raise ValueError("task budget requires nonnegative integers")
    start, deadline = timestamp(data["started_at"]), timestamp(data["deadline"])
    link = data.get("continuation")
    effective = timestamp(link["effective_at"]) if "continuation" in data else start
    if not start <= effective < deadline or data["max_requests"] <= data["reserved_requests"]:
        raise ValueError("task time/request budget is invalid")
    return data, review_evidence.sha(raw), effective, deadline


def approval_chain(path, file_rows):
    files = {identity(row["path"]): row for row in file_rows}
    chain, sources, source_hashes = {}, set(), set()
    expected, successor = None, None
    while True:
        key = identity(path)
        if key in chain or key in sources:
            raise ValueError("cyclic task approval or reused approval artifact")
        data, sha, effective, deadline = approval_record(path, files, expected)
        source = identity(data["approval_source"]["path"])
        source_sha = data["approval_source"]["sha256"]
        if source in chain or source in sources or source_sha in source_hashes:
            raise ValueError("continuation needs a distinct user approval source")
        sources.add(source)
        source_hashes.add(source_sha)
        if successor is not None:
            newer, cutoff, new_deadline = successor
            mutable = {"approval_source", "deadline", "continuation"}
            if ({k: v for k, v in data.items() if k not in mutable}
                    != {k: v for k, v in newer.items() if k not in mutable}
                    or not effective < cutoff or not deadline < new_deadline):
                raise ValueError("continuation must preserve task scope, start and budget")
            window_end = min(deadline, cutoff)
        else:
            window_end = deadline
        chain[key] = (data, sha, effective, window_end)
        if "continuation" not in data:
            return chain
        successor = data, effective, deadline
        previous = data["continuation"]["previous"]
        path, expected = previous["path"], previous["sha256"]


def validate_receipt(receipt, chain):
    info = receipt["binding"].get("task_approval")
    if info is None:
        return
    match = chain.get(identity(info["path"]))
    if match is None:
        raise ValueError("task approval changed without a bound continuation")
    data, sha, effective, deadline = match
    if (info["sha256"] != sha or info["task_id"] != data["task_id"]
            or info["mode"] != data["mode"]
            or not effective <= timestamp(receipt["created_at"]) < deadline):
        raise ValueError("receipt is outside its bound task approval window")


def validate(path, binding, scope, timeout, entries=None):
    chain = approval_chain(path, binding["files"])
    data, sha, effective, deadline = chain[identity(path)]
    if (data.get("project_id") != scope["project_id"]
            or not isinstance(data.get("review_ids"), list)
            or binding["review_id"] not in data["review_ids"]
            or not isinstance(data.get("points"), list)
            or any(type(p) is not int or p not in (1, 2, 3) for p in data["points"])
            or binding["point"] not in data["points"]):
        raise ValueError("task approval does not cover this project/object/point")
    now = dt.datetime.now(dt.timezone.utc)
    if (not effective <= now < deadline
            or timeout <= 0 or (deadline - now).total_seconds() < timeout + data["closeout_seconds"]):
        raise ValueError("task time/request budget exhausted or insufficient closeout time")
    if entries is not None:
        # Count all project sends since task start, including legacy sends and failed
        # requests. Other channels consume the explicitly reserved allocation.
        start = timestamp(data["started_at"])
        used = request_count(entries, scope, start)
        if used + data["reserved_requests"] >= data["max_requests"]:
            raise ValueError("task request budget exhausted")
        import review_ledger

        for review_id in data["review_ids"]:
            for point in data["points"]:
                summary = review_ledger.summarize(entries, scope["project_id"], review_id,
                                                  point, scope["canonical"])
                for row in summary["canonical_receipts"]:
                    if timestamp(row["created_at"]) < start:
                        continue
                    with open(row["receipt"], "rb") as handle:
                        receipt_raw = handle.read()
                    if review_evidence.sha(receipt_raw) != row["receipt_sha256"]:
                        raise ValueError("task receipt no longer matches the ledger")
                    receipt = json.loads(receipt_raw)
                    if timestamp(receipt["created_at"]) >= start:
                        validate_receipt(receipt, chain)
    return {"path": os.path.realpath(path), "sha256": sha,
            "task_id": data["task_id"], "mode": data["mode"]}


def validate_progress(binding, previous, history=()):
    report = previous.get("report")
    if not isinstance(report, dict) or report.get("decision") == "通过":
        raise ValueError("task progress requires a valid adverse baseline, never an unknown response")
    approval_path = binding["task_approval"]["path"]
    chain = approval_chain(approval_path, binding["files"])
    validate_receipt(previous, chain)
    active = {row["id"] for row in report["findings"] if row["kind"] == "blocking"}
    progress = binding.get("remediation_progress")
    if not isinstance(progress, list) or not progress:
        raise ValueError("task progress evidence is required before another review")
    files = {identity(row["path"]): row for row in binding["files"]}
    old = {identity(row["path"]): row["sha256"] for row in previous["binding"]["files"]}
    excluded = set(chain)
    excluded.update(identity(data["approval_source"]["path"]) for data, *_ in chain.values())
    for row in progress:
        if not isinstance(row, dict) or row.get("finding_id") not in active:
            raise ValueError("task progress must address an active blocking ID")
        path, verification = identity(row["path"]), identity(row["verification"])
        if verification not in {identity(p) for p in binding.get("verifications", [])}:
            raise ValueError("task progress verification must be declared in verifications")
        checked = review_evidence.validate_verification(verification, files)
        record = json.loads(review_evidence.bound_file(verification, files))
        excluded.update([verification, *[identity(r["path"]) for r in record["artifacts"]]])
        if (path in excluded or path.endswith(".review.json") or path not in files
                or old.get(path) == files[path]["sha256"]
                or path not in {identity(r["path"]) for r in record["inputs"]}
                or checked["exit_code"] != 0
                or timestamp(checked["started_at"]) < timestamp(previous["created_at"])):
            raise ValueError("task progress needs a changed controlled source and fresh successful verification")
        for prior in history:
            prior_files = {identity(r["path"]): r["sha256"] for r in prior["binding"]["files"]}
            if prior_files.get(path) == files[path]["sha256"]:
                raise ValueError("task progress repeats previously reviewed source bytes")
