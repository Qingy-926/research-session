# -*- coding: utf-8 -*-
"""跨目录复核台账：按项目身份、review_id 与复核点记录发送历史。

只由 audit_dispatch.py 写入；只追加，不修改回执、复核包或项目原件。
台账默认位于用户目录，避免整项目复制或搬迁后看不到历史。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

SCHEMA = "research-session/review-ledger/v1"
LEDGER_ENV = "RESEARCH_SESSION_LEDGER_DIR"
DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".agents", "state", "research-review")
LEDGER_NAME = "ledger.jsonl"
LOCK_WAIT_SECONDS = 5.0
PRUNE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".idea", ".mypy_cache", ".ruff_cache",
}
KINDS = {"send_intent", "receipt", "attempt", "lock", "coverage", "unreadable", "resolution"}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _strip_long_prefix(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _long_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def norm_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(_strip_long_prefix(path))))


def ledger_path(home: str | None = None) -> str:
    root = home or os.environ.get(LEDGER_ENV) or DEFAULT_HOME
    return os.path.join(os.path.abspath(os.path.expanduser(root)), LEDGER_NAME)


def is_project_dir(path: str) -> bool:
    if re.match(r"^project-\d+(?:-|$)", os.path.basename(path), re.IGNORECASE):
        return True
    if os.path.isfile(os.path.join(path, ".review-project.json")):
        return True
    return os.path.isfile(os.path.join(path, "AGENTS.md")) and os.path.isdir(os.path.join(path, "01-资料"))


def project_root_for_path(path: str) -> str | None:
    """Return the outermost enclosing research project, never a nested backup."""
    current = norm_path(path)
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    found: list[str] = []
    while True:
        if is_project_dir(current):
            found.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return found[-1] if found else None


def project_identity(root: str) -> str:
    """Stable identity: category + project number; explicit marker wins."""
    marker = os.path.join(root, ".review-project.json")
    if os.path.isfile(marker):
        try:
            with open(marker, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError("项目标识文件不可读: %s: %s" % (marker, exc))
        value = data.get("project_id") if isinstance(data, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("项目标识文件缺少 project_id: %s" % marker)
        return value.strip().casefold()
    category = os.path.basename(os.path.dirname(root)) or "root"
    base = os.path.basename(root)
    match = re.match(r"^(project-\d+)(?:-|$)", base, re.IGNORECASE)
    return ("%s/%s" % (category, match.group(1) if match else base)).casefold()


def canonical_reviews(root: str) -> str:
    return norm_path(os.path.join(root, "01-资料", "reviews"))


def _same_project_file(row: object, root: str) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return False
    candidate = project_root_for_path(row["path"])
    return candidate is not None and norm_path(candidate) == root


def validate_scope(pack_path: str, inputs: dict[str, Any]) -> dict[str, str]:
    """Require the project's single authoritative reviews directory."""
    root = project_root_for_path(pack_path)
    if root is None:
        raise ValueError("无法确定项目根目录；复核包须位于 project-<编号>-* 项目或含 AGENTS.md 与 01-资料 的项目内")
    canonical = canonical_reviews(root)
    try:
        common = os.path.commonpath([root, canonical])
    except ValueError:
        raise ValueError("权威 reviews 目录指向项目之外")
    if os.path.normcase(common) != os.path.normcase(root):
        raise ValueError("权威 reviews 目录指向项目之外")
    if norm_path(os.path.dirname(os.path.abspath(pack_path))) != canonical:
        raise ValueError("复核包须放在项目权威 01-资料/reviews 目录，不能从其他目录、备份或迁移副本送审")
    files = inputs.get("files") if isinstance(inputs, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError("输入清单缺少 files")
    if not any(_same_project_file(row, root) for row in files):
        raise ValueError("输入清单至少须包含同一项目的受控原件，不能仅依靠包位置确定项目")
    return {"project_root": root, "canonical": canonical, "project_id": project_identity(root)}


def _require(entry: dict[str, Any], key: str, number: int) -> None:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("复核台账第 %d 行缺少 %s" % (number, key))


def read_entries(home: str | None = None) -> list[dict[str, Any]]:
    path = ledger_path(home)
    if not os.path.isfile(path):
        return []
    entries: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            text = line.strip()
            if not text:
                continue
            try:
                entry = json.loads(text)
            except ValueError as exc:
                raise ValueError("复核台账损坏（%s 第 %d 行）：%s" % (path, number, exc))
            if not isinstance(entry, dict) or entry.get("schema") != SCHEMA:
                raise ValueError("复核台账第 %d 行 schema 无效" % number)
            _require(entry, "entry_id", number)
            _require(entry, "kind", number)
            _require(entry, "created_at", number)
            _require(entry, "project_id", number)
            _require(entry, "project_root", number)
            if entry["kind"] not in KINDS:
                raise ValueError("复核台账第 %d 行 kind 无效: %s" % (number, entry["kind"]))
            entries.append(entry)
    return entries


def _fingerprint(value: dict[str, Any]) -> str:
    keys = (
        "kind", "project_id", "project_root", "review_id", "point", "pack", "pack_sha256",
        "receipt", "receipt_sha256", "round", "cycle", "decision",
        "intent_id", "attempt", "failure_kind", "lock", "complete", "review_complete",
        "failed_paths", "skipped_paths", "unreadable_paths", "link_exclusions", "resolves", "note", "path", "error", "scan_id", "occurrence_id", "scan_version",
    )
    semantic = {key: value.get(key) for key in keys if key in value}
    raw = json.dumps(semantic, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _acquire(path: str, timeout: float = LOCK_WAIT_SECONDS) -> str:
    lock = path + ".lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode("ascii"))
            finally:
                os.close(fd)
            return lock
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("复核台账正被另一个会话写入；核查后重试，不自动删除锁: %s" % lock)
            time.sleep(0.05)


def _append_locked(home: str | None, path: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = {entry["entry_id"] for entry in read_entries(home)}
    added: list[dict[str, Any]] = []
    with open(path, "ab") as fh:
        for value in values:
            payload = dict(value)
            payload.setdefault("schema", SCHEMA)
            payload.setdefault("created_at", utc_now())
            entry_id = _fingerprint(payload)
            payload["entry_id"] = entry_id
            if entry_id in existing:
                continue
            line = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
            existing.add(entry_id)
            added.append(payload)
    return added


def append_entries(home: str | None, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        return _append_locked(home, path, values)
    finally:
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass


def claim_send(home: str | None, scope: dict[str, str], binding: dict[str, Any],
               pack: str, cycle: int, allow_existing_receipt: bool = False,
               previous_path: str | None = None,
               recovered_previous: bool = False) -> dict[str, Any]:
    """Atomically check history and claim the object before any model request."""
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        entries = read_entries(home)
        summary = summarize(entries, scope["project_id"], binding["review_id"],
                            binding["point"], scope["canonical"])
        blocked = blocking_reason(summary)
        if blocked is not None:
            return {"claimed": False, "reason": blocked, "summary": summary}
        if not allow_existing_receipt and summary["canonical_receipts"]:
            return {
                "claimed": False,
                "reason": ("review_history",
                           "该对象已有审查回执；复用原回执或按 --previous 整改，不得重复首审",
                           {"ledger_entries": summary["canonical_receipts"]}),
                "summary": summary,
            }
        if allow_existing_receipt:
            receipts = [row for row in summary["group"] if row.get("kind") == "receipt"
                        and row.get("cycle", 1) == cycle]
            if previous_path is None:
                return {"claimed": False, "reason": ("review_history", "previous receipt required", {}), "summary": summary}
            with open(previous_path, "rb") as previous_file:
                previous_sha = hashlib.sha256(previous_file.read()).hexdigest()
            accepted_previous_round = 0 if recovered_previous else 1
            if not any(row.get("receipt_sha256") == previous_sha and row.get("round") == accepted_previous_round for row in receipts):
                return {"claimed": False, "reason": ("review_history", "previous receipt missing or stale", {}), "summary": summary}
            for row in receipts:
                round_number = row.get("round")
                if type(round_number) is not int or round_number >= 2:
                    return {"claimed": False, "reason": ("review_limit", "review round limit or unknown round", {}), "summary": summary}
                if round_number <= 0 and not (recovered_previous and round_number == 0
                                              and row.get("receipt_sha256") == previous_sha):
                    return {"claimed": False, "reason": ("review_history", "unfinished receipt requires investigation", {}), "summary": summary}
        entry = send_intent(scope, binding, pack, cycle)
        added = _append_locked(home, path, [entry])
        if not added:
            raise ValueError("发送意图未写入台账；不发送")
        return {"claimed": True, "entry": added[0], "summary": summary}
    finally:
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass


def _base(kind: str, scope: dict[str, str], review_id: str | None = None,
          point: int | None = None, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": kind, "project_id": scope["project_id"], "project_root": scope["project_root"],
    }
    if review_id is not None:
        value["review_id"] = review_id
    if point is not None:
        value["point"] = point
    value.update({key: item for key, item in extra.items() if item is not None})
    return value


def send_intent(scope: dict[str, str], binding: dict[str, Any], pack: str, cycle: int) -> dict[str, Any]:
    return _base("send_intent", scope, binding["review_id"], binding["point"], pack=os.path.abspath(pack),
                 pack_sha256=binding["pack_sha256"], cycle=cycle, intent_id=uuid.uuid4().hex)


def receipt_entry(scope: dict[str, str], binding: dict[str, Any], path: str,
                  receipt: dict[str, Any], cycle: int) -> dict[str, Any]:
    with open(path, "rb") as fh:
        receipt_sha = hashlib.sha256(fh.read()).hexdigest()
    return _base("receipt", scope, binding["review_id"], binding["point"],
                 pack=os.path.abspath(receipt["pack"]["path"]),
                 pack_sha256=binding["pack_sha256"], receipt=os.path.abspath(path),
                 receipt_sha256=receipt_sha,
                 round=receipt.get("round"), cycle=cycle,
                 decision=(receipt.get("report") or {}).get("decision", "未完成"),
                 model=binding.get("model"), intent_id=receipt.get("intent_id"), classification="canonical_receipt")


def attempt_entry(scope: dict[str, str], binding: dict[str, Any], path: str,
                  failure_kind: str, cycle: int) -> dict[str, Any]:
    return _base("attempt", scope, binding["review_id"], binding["point"], pack_sha256=binding["pack_sha256"],
                 attempt=os.path.abspath(path), failure_kind=failure_kind, cycle=cycle)


def coverage_entry(scope: dict[str, str], failed_paths: list[dict[str, str]],
                   skipped_paths: list[dict[str, str]] | None = None,
                   unreadable_paths: list[dict[str, str]] | None = None,
                   link_exclusions: list[dict[str, str]] | None = None) -> dict[str, Any]:
    skipped = skipped_paths or []
    unreadable = unreadable_paths or []
    links = link_exclusions or []
    review_complete = not failed_paths and not skipped and not unreadable
    return _base("coverage", scope, scan_version=2, complete=review_complete and not skipped,
                 review_complete=review_complete, failed_paths=failed_paths,
                 skipped_paths=skipped, unreadable_paths=unreadable,
                 link_exclusions=links)


def _lexists_long(path: str) -> bool:
    candidate = path
    if os.name == "nt" and not candidate.startswith("\\\\?\\"):
        candidate = "\\\\?\\" + os.path.abspath(candidate)
    return os.path.lexists(candidate)


def _review_related_failure(path: str) -> bool:
    """A failed path only blocks coverage when it could hold review artifacts."""
    name = os.path.basename(path).casefold()
    if name == "reviews":
        return True
    if name.endswith(".review.json") or ".attempt-" in name or name.startswith(".review-send-"):
        return True
    for candidate in (os.path.join(path, "01-资料", "reviews"), os.path.join(path, "reviews")):
        if _lexists_long(candidate):
            return True
    return False


def history_for(entries: list[dict[str, Any]], project_id: str, review_id: str,
                point: int) -> list[dict[str, Any]]:
    return [
        entry for entry in entries
        if entry.get("kind") != "coverage"
        and entry.get("project_id") == project_id
        and entry.get("review_id") == review_id and entry.get("point") == point
    ]


def latest_coverage(entries: list[dict[str, Any]], project_id: str, root: str | None = None) -> dict[str, Any] | None:
    rows = [entry for entry in entries
            if entry.get("kind") == "coverage" and entry.get("project_id") == project_id
            and (root is None or norm_path(entry.get("project_root", "")) == norm_path(root))]
    return rows[-1] if rows else None


def _receipt_dir(entry: dict[str, Any]) -> str | None:
    path = entry.get("receipt")
    if not isinstance(path, str) or not path:
        return None
    return norm_path(os.path.dirname(path))


def _receipt_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("review_id"), entry.get("point"), entry.get("pack_sha256"),
        entry.get("model"), entry.get("round"), entry.get("cycle", 1), entry.get("decision"),
    )


def _object_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return entry.get("review_id"), entry.get("point")


def _receipt_exists(entry: dict[str, Any]) -> bool:
    path = entry.get("receipt")
    return isinstance(path, str) and bool(path) and os.path.isfile(path)


def _coverage_unreadables(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Older ledger versions deduplicated unreadable events without including the path.
    # Coverage retained the complete list; preserve every unresolved path as evidence.
    recovered = []
    for row in entries:
        if row.get("kind") != "coverage":
            continue
        for item in row.get("unreadable_paths") or []:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            key = hashlib.sha256((row["entry_id"] + "\0" + norm_path(item["path"])).encode("utf-8")).hexdigest()[:32]
            recovered.append({"kind": "unreadable", "entry_id": "coverage-" + key,
                              "project_id": row["project_id"], "project_root": row["project_root"],
                              "path": item["path"], "error": item.get("error", "unreadable")})
    return recovered


def summarize(entries: list[dict[str, Any]], project_id: str, review_id: str,
              point: int, canonical: str) -> dict[str, Any]:
    group = history_for(entries, project_id, review_id, point)
    canonical_real = norm_path(canonical)
    receipts = [entry for entry in group if entry.get("kind") == "receipt"]
    canonical_receipts = [entry for entry in receipts if _receipt_dir(entry) == canonical_real]
    existing_canonical_keys = {
        _receipt_key(entry) for entry in canonical_receipts if _receipt_exists(entry)
    }
    missing_receipts = [
        entry for entry in receipts
        if not _receipt_exists(entry) and _receipt_key(entry) not in existing_canonical_keys
    ]
    canonical_keys = {_receipt_key(entry) for entry in canonical_receipts}
    duplicate_copies: list[dict[str, Any]] = []
    outside_receipts: list[dict[str, Any]] = []
    for entry in receipts:
        if _receipt_dir(entry) == canonical_real:
            continue
        if entry.get("classification") == "duplicate_copy" or _receipt_key(entry) in canonical_keys:
            duplicate_copies.append(entry)
        else:
            outside_receipts.append(entry)
    resolutions = {entry.get("resolves") for entry in entries if entry.get("kind") == "resolution"}
    intents = [entry for entry in group if entry.get("kind") == "send_intent"]
    unresolved_intents = []
    for intent in intents:
        if intent.get("entry_id") in resolutions:
            continue
        if intent.get("intent_id") and any(entry.get("intent_id") == intent.get("intent_id") for entry in receipts):
            continue
        unresolved_intents.append(intent)
    locks = [entry for entry in group if entry.get("kind") == "lock"]
    unknown_locks = []
    receipt_objects = {_object_key(entry) for entry in receipts}
    for lock in locks:
        if lock.get("classification") == "orphan_lock_with_receipt":
            continue
        if _object_key(lock) in receipt_objects:
            continue
        unknown_locks.append(lock)
    coverage = latest_coverage(entries, project_id, os.path.dirname(os.path.dirname(canonical_real)))
    review_complete = False
    if coverage is not None:
        value = coverage.get("review_complete")
        review_complete = coverage.get("scan_version") == 2 and coverage.get("complete") is True and (value is True or value is None)
    roots = {norm_path(entry["project_root"]) for entry in entries
             if entry.get("project_id") == project_id and isinstance(entry.get("project_root"), str)}
    roots.add(norm_path(os.path.dirname(os.path.dirname(canonical_real))))
    incomplete_roots = [root for root in sorted(roots)
                        if (latest_coverage(entries, project_id, root) is None
                            or latest_coverage(entries, project_id, root).get("complete") is not True
                            or latest_coverage(entries, project_id, root).get("scan_version") != 2)]
    if incomplete_roots:
        review_complete = False
    project_unreadable = [
        entry for entry in entries + _coverage_unreadables(entries)
        if entry.get("kind") == "unreadable" and entry.get("project_id") == project_id
        and entry.get("entry_id") not in resolutions
    ]
    return {
        "group": group, "canonical_receipts": canonical_receipts,
        "outside_receipts": outside_receipts, "duplicate_copies": duplicate_copies,
        "missing_receipts": missing_receipts,
        "unresolved_intents": unresolved_intents, "unknown_locks": unknown_locks,
        "attempts": [entry for entry in group if entry.get("kind") == "attempt"],
        "unreadable": project_unreadable,
        "coverage": coverage,
        "coverage_complete": bool(coverage is not None and coverage.get("complete") is True),
        "coverage_review_complete": bool(review_complete),
        "incomplete_roots": incomplete_roots,
    }


def blocking_reason(summary: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    if summary["unreadable"]:
        paths = [entry.get("path") or entry.get("receipt") for entry in summary["unreadable"]]
        return ("ledger", "复核历史中有无法解析的记录，不能当作没有历史: %s" % ", ".join(str(p) for p in paths),
                {"ledger_entries": summary["unreadable"]})
    if summary["missing_receipts"]:
        paths = [entry.get("receipt") for entry in summary["missing_receipts"]]
        return ("review_history",
                "台账记录的回执已不在原路径: %s；先人工核对是否搬迁，不得直接重发" % ", ".join(str(p) for p in paths),
                {"ledger_entries": summary["missing_receipts"]})
    if summary["outside_receipts"]:
        paths = [entry.get("receipt") for entry in summary["outside_receipts"]]
        return ("review_history",
                "该对象已有复核回执在权威目录之外: %s；不得直接重发，先人工核对并归位" % ", ".join(str(p) for p in paths),
                {"ledger_entries": summary["outside_receipts"]})
    if summary["unresolved_intents"]:
        ids = [entry.get("entry_id") for entry in summary["unresolved_intents"]]
        return ("review_history",
                "该对象有未完成的发送意图，可能已请求上游: %s；先核查回执或 --ledger-backfill，确认未送出后用 --resolve-ledger <entry_id> --ledger-note <依据> 解除"
                % ", ".join(str(value) for value in ids),
                {"ledger_entries": summary["unresolved_intents"]})
    if summary["unknown_locks"]:
        paths = [entry.get("lock") for entry in summary["unknown_locks"]]
        return ("review_history",
                "该对象有来源不明的历史发送锁: %s；核查后归位，不得盲删重送" % ", ".join(str(p) for p in paths),
                {"ledger_entries": summary["unknown_locks"]})
    if not summary["coverage_review_complete"]:
        return ("review_scope",
                "项目复核历史扫描不完整，不能当作没有历史记录；修复访问后重跑 --ledger-backfill",
                {"coverage": summary["coverage"], "incomplete_roots": summary["incomplete_roots"]})
    return None


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _receipt_record(path: str, project_id: str, project_root: str) -> dict[str, Any]:
    data = _read_json(path)
    binding = data.get("binding") if isinstance(data, dict) else None
    review_id = binding.get("review_id") if isinstance(binding, dict) else None
    point = binding.get("point") if isinstance(binding, dict) else None
    if not isinstance(review_id, str) or not review_id.strip() or type(point) is not int:
        raise ValueError("回执缺少 binding.review_id/point")
    report = data.get("report") if isinstance(data, dict) else None
    decision = report.get("decision") if isinstance(report, dict) else "未完成"
    with open(path, "rb") as fh:
        receipt_sha = hashlib.sha256(fh.read()).hexdigest()
    return {
        "kind": "receipt", "project_id": project_id, "project_root": project_root,
        "review_id": review_id, "point": point,
        "pack": (data.get("pack") or {}).get("path"),
        "pack_sha256": binding.get("pack_sha256"),
        "receipt": os.path.abspath(_strip_long_prefix(path)),
        "receipt_sha256": receipt_sha,
        "round": data.get("round"), "cycle": data.get("cycle", 1), "decision": decision,
        "model": binding.get("model"), "intent_id": data.get("intent_id"),
    }


def _attempt_record(path: str, project_id: str, project_root: str) -> dict[str, Any]:
    data = _read_json(path)
    binding = data.get("binding") if isinstance(data, dict) else None
    review_id = binding.get("review_id") if isinstance(binding, dict) else None
    point = binding.get("point") if isinstance(binding, dict) else None
    if not isinstance(review_id, str) or not review_id.strip() or type(point) is not int:
        raise ValueError("尝试记录缺少 binding.review_id/point")
    return {
        "kind": "attempt", "project_id": project_id, "project_root": project_root,
        "review_id": review_id, "point": point, "pack_sha256": binding.get("pack_sha256"),
        "attempt": os.path.abspath(_strip_long_prefix(path)), "failure_kind": data.get("failure_kind"),
        "cycle": data.get("cycle", 1),
    }


def _lock_record(path: str, project_id: str, project_root: str,
                 skip_pid: int | None = None) -> dict[str, Any] | None:
    data = _read_json(path)
    if skip_pid is not None and isinstance(data, dict) and data.get("pid") == skip_pid:
        return None
    review_id = data.get("review_id") if isinstance(data, dict) else None
    point = data.get("point") if isinstance(data, dict) else None
    if not isinstance(review_id, str) or not review_id.strip() or type(point) is not int:
        raise ValueError("发送锁缺少 review_id/point")
    return {
        "kind": "lock", "project_id": project_id, "project_root": project_root,
        "review_id": review_id, "point": point, "pack": data.get("pack"),
        "lock": os.path.abspath(_strip_long_prefix(path)),
    }


def _classify(records: list[dict[str, Any]], canonical: str) -> None:
    canonical_real = norm_path(canonical)
    receipts = [record for record in records if record.get("kind") == "receipt"]
    canonical_keys = {
        _receipt_key(record) for record in receipts if _receipt_dir(record) == canonical_real
    }
    receipt_objects = {_object_key(record) for record in receipts}
    for record in records:
        if record.get("kind") == "receipt":
            if _receipt_dir(record) == canonical_real:
                record["classification"] = "canonical_receipt"
            elif _receipt_key(record) in canonical_keys:
                record["classification"] = "duplicate_copy"
            else:
                record["classification"] = "historical_receipt"
        elif record.get("kind") == "lock":
            record["classification"] = (
                "orphan_lock_with_receipt" if _object_key(record) in receipt_objects else "orphan_lock_unknown"
            )
        elif record.get("kind") == "attempt":
            record["classification"] = "historical_attempt"
        elif record.get("kind") == "unreadable":
            record["classification"] = "unreadable"


def scan_project(project_root: str, project_id: str, canonical: str,
                 skip_pid: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    unreadable: list[dict[str, str]] = []
    link_exclusions: list[dict[str, str]] = []
    root_norm = norm_path(project_root)

    def onerror(exc: OSError) -> None:
        path = os.path.abspath(_strip_long_prefix(getattr(exc, "filename", "") or ""))
        failure = {"path": path, "error": "%s: %s" % (type(exc).__name__, exc)}
        if _review_related_failure(path):
            failed.append(failure)
        else:
            skipped.append(failure)

    for dirpath, dirnames, filenames in os.walk(_long_path(project_root), onerror=onerror):
        descend = []
        for name in sorted(dirnames):
            child = os.path.join(dirpath, name)
            display = os.path.abspath(_strip_long_prefix(child))
            try:
                info = os.lstat(child)
                if os.path.islink(child) or getattr(info, "st_file_attributes", 0) & 0x400:
                    target = os.path.realpath(_strip_long_prefix(child))
                    target_norm = norm_path(target)
                    if not _lexists_long(target):
                        link_exclusions.append({
                            "path": display, "target": _strip_long_prefix(target),
                            "error": "broken_link"})
                    elif target_norm != root_norm and not target_norm.startswith(root_norm + os.sep):
                        parts = [part.casefold() for part in os.path.normpath(target).split(os.sep)]
                        if "reviews" in parts:
                            failed.append({
                                "path": display,
                                "error": "external_link_to_reviews: %s" % _strip_long_prefix(target)})
                        else:
                            link_exclusions.append({
                                "path": display, "target": _strip_long_prefix(target),
                                "error": "external_link"})
                    else:
                        link_exclusions.append({
                            "path": display, "target": _strip_long_prefix(target),
                            "error": "internal_link"})
                else:
                    descend.append(name)
            except OSError as exc:
                onerror(exc)
        dirnames[:] = descend
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            display = os.path.abspath(_strip_long_prefix(path))
            try:
                if name.endswith(".review.json"):
                    records.append(_receipt_record(path, project_id, project_root))
                elif ".attempt-" in name and name.endswith(".json"):
                    records.append(_attempt_record(path, project_id, project_root))
                elif name.startswith(".review-send-") and name.endswith(".lock"):
                    lock = _lock_record(path, project_id, project_root, skip_pid)
                    if lock is not None:
                        records.append(lock)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                message = "%s: %s" % (type(exc).__name__, exc)
                item = {"path": display, "error": message}
                unreadable.append(item)
                records.append({
                    "kind": "unreadable", "project_id": project_id, "project_root": project_root,
                    "path": item["path"], "error": message,
                })
    _classify(records, canonical)
    scope = {"project_id": project_id, "project_root": project_root}
    coverage = coverage_entry(scope, failed, skipped, unreadable, link_exclusions)
    return records, coverage


def coverage_summary(coverage: dict[str, Any]) -> dict[str, Any]:
    skipped = coverage.get("skipped_paths") or []
    failed = coverage.get("failed_paths") or []
    unreadable = coverage.get("unreadable_paths") or []
    links = coverage.get("link_exclusions") or []
    return {
        "complete": coverage.get("complete") is True,
        "review_complete": coverage.get("review_complete") is True,
        "failed_count": len(failed), "skipped_count": len(skipped),
        "unreadable_count": len(unreadable), "link_exclusion_count": len(links),
        "failed_sample": failed[:5], "skipped_sample": skipped[:5],
        "unreadable_sample": unreadable[:5], "link_exclusion_sample": links[:5],
    }


def scan_and_register(project_root: str, project_id: str, canonical: str,
                      home: str | None, skip_pid: int | None = None) -> dict[str, Any]:
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        records, coverage = scan_project(project_root, project_id, canonical, skip_pid)
        existing = read_entries(home)
        resolutions = {row.get("resolves") for row in existing if row.get("kind") == "resolution"}
        # A fresh parse of the same file is required; disappearance is never recovery.
        if coverage["complete"]:
            parsed = {norm_path(row[key]) for row in records
                      for key in ("receipt", "attempt", "lock") if isinstance(row.get(key), str)}
            for old in existing + _coverage_unreadables(existing):
                if (old.get("kind") != "unreadable" or old.get("project_id") != project_id
                        or norm_path(old.get("project_root", "")) != norm_path(project_root)
                        or old.get("entry_id") in resolutions or not isinstance(old.get("path"), str)
                        or norm_path(old["path"]) not in parsed):
                    continue
                target = old["path"]
                if target.endswith(".review.json"):
                    _receipt_record(target, project_id, project_root)
                elif ".attempt-" in os.path.basename(target) and target.endswith(".json"):
                    _attempt_record(target, project_id, project_root)
                elif os.path.basename(target).startswith(".review-send-") and target.endswith(".lock"):
                    _lock_record(target, project_id, project_root)
                else:
                    continue
                records.append(_base("resolution", old, resolves=old["entry_id"],
                                     note="same file parsed in a complete rescan"))
        for record in records:
            if record.get("kind") == "unreadable":
                record["occurrence_id"] = uuid.uuid4().hex
        prior = latest_coverage(existing, project_id, project_root)
        # Coverage is a state transition, not a set: A -> B -> A must retain the last A.
        if prior is None or _fingerprint({k: v for k, v in prior.items() if k != "scan_id"}) != _fingerprint(coverage):
            coverage["scan_id"] = uuid.uuid4().hex
            records.append(coverage)
        added = _append_locked(home, path, records)
    finally:
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass
    counts: dict[str, int] = {}
    for entry in added:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    return {"project_id": project_id, "project_root": project_root,
            "appended": len(added), "kinds": counts, "coverage": coverage_summary(coverage)}


def preflight(home: str | None, scope: dict[str, str], review_id: str, point: int) -> dict[str, Any]:
    entries = read_entries(home)
    summary = summarize(entries, scope["project_id"], review_id, point, scope["canonical"])
    if not summary["coverage_review_complete"]:
        scan_and_register(scope["project_root"], scope["project_id"], scope["canonical"], home,
                          skip_pid=os.getpid())
        entries = read_entries(home)
        summary = summarize(entries, scope["project_id"], review_id, point, scope["canonical"])
    return summary


def find_project_roots(root: str) -> list[str]:
    root = norm_path(root)
    if is_project_dir(root):
        return [root]
    found: list[str] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in PRUNE_DIRS)
        if is_project_dir(dirpath):
            found.append(dirpath)
            dirnames[:] = []
    return found


def backfill(root: str, home: str | None) -> dict[str, Any]:
    projects = find_project_roots(root)
    if not projects:
        raise ValueError("未找到项目目录；请给出 project-<编号>-* 项目或包含项目的上级目录")
    results = []
    for project in projects:
        scope = {"project_root": project, "canonical": canonical_reviews(project),
                 "project_id": project_identity(project)}
        results.append(scan_and_register(project, scope["project_id"], scope["canonical"], home))
    return {"root": norm_path(root), "ledger": ledger_path(home), "projects": results}


def resolve_intent(home: str | None, entry_id: str, note: str,
                   receipt_path: str | None = None) -> dict[str, Any]:
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        entries = read_entries(home)
        target = next((entry for entry in entries if entry.get("entry_id") == entry_id), None)
        if target is None or target.get("kind") != "send_intent":
            raise ValueError("未找到发送意图")
        extra = {}
        if receipt_path:
            data = _read_json(receipt_path)
            pack = data.get("pack") if isinstance(data, dict) else None
            report = data.get("report") if isinstance(data, dict) else None
            round_number = data.get("round") if isinstance(data, dict) else None
            if (not isinstance(data, dict) or data.get("schema") != "research-session/review/v1"
                    or data.get("intent_id") is not None or "report" not in data
                    or type(round_number) is not int or round_number not in (0, 1, 2)
                    or (report is not None and (not isinstance(report, dict) or round_number == 0))
                    or not isinstance(pack, dict) or not isinstance(pack.get("path"), str)
                    or type(pack.get("before_bytes")) is not int or pack["before_bytes"] <= 0
                    or not isinstance(pack.get("after_sha256"), str)):
                raise ValueError("历史回执结构无效或已绑定其他发送意图")
            with open(pack["path"], "rb") as pack_file:
                content = pack_file.read()
            if (hashlib.sha256(content).hexdigest() != pack["after_sha256"]
                    or hashlib.sha256(content[:pack["before_bytes"]]).hexdigest() != target.get("pack_sha256")):
                raise ValueError("历史回执绑定的包内容或摘要已改变")
            actual = _receipt_record(receipt_path, target["project_id"], target["project_root"])
            same = (actual.get("review_id") == target.get("review_id")
                    and actual.get("point") == target.get("point")
                    and actual.get("pack_sha256") == target.get("pack_sha256")
                    and actual.get("cycle", 1) == target.get("cycle", 1))
            recorded = any(row.get("kind") == "receipt" and row.get("project_id") == target["project_id"]
                           and row.get("receipt_sha256") == actual["receipt_sha256"] for row in entries)
            if not same or not recorded:
                raise ValueError("回执未登记或与发送意图不匹配")
            extra = {"receipt": os.path.abspath(receipt_path), "receipt_sha256": actual["receipt_sha256"]}
        resolution = _base("resolution", target, target.get("review_id"), target.get("point"),
                           resolves=entry_id, note=note, **extra)
        _append_locked(home, path, [resolution])
    finally:
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass
    return {"resolves": entry_id, "note": note, "ledger": path, **extra}
