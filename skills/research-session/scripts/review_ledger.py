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
import stat
import sys
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
OWN_SEND_GUARDS: dict[str, str] = {}
OWN_SEND_FILES: dict[str, tuple[str, int]] = {}
LEDGER_LEASES: dict[str, Any] = {}


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


def is_offline_fixture_path(path: str) -> bool:
    for value in (os.path.abspath(_strip_long_prefix(path)), norm_path(path)):
        if "project-999-fixture" in re.split(r"[/\\]", value.casefold()):
            return True
    return False


def verify_ledger_scope(home: str | None, project_root: str) -> None:
    if is_offline_fixture_path(project_root):
        expected = os.path.join(project_root, "runs", "run-001", "fixture-ledger", LEDGER_NAME)
        if home is None or norm_path(ledger_path(home)) != norm_path(expected):
            raise ValueError("offline fixture review requires its dedicated test ledger")
    elif is_offline_fixture_path(ledger_path(home)):
        raise ValueError("ordinary review cannot use an offline fixture ledger")


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
    entry_ids: set[str] = set()
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
            if entry["entry_id"] in entry_ids:
                raise ValueError("复核台账第 %d 行 entry_id 重复" % number)
            entry_ids.add(entry["entry_id"])
            entries.append(entry)
    return entries


def _fingerprint(value: dict[str, Any]) -> str:
    keys = (
        "kind", "project_id", "project_root", "review_id", "point", "pack", "pack_sha256",
        "receipt", "receipt_sha256", "round", "cycle", "decision",
        "intent_id", "attempt", "failure_kind", "lock", "complete", "review_complete",
        "failed_paths", "skipped_paths", "unreadable_paths", "link_exclusions", "resolves", "note", "path", "error", "scan_id", "occurrence_id", "scan_version", "resolution_type", "evidence_source", "evidence_sha256", "attestation", "attestation_sha256",
        "guard_id", "lock_sha256", "lock_mtime_ns", "lock_size", "attempt_sha256",
        "recovery_id", "prepared_id", "archive", "pack_archive",
    )
    semantic = {key: value.get(key) for key in keys if key in value}
    raw = json.dumps(semantic, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _acquire(path: str, timeout: float = LOCK_WAIT_SECONDS) -> str:
    lock = path + ".lock"
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        return _acquire_windows_ledger(lock, deadline)
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


def _acquire_windows_ledger(lock: str, deadline: float) -> str:
    """Hold an exclusive kernel file lease; only a verified legacy owner is reclaimed."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    invalid = ctypes.c_void_p(-1).value

    def owner_exited(lease: Any) -> bool:
        if not _ordinary_unlinked_file(lock):
            raise ValueError("ledger sentinel is not an ordinary unlinked file")
        try:
            owner = json.load(lease)
            started = _pid_started_at(owner["pid"])
            recorded = owner.get("owner_started_at")
            return (started is None or started > _aware_epoch(owner["created_at"]) + 0.001
                    or isinstance(recorded, (int, float)) and started > recorded + 0.001)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("cannot verify legacy ledger owner; preserve sentinel") from exc

    while True:
        handle = kernel.CreateFileW(_long_path(lock), 0xC0010000, 0, None, 1, 0x04000000, None)
        if handle != invalid:
            lease = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY), "w+b")
            try:
                lease.write(json.dumps({"pid": os.getpid(), "created_at": utc_now(),
                                        "owner_started_at": _pid_started_at(os.getpid())}).encode("ascii"))
                lease.flush()
                os.fsync(lease.fileno())
                LEDGER_LEASES[lock] = lease
                return lock
            except BaseException:
                lease.close()
                raise
        error = ctypes.get_last_error()
        if error not in (5, 32, 80, 183):
            raise OSError(error, "cannot acquire ledger lease", lock)
        # The first inspection permits legacy cleanup. Only a dead-owner candidate
        # is opened exclusively, then rechecked against that precise file instance.
        handle = kernel.CreateFileW(_long_path(lock), 0x80000000, 7, None, 3, 0, None)
        dead = False
        if handle != invalid:
            with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb") as prior:
                try:
                    dead = owner_exited(prior)
                except FileNotFoundError:
                    # Released and unlinked between open and inspection: retry the lease.
                    dead = False
        if dead:
            handle = kernel.CreateFileW(_long_path(lock), 0x80010000, 0, None, 3, 0, None)
            if handle != invalid:
                with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb") as prior:
                    try:
                        confirmed = owner_exited(prior)
                    except FileNotFoundError:
                        continue
                    if not confirmed:
                        raise ValueError("legacy ledger owner changed; preserve sentinel")
                    disposition = wintypes.BOOL(True)
                    if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition),
                                                            ctypes.sizeof(disposition)):
                        raise OSError(ctypes.get_last_error(), "cannot retire verified ledger sentinel")
                    continue
        if time.monotonic() >= deadline:
            raise ValueError("ledger lease is active or inaccessible; preserve sentinel: %s" % lock)
        time.sleep(0.05)


def _release_ledger(lock: str) -> None:
    if os.name == "nt":
        lease = LEDGER_LEASES.pop(lock, None)
        if lease is None:
            raise ValueError("ledger lease is not owned by this process")
        lease.close()
    else:
        os.unlink(lock)


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
            _release_ledger(lock)
        except FileNotFoundError:
            pass


def claim_send(home: str | None, scope: dict[str, str], binding: dict[str, Any],
               pack: str, cycle: int, allow_existing_receipt: bool = False,
               previous_path: str | None = None,
               recovered_previous: bool = False, *, unattended_round: int | None = None,
               task_approval: str | None = None, task_timeout: int = 1200,
               task_round: int | None = None) -> dict[str, Any]:
    """Atomically check history and claim the object before any model request."""
    verify_ledger_scope(home, scope["project_root"])
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        _refresh_known_roots_locked(home, path, scope)
        entries = read_entries(home)
        summary = summarize(entries, scope["project_id"], binding["review_id"],
                            binding["point"], scope["canonical"])
        blocked = blocking_reason(summary)
        if blocked is not None:
            return {"claimed": False, "reason": blocked, "summary": summary}
        if task_approval:
            import review_task
            if unattended_round is not None or type(task_round) is not int or task_round < 1:
                raise ValueError("invalid task review round")
            verified = review_task.validate(task_approval, binding, scope, task_timeout, entries)
            if verified != binding.get("task_approval"):
                raise ValueError("task approval changed before claim")
            if previous_path:
                previous_data = _read_json(previous_path)
                if recovered_previous:
                    # The dispatcher validates the effective format-recovered view.
                    # It is still round0 in the immutable original receipt.
                    previous_data = None
                if previous_data is not None:
                    review_task.validate_progress(binding, previous_data, [
                        _read_json(row["receipt"]) for row in summary["canonical_receipts"]
                        if row.get("receipt") and row.get("cycle", 1) == cycle])
        if not allow_existing_receipt and summary["canonical_receipts"]:
            return {
                "claimed": False,
                "reason": ("review_history",
                           "该对象已有审查回执；复用原回执或按 --previous 整改，不得重复首审",
                           {"ledger_entries": summary["canonical_receipts"]}),
                "summary": summary,
            }
        if unattended_round is not None and (type(unattended_round) is not int or
                unattended_round not in (1, 2, 3) or cycle != 1 or recovered_previous or
                allow_existing_receipt != (unattended_round > 1)):
            return {"claimed": False, "reason": ("review_limit", "invalid unattended round transition", {}), "summary": summary}
        if allow_existing_receipt:
            receipts = [row for row in summary["group"] if row.get("kind") == "receipt"
                        and row.get("cycle", 1) == cycle]
            if previous_path is None:
                return {"claimed": False, "reason": ("review_history", "previous receipt required", {}), "summary": summary}
            with open(previous_path, "rb") as previous_file:
                previous_sha = hashlib.sha256(previous_file.read()).hexdigest()
            accepted_previous_round = (0 if recovered_previous else task_round - 1) if task_approval else (
                unattended_round - 1 if unattended_round else (0 if recovered_previous else 1))
            if not any(row.get("receipt_sha256") == previous_sha and row.get("round") == accepted_previous_round for row in receipts):
                return {"claimed": False, "reason": ("review_history", "previous receipt missing or stale", {}), "summary": summary}
            for row in receipts:
                round_number = row.get("round")
                if type(round_number) is not int or round_number > accepted_previous_round and round_number >= 2:
                    return {"claimed": False, "reason": ("review_limit", "review round limit or unknown round", {}), "summary": summary}
                recovered_source = None
                if task_approval and previous_path:
                    recovered_source = (_read_json(previous_path).get("recovered_previous") or {}).get("source_sha256")
                if round_number <= 0 and not (round_number == 0 and (
                        recovered_previous and row.get("receipt_sha256") == previous_sha
                        or recovered_source and row.get("receipt_sha256") == recovered_source)):
                    return {"claimed": False, "reason": ("review_history", "unfinished receipt requires investigation", {}), "summary": summary}
        entry = send_intent(scope, binding, pack, cycle)
        added = _append_locked(home, path, [entry])
        if not added:
            raise ValueError("发送意图未写入台账；不发送")
        return {"claimed": True, "entry": added[0], "summary": summary}
    finally:
        try:
            _release_ledger(lock)
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
                 model=binding.get("model"), intent_id=receipt.get("intent_id"),
                 guard_id=receipt.get("guard_id"), classification="canonical_receipt")


def attempt_entry(scope: dict[str, str], binding: dict[str, Any], path: str,
                  failure_kind: str, cycle: int) -> dict[str, Any]:
    data = _read_json(path)
    return _base("attempt", scope, binding["review_id"], binding["point"], pack_sha256=binding["pack_sha256"],
                 attempt=os.path.abspath(_strip_long_prefix(path)), attempt_sha256=_file_sha256(path),
                 intent_id=data.get("intent_id"), guard_id=data.get("guard_id"),
                 failure_kind=failure_kind, cycle=cycle)


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
    revoked = {row.get("resolves") for row in group if row.get("kind") == "resolution" and row.get("resolution_type") == "revoke_fixture"}
    corrections = [row for row in group if row.get("kind") == "resolution" and row.get("resolution_type") == "false_fixture" and row.get("entry_id") not in revoked]
    invalid_corrections = []
    corrected = set()
    for correction in corrections:
        target = next((row for row in group if row.get("entry_id") == correction.get("resolves") and row.get("kind") == "receipt"), None)
        if target is not None and _fixture_correction_valid(target, correction, canonical_real, entries):
            corrected.add(target["entry_id"])
        else:
            invalid_corrections.append(correction)
    receipts = [entry for entry in group if entry.get("kind") == "receipt" and entry.get("entry_id") not in corrected]
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
    recovered = [row for row in group if row.get("resolution_type") == "send_lock_recovered"]
    valid_recovered = [row for row in recovered if _send_recovery_valid(row, entries)]
    invalid_recovered = [row for row in recovered if row not in valid_recovered]
    resolutions = {entry.get("resolves") for entry in entries if entry.get("kind") == "resolution"
                   and entry.get("resolution_type") not in
                   {"send_lock_prepared", "send_lock_recovery_revoked", "send_lock_recovered"}}
    resolutions.update(row["resolves"] for row in valid_recovered)
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
    for lock in locks:
        if (_owns_send_guard(lock.get("lock", ""), lock.get("guard_id"))
                and lock.get("lock_sha256") == OWN_SEND_FILES[norm_path(lock["lock"])][0]):
            continue
        if _normal_release_valid(lock, group):
            continue
        if lock.get("entry_id") in resolutions and any(
                row["resolves"] == lock.get("entry_id") for row in valid_recovered):
            continue
        if any(_lock_receipt_matches(lock, receipt) for receipt in receipts):
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
        "group": [entry for entry in group if entry.get("entry_id") not in corrected], "canonical_receipts": canonical_receipts,
        "outside_receipts": outside_receipts, "duplicate_copies": duplicate_copies,
        "missing_receipts": missing_receipts,
        "unresolved_intents": unresolved_intents, "unknown_locks": unknown_locks,
        "invalid_fixture_corrections": invalid_corrections,
        "invalid_lock_recoveries": invalid_recovered,
        "attempts": [entry for entry in group if entry.get("kind") == "attempt"],
        "unreadable": project_unreadable,
        "coverage": coverage,
        "coverage_complete": bool(coverage is not None and coverage.get("complete") is True),
        "coverage_review_complete": bool(review_complete),
        "incomplete_roots": incomplete_roots,
    }


def blocking_reason(summary: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    if summary.get("invalid_lock_recoveries"):
        return ("review_history", "发送锁恢复证据已失效或被撤销；核对原恢复记录，不得自动重发",
                {"ledger_entries": summary["invalid_lock_recoveries"]})
    if summary.get("invalid_fixture_corrections"):
        return ("review_history", "测试夹具纠错绑定已失效；重新核查原文件和证据", {"ledger_entries": summary["invalid_fixture_corrections"]})
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


def _receipt_record(path: str, project_id: str, project_root: str) -> dict[str, Any] | None:
    data = _read_json(path)
    if (isinstance(data, dict) and "unattended" in data) != is_offline_fixture_path(project_root):
        return None
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
        "guard_id": data.get("guard_id"),
    }


def _attempt_record(path: str, project_id: str, project_root: str) -> dict[str, Any] | None:
    data = _read_json(path)
    if (isinstance(data, dict) and "unattended" in data) != is_offline_fixture_path(project_root):
        return None
    binding = data.get("binding") if isinstance(data, dict) else None
    review_id = binding.get("review_id") if isinstance(binding, dict) else None
    point = binding.get("point") if isinstance(binding, dict) else None
    if not isinstance(review_id, str) or not review_id.strip() or type(point) is not int:
        raise ValueError("尝试记录缺少 binding.review_id/point")
    return {
        "kind": "attempt", "project_id": project_id, "project_root": project_root,
        "review_id": review_id, "point": point, "pack_sha256": binding.get("pack_sha256"),
        "attempt": os.path.abspath(_strip_long_prefix(path)), "failure_kind": data.get("failure_kind"),
        "attempt_sha256": _file_sha256(path),
        **{key: data[key] for key in ("intent_id", "guard_id") if data.get(key) is not None},
        "cycle": data.get("cycle", 1),
    }


def _lock_record(path: str, project_id: str, project_root: str,
                 skip_pid: int | None = None) -> dict[str, Any] | None:
    data = _read_json(path)
    if (isinstance(data, dict) and "unattended" in data) != is_offline_fixture_path(project_root):
        return None
    if (skip_pid is not None and isinstance(data, dict) and data.get("pid") == skip_pid
            and _owns_send_guard(path, data.get("guard_id"))):
        return None
    review_id = data.get("review_id") if isinstance(data, dict) else None
    point = data.get("point") if isinstance(data, dict) else None
    if not isinstance(review_id, str) or not review_id.strip() or type(point) is not int:
        raise ValueError("发送锁缺少 review_id/point")
    return {
        "kind": "lock", "project_id": project_id, "project_root": project_root,
        "review_id": review_id, "point": point, "pack": data.get("pack"),
        "pack_sha256": data.get("pack_sha256"), "intent_id": data.get("intent_id"),
        "lock": os.path.abspath(_strip_long_prefix(path)),
        "lock_sha256": _file_sha256(path), "lock_mtime_ns": os.stat(path).st_mtime_ns,
        "lock_size": os.stat(path).st_size, "guard_id": data.get("guard_id"),
    }


def _lock_receipt_matches(lock: dict[str, Any], receipt: dict[str, Any]) -> bool:
    if (_object_key(lock) != _object_key(receipt) or not _receipt_exists(receipt)
            or not lock.get("pack") or not receipt.get("pack")
            or norm_path(lock["pack"]) != norm_path(receipt["pack"])
            or lock.get("pack_sha256") is not None and
            lock["pack_sha256"] != receipt.get("pack_sha256")):
        return False
    if lock.get("guard_id"):
        return lock["guard_id"] == receipt.get("guard_id")
    # Old records lacking a request identity remain unknown. Object equality
    # and an old classification cannot establish which request produced a receipt.
    return bool(lock.get("intent_id") and lock["intent_id"] == receipt.get("intent_id"))


def _classify(records: list[dict[str, Any]], canonical: str) -> None:
    canonical_real = norm_path(canonical)
    receipts = [record for record in records if record.get("kind") == "receipt"]
    canonical_keys = {
        _receipt_key(record) for record in receipts if _receipt_dir(record) == canonical_real
    }
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
                "orphan_lock_with_receipt" if any(_lock_receipt_matches(record, receipt)
                                                 for receipt in receipts) else "orphan_lock_unknown"
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
            if name.casefold() == "project-999-fixture":
                continue
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
                    entry = _receipt_record(path, project_id, project_root)
                elif ".attempt-" in name and name.endswith(".json"):
                    entry = _attempt_record(path, project_id, project_root)
                elif name.startswith(".review-send-") and name.endswith(".lock"):
                    entry = _lock_record(path, project_id, project_root, skip_pid)
                else:
                    continue
                if entry is not None:
                    records.append(entry)
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
    verify_ledger_scope(home, project_root)
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        return _scan_and_register_locked(project_root, project_id, canonical, home, path, skip_pid)
    finally:
        try:
            _release_ledger(lock)
        except FileNotFoundError:
            pass

def _scan_and_register_locked(project_root: str, project_id: str, canonical: str,
                              home: str | None, path: str, skip_pid: int | None) -> dict[str, Any]:
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
    counts: dict[str, int] = {}
    for entry in added:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    return {"project_id": project_id, "project_root": project_root,
            "appended": len(added), "kinds": counts, "coverage": coverage_summary(coverage)}


def preflight(home: str | None, scope: dict[str, str], review_id: str, point: int) -> dict[str, Any]:
    verify_ledger_scope(home, scope["project_root"])
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        _refresh_known_roots_locked(home, path, scope)
        entries = read_entries(home)
        return summarize(entries, scope["project_id"], review_id, point, scope["canonical"])
    finally:
        try:
            _release_ledger(lock)
        except FileNotFoundError:
            pass


def _refresh_known_roots_locked(home: str | None, path: str, scope: dict[str, str]) -> None:
    roots: dict[str, str] = {}
    if not is_offline_fixture_path(scope["project_root"]):
        for row in read_entries(home):
            if row.get("project_id") == scope["project_id"] and not is_offline_fixture_path(row["project_root"]):
                roots.setdefault(norm_path(row["project_root"]), row["project_root"])
    roots.setdefault(norm_path(scope["project_root"]), scope["project_root"])
    for _, root in sorted(roots.items()):
        _scan_and_register_locked(root, scope["project_id"], canonical_reviews(root),
                                  home, path, os.getpid())


def find_project_roots(root: str) -> list[str]:
    if is_offline_fixture_path(root):
        return []
    root = norm_path(root)
    if is_project_dir(root):
        return [root]
    found: list[str] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in PRUNE_DIRS
                             and not is_offline_fixture_path(os.path.join(dirpath, name)))
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
                    or data.get("unattended") is not None
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
            _release_ledger(lock)
        except FileNotFoundError:
            pass
    return {"resolves": entry_id, "note": note, "ledger": path, **extra}


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _ordinary_unlinked_file(path: str) -> bool:
    current = os.path.abspath(path)
    info = os.lstat(current)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return False
    while True:
        info = os.lstat(current)
        if os.path.islink(current) or getattr(info, "st_file_attributes", 0) & 0x400:
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent


def _linux_pid_started_at(pid: int) -> float | None:
    """Start time in epoch seconds from /proc; absence is provable, errors are not."""
    try:
        with open("/proc/%d/stat" % pid, encoding="ascii") as fh:
            text = fh.read()
    except FileNotFoundError:
        if os.path.exists("/proc/self/stat"):
            return None
        raise ValueError("cannot inspect owner process: /proc is unavailable")
    except OSError as exc:
        raise ValueError("cannot inspect owner process: %s" % exc) from exc
    # Field 2 (comm) may contain spaces and parentheses, so split after the last ')'.
    fields = text.rpartition(")")[2].split()
    if len(fields) < 20:
        raise ValueError("cannot read owner process creation time")
    try:
        ticks = int(fields[19])
        with open("/proc/stat", encoding="ascii") as fh:
            boot = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (StopIteration, OSError, ValueError) as exc:
        raise ValueError("cannot read owner process creation time") from exc


def _pid_started_at(pid: int) -> float | None:
    """None proves absence; access failures never mean that the owner exited."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid owner PID")
    if os.name != "nt":
        if sys.platform.startswith("linux"):
            return _linux_pid_started_at(pid)
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OverflowError):
            return None
        raise ValueError("live owner; creation-time verification unavailable on this platform")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: the PID no longer exists.
            return None
        raise ValueError("cannot inspect owner process: %s" % error)
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, created, exited, kernel_time, user_time):
            raise ValueError("cannot inspect owner creation time")
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10000000 - 11644473600
    finally:
        kernel.CloseHandle(handle)


def create_send_guard(home: str | None, scope: dict[str, str], lock_path: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
    """Guard creation and recovery share the ledger mutex, including the unlink window."""
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mutex = _acquire(path)
    try:
        verify_ledger_scope(home, scope["project_root"])
        value = dict(payload, guard_id=uuid.uuid4().hex, created_at=utc_now(),
                     project_id=scope["project_id"], project_root=scope["project_root"],
                     owner_started_at=_pid_started_at(os.getpid()),
                     pack_sha256=_file_sha256(payload["pack"]),
                     implementation_sha256={
                         role: _file_sha256(os.path.join(os.path.dirname(__file__), name))
                         for role, name in (("dispatcher", "audit_dispatch.py"), ("ledger", "review_ledger.py"))})
        with open(lock_path, "x", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        OWN_SEND_GUARDS[norm_path(lock_path)] = value["guard_id"]
        OWN_SEND_FILES[norm_path(lock_path)] = (_file_sha256(lock_path), os.stat(lock_path).st_mtime_ns)
        return value
    finally:
        _release_ledger(mutex)


def _owns_send_guard(lock_path: str, guard_id: str | None) -> bool:
    try:
        key = norm_path(lock_path)
        return (bool(guard_id) and OWN_SEND_GUARDS.get(key) == guard_id and
                OWN_SEND_FILES.get(key) == (_file_sha256(lock_path), os.stat(lock_path).st_mtime_ns))
    except OSError:
        return False


def release_send_guard(home: str | None, lock_path: str, guard_id: str) -> None:
    mutex = _acquire(ledger_path(home))
    try:
        data = _read_json(lock_path)
        if (data.get("guard_id") != guard_id or data.get("pid") != os.getpid()
                or not _owns_send_guard(lock_path, guard_id)):
            raise ValueError("send guard generation changed; refusing cleanup")
        lock_sha = _file_sha256(lock_path)
        registered = [row for row in read_entries(home) if row["kind"] == "lock"
                      and row.get("guard_id") == guard_id and row.get("lock_sha256") == lock_sha
                      and norm_path(row["lock"]) == norm_path(lock_path)]
        archive = os.path.join(os.path.dirname(lock_path), ".released-send-" + lock_sha + ".json")
        if registered:
            _publish_recovery_snapshot(lock_path, archive, lock_sha)
            _append_locked(home, ledger_path(home), [
                _base("resolution", row, row["review_id"], row["point"],
                      resolution_type="send_guard_release_prepared", resolves=row["entry_id"],
                      guard_id=guard_id, lock_sha256=lock_sha,
                      lock_mtime_ns=row["lock_mtime_ns"], lock_size=row["lock_size"], archive=archive)
                for row in registered])
        os.unlink(lock_path)
        _append_locked(home, ledger_path(home), [
            _base("resolution", row, row["review_id"], row["point"],
                  resolution_type="send_guard_released", resolves=row["entry_id"],
                  guard_id=guard_id, lock_sha256=lock_sha) for row in registered])
        OWN_SEND_GUARDS.pop(norm_path(lock_path), None)
        OWN_SEND_FILES.pop(norm_path(lock_path), None)
    finally:
        _release_ledger(mutex)


def _normal_release_valid(lock: dict[str, Any], group: list[dict[str, Any]]) -> bool:
    try:
        rows = [row for row in group if row.get("resolves") == lock["entry_id"] and lock.get("guard_id")
                and row.get("guard_id") == lock["guard_id"]
                and row.get("lock_sha256") == lock.get("lock_sha256")]
        prepared = [row for row in rows if row.get("resolution_type") == "send_guard_release_prepared"]
        if not prepared:
            return any(row.get("resolution_type") == "send_guard_released" for row in rows)
        valid = any(row["entry_id"] == _fingerprint(row)
                    and _object_key(row) == _object_key(lock)
                    and row.get("lock_mtime_ns") == lock.get("lock_mtime_ns")
                    and row.get("lock_size") == lock.get("lock_size")
                    and _bound_file(row["archive"])["sha256"] == lock["lock_sha256"]
                    for row in prepared)
        if not valid:
            return False
        path = lock["lock"]
        return (not os.path.lexists(path)
                or _file_sha256(path) != lock["lock_sha256"]
                or os.stat(path).st_mtime_ns != lock["lock_mtime_ns"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _aware_epoch(value: str) -> float:
    moment = datetime.datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value))
    if moment.tzinfo is None:
        raise ValueError("recovery timestamps require a timezone")
    return moment.timestamp()


def _bound_file(path: str) -> dict[str, str]:
    if not os.path.isabs(path) or not _ordinary_unlinked_file(path):
        raise ValueError("recovery evidence must be an ordinary, unlinked absolute file: %s" % path)
    return {"path": path, "sha256": _file_sha256(path)}


def _recovery_snapshot(home: str | None, entry_id: str, *, archived_lock: str | None = None,
                       expected_lock: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = read_entries(home)
    target = next((row for row in entries if row["entry_id"] == entry_id and row["kind"] == "lock"), None)
    if target is None:
        raise ValueError("registered send lock not found")
    root = target["project_root"]
    verify_ledger_scope(home, root)
    if is_offline_fixture_path(root):
        raise ValueError("ordinary send recovery cannot operate on unattended fixtures")
    pack, lock_path = target["pack"], target["lock"]
    key = hashlib.sha256(json.dumps([target["review_id"], target["point"]],
                                   ensure_ascii=False).encode("utf-8")).hexdigest()
    canonical = canonical_reviews(root)
    if (project_identity(root) != target["project_id"]
            or norm_path(project_root_for_path(pack) or "") != norm_path(root)
            or norm_path(os.path.dirname(pack)) != canonical
            or norm_path(lock_path) != norm_path(os.path.join(canonical, ".review-send-" + key + ".lock"))):
        raise ValueError("lock is outside its authoritative project/object")
    source = archived_lock or lock_path
    snapshot = _bound_file(source)
    info, data = os.stat(source), _read_json(source)
    mtime_ns = expected_lock["mtime_ns"] if archived_lock and expected_lock else info.st_mtime_ns
    snapshot["path"] = lock_path
    if (not isinstance(data, dict) or data.get("review_id") != target["review_id"]
            or type(data.get("point")) is not int or data["point"] != target["point"]
            or norm_path(data.get("pack", "")) != norm_path(pack) or data.get("unattended") is not None):
        raise ValueError("lock payload does not match its registered object")
    snapshot.update(mtime_ns=mtime_ns, size=info.st_size, guard_id=data.get("guard_id"),
                    implementation_sha256=data.get("implementation_sha256"))
    if expected_lock is not None and snapshot != expected_lock:
        raise ValueError("archived lock no longer matches the prepared snapshot")
    for key_name, actual in (("lock_sha256", snapshot["sha256"]), ("lock_mtime_ns", mtime_ns)):
        if target.get(key_name) is not None and target[key_name] != actual:
            raise ValueError("registered lock generation has changed")
    observed = []
    roots = {row["project_root"] for row in entries if row["project_id"] == target["project_id"]}
    for known_root in sorted(roots):
        records, coverage = scan_project(known_root, target["project_id"], canonical_reviews(known_root))
        if coverage["complete"] is not True or any(row["kind"] == "unreadable" for row in records):
            raise ValueError("recovery requires complete readable history at every registered root")
        observed.extend(records)
    combined = {row["entry_id"]: row for row in entries}
    for row in observed:
        row = dict(row, entry_id=_fingerprint(row))
        combined.setdefault(row["entry_id"], row)
    group = history_for(list(combined.values()), target["project_id"], target["review_id"], target["point"])
    locks = [row for row in group if row["kind"] == "lock" and
             norm_path(row.get("lock", "")) == norm_path(lock_path) and
             (row.get("lock_sha256") is None or row["lock_sha256"] == snapshot["sha256"]) and
             (row.get("lock_mtime_ns") is None or row["lock_mtime_ns"] == mtime_ns)]
    pack_snapshot_sha = _bound_file(pack)["sha256"]
    intents = [row for row in group if row["kind"] == "send_intent"
               and norm_path(row.get("pack", "")) == norm_path(pack)
               and (not data.get("pack_sha256") or row.get("pack_sha256") == data["pack_sha256"])
               and _aware_epoch(row["created_at"]) >= mtime_ns / 1e9 - 1]
    if len(intents) > 1 or not intents and not data.get("guard_id"):
        raise ValueError("send intent is absent or ambiguous for this lock generation")
    intent = intents[0] if intents else {
        "entry_id": None, "intent_id": None, "created_at": data["created_at"],
        "pack_sha256": data["pack_sha256"], "cycle": 1}
    pack_sha = intent["pack_sha256"]
    if any(row["kind"] == "send_intent" and _aware_epoch(row["created_at"]) >
           _aware_epoch(intent["created_at"]) for row in group):
        raise ValueError("a later send intent requires separate investigation")
    attempts = []
    for row in group:
        if row["kind"] != "attempt" or row.get("pack_sha256") != pack_sha:
            continue
        proof = _bound_file(row["attempt"])
        attempt = _read_json(row["attempt"])
        if _aware_epoch(attempt["created_at"]) < _aware_epoch(intent["created_at"]):
            continue
        binding = attempt.get("binding") or {}
        if (attempt.get("schema") != "research-session/review-attempt/v1"
                or any(attempt.get(k) is not False for k in
                       ("ok", "review_counted", "review_completed", "gate_passed"))
                or attempt.get("unattended") is not None
                or binding.get("review_id") != target["review_id"]
                or type(binding.get("point")) is not int or binding["point"] != target["point"]
                or binding.get("pack_sha256") != pack_sha
                or attempt.get("cycle", 1) != intent.get("cycle", 1)
                or attempt.get("intent_id", intent["intent_id"]) != intent["intent_id"]
                or attempt.get("guard_id", data.get("guard_id")) != data.get("guard_id")
                or row.get("attempt_sha256", proof["sha256"]) != proof["sha256"]):
            raise ValueError("failed attempt is not bound to this intent/lock")
        proof.update(entry_id=row["entry_id"], failure_kind=attempt.get("failure_kind"),
                     created_at=attempt["created_at"])
        if proof["path"] not in [item["path"] for item in attempts]:
            attempts.append(proof)
    # A receipt is reconciled as a distinct terminal state, never erased or recounted.
    receipts = []
    for row in group:
        if row["kind"] != "receipt":
            continue
        if row.get("intent_id") == intent["intent_id"] or (
                row.get("pack_sha256") == pack_sha and row.get("cycle", 1) == intent.get("cycle", 1)
                and not row.get("intent_id")):
            from audit_dispatch import load_checklist, load_receipt, REFERENCE_MD
            _, checklist = load_checklist(target["point"], REFERENCE_MD)
            receipt = load_receipt(row["receipt"], len(checklist))
            if (receipt.get("intent_id") != intent["intent_id"]
                    or receipt.get("cycle", 1) != intent.get("cycle", 1)
                    or receipt.get("binding", {}).get("pack_sha256") != pack_sha
                    or norm_path(receipt["pack"]["path"]) != norm_path(pack)
                    or data.get("guard_id") and receipt.get("guard_id") != data["guard_id"]):
                raise ValueError("receipt is not exactly bound to the send being reconciled")
            proof = _bound_file(row["receipt"])
            if proof["sha256"] != row["receipt_sha256"]:
                raise ValueError("registered receipt changed")
            if proof not in receipts:
                receipts.append(proof)
    if not receipts and pack_snapshot_sha != pack_sha:
        raise ValueError("failed-send package changed")
    for row in observed:
        if (row["kind"] == "lock" and _object_key(row) == _object_key(target)
                and norm_path(row["lock"]) != norm_path(lock_path)):
            raise ValueError("another observed lock exists for this object")
    started = _pid_started_at(data.get("pid"))
    latest = max([_aware_epoch(intent["created_at"]),
                  *[_aware_epoch(item["created_at"]) for item in attempts]])
    if started is None:
        owner_state = "exited"
    elif started > max(mtime_ns / 1e9, latest):
        owner_state = "pid_reused"
    else:
        raise ValueError("the original owner is still alive or its identity is uncertain")
    bound = {key_name: target[key_name] for key_name in
             ("project_id", "project_root", "review_id", "point")}
    bound.update(lock_entry_ids=sorted(row["entry_id"] for row in locks),
                 intent_entry_id=intent["entry_id"], intent_id=intent["intent_id"],
                 intent_created_at=intent["created_at"],
                 cycle=intent.get("cycle", 1), pack=pack, pack_sha256=pack_sha,
                 pack_snapshot_sha256=pack_snapshot_sha,
                 lock=snapshot, attempts=sorted(attempts, key=lambda item: item["path"]),
                 receipts=sorted(receipts, key=lambda item: item["path"]))
    return dict(schema="research-session/send-lock-recovery/v1", target=bound,
                owner_state=owner_state, decision=None, outcome=None, operator="", reviewer="",
                reviewed_at=None, basis="", limitations="", review_record=None,
                upstream_confirmation=None), observed


def send_lock_recovery_template(home: str | None, entry_id: str) -> dict[str, Any]:
    mutex = _acquire(ledger_path(home))
    try:
        return _recovery_snapshot(home, entry_id)[0]
    finally:
        _release_ledger(mutex)


def _read_recovery_evidence(path: str, sha256: str) -> dict[str, Any]:
    if _bound_file(path)["sha256"] != sha256:
        raise ValueError("recovery evidence changed")
    value = _read_json(path)
    if (value.get("schema") != "research-session/send-lock-recovery/v1"
            or (value.get("decision"), value.get("outcome")) not in {
                ("recover_failed_send", "not_accepted"),
                ("recover_failed_send", "terminated_without_valid_review"),
                ("reconcile_receipted_send", "receipt_preserved")}
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   for key in ("operator", "reviewer", "basis", "limitations"))
            or value["operator"].strip().casefold() == value["reviewer"].strip().casefold()):
        raise ValueError("independent per-send recovery assessment is incomplete")
    _aware_epoch(value["reviewed_at"])
    record = value.get("review_record")
    if (not isinstance(record, dict) or norm_path(record.get("path", "")) == norm_path(path)
            or _bound_file(record["path"]) != record):
        raise ValueError("independent review original is absent or changed")
    target = value["target"]
    if bool(target["receipts"]) != (value["decision"] == "reconcile_receipted_send"):
        raise ValueError("receipt state requires its distinct reconciliation decision")
    for receipt in target["receipts"]:
        if _bound_file(receipt["path"]) != receipt:
            raise ValueError("reconciled receipt is absent or changed")
    latest = _aware_epoch(target["intent_created_at"])
    uncertain = []
    if not target["attempts"] and not target["receipts"]:
        uncertain.append((None, latest, None))
    for item in target["attempts"]:
        if _bound_file(item["path"])["sha256"] != item["sha256"]:
            raise ValueError("bound failed attempt changed")
        attempt = _read_json(item["path"])
        latest = max(latest, _aware_epoch(item["created_at"]))
        # A client/gateway rejection is terminal for this call. Other failures require
        # separate request-specific upstream evidence, not inference from a dead PID.
        rejected = (attempt.get("failure_kind") == "http_error"
                    and type(attempt.get("status")) is int and 400 <= attempt["status"] < 500
                    and attempt["status"] not in (408, 409, 499)
                    and isinstance(attempt.get("upstream"), dict)
                    and bool(attempt["upstream"].get("code")))
        if not rejected and not target["receipts"]:
            uncertain.append(((attempt.get("upstream") or {}).get("response_id"),
                              _aware_epoch(item["created_at"]), item["sha256"]))
    confirmations = value.get("upstream_confirmations") or (
        [value["upstream_confirmation"]] if value.get("upstream_confirmation") else [])
    if not isinstance(confirmations, list):
        raise ValueError("terminal confirmations must be a list")
    for request_id, request_time, attempt_sha in uncertain:
        candidates = [item for item in confirmations if isinstance(item, dict)
                      and (not request_id or item.get("request_identity") == request_id)
                      and (len(uncertain) <= 1 or item.get("attempt_sha256") == attempt_sha)]
        if len(candidates) != 1:
            raise ValueError("each uncertain request requires one matching terminal confirmation identity")
        confirmation = candidates[0]
        local_not_sent = (
            confirmation.get("source_kind") == "local_pre_request"
            and confirmation.get("terminal_state") == "not_sent"
            and not target["intent_id"] and not target["attempts"] and not target["receipts"]
            and bool(target["lock"]["guard_id"]))
        if local_not_sent:
            implementation = confirmation.get("implementation")
            expected = target["lock"].get("implementation_sha256")
            if (not isinstance(implementation, list) or len(implementation) != 2
                    or not isinstance(expected, dict) or set(expected) != {"dispatcher", "ledger"}
                    or {item.get("role") for item in implementation} != set(expected)
                    or any(_bound_file(item["path"])["sha256"] != item["sha256"]
                           or item["sha256"] != expected[item["role"]] for item in implementation)):
                raise ValueError("local not-sent proof requires the exact pre-request implementation snapshots")
        excluded = {path, record["path"], target["pack"], target["lock"]["path"],
                    *[item["path"] for item in target["attempts"]]}
        if (not isinstance(confirmation, dict)
                or not local_not_sent and confirmation.get("terminal_state") not in {"rejected", "failed", "cancelled"}
                or confirmation.get("intent_id") != target["intent_id"]
                or not target["intent_id"] and confirmation.get("guard_id") != target["lock"]["guard_id"]
                or confirmation.get("no_valid_review") is not True
                or not isinstance(confirmation.get("request_identity"), str)
                or not confirmation["request_identity"].strip()
                or not local_not_sent and confirmation.get("source_kind") not in {"gateway", "upstream"}
                or not isinstance(confirmation.get("record"), dict)
                or _bound_file(confirmation["record"]["path"]) != confirmation["record"]
                or confirmation["record"]["path"] in excluded):
            raise ValueError("ambiguous failure requires independent request-specific terminal confirmation")
        if _aware_epoch(confirmation["checked_at"]) < request_time:
            raise ValueError("upstream confirmation predates the failed attempt")
    if _aware_epoch(value["reviewed_at"]) < latest:
        raise ValueError("independent review predates the request evidence")
    return value


def _send_recovery_valid(row: dict[str, Any], entries: list[dict[str, Any]],
                         *, require_complete: bool = True) -> bool:
    try:
        if row["entry_id"] != _fingerprint(row) or any(
                item.get("resolution_type") == "send_lock_recovery_revoked"
                and item.get("resolves") == row["recovery_id"] for item in entries):
            return False
        value = _read_recovery_evidence(row["evidence_source"], row["evidence_sha256"])
        target = value["target"]
        ids = [*target["lock_entry_ids"]]
        if target["intent_entry_id"]:
            ids.append(target["intent_entry_id"])
        original = next(item for item in entries if item["entry_id"] == row["resolves"])
        prepared = next(item for item in entries if item["entry_id"] == row["prepared_id"])
        completed_ids = {item.get("resolves") for item in entries
                         if item.get("prepared_id") == prepared["entry_id"]
                         and item.get("resolution_type") == "send_lock_recovered"}
        return (row["resolves"] in ids
                and (not require_complete or completed_ids == set(ids))
                and all(row.get(key) == original.get(key) == target.get(key)
                        for key in ("project_id", "project_root", "review_id", "point"))
                and prepared.get("resolution_type") == "send_lock_prepared"
                and prepared.get("recovery_id") == row["recovery_id"]
                and prepared.get("evidence_sha256") == row["evidence_sha256"]
                and _bound_file(row["pack_archive"])["sha256"] == target["pack_snapshot_sha256"]
                and _bound_file(row["archive"])["sha256"] == target["lock"]["sha256"])
    except (ValueError, OSError, KeyError, TypeError, AttributeError, StopIteration):
        return False


def recover_send_lock(home: str | None, entry_id: str, evidence: str, evidence_sha256: str,
                      *, dry_run: bool = False) -> dict[str, Any]:
    mutex = _acquire(ledger_path(home))
    try:
        value = _read_recovery_evidence(evidence, evidence_sha256)
        target = value["target"]
        if entry_id not in target["lock_entry_ids"]:
            raise ValueError("recovery assessment targets another lock entry")
        entries = read_entries(home)
        prepared = next((row for row in entries if row.get("resolution_type") == "send_lock_prepared"
                         and row.get("evidence_source") == evidence
                         and row.get("evidence_sha256") == evidence_sha256), None)
        archive = os.path.join(os.path.dirname(target["lock"]["path"]),
                               ".recovered-send-" + target["lock"]["sha256"] + ".json")
        pack_archive = os.path.join(os.path.dirname(target["lock"]["path"]),
                                    ".recovered-pack-" + target["pack_snapshot_sha256"] + ".md")
        result = dict(local_only=True, gate_passed=False, review_completed=False, archive=archive,
                      pack_archive=pack_archive,
                      owner_state=value["owner_state"], applied=not dry_run, already_completed=False)
        if prepared and any(row.get("resolution_type") == "send_lock_recovery_revoked"
                            and row.get("resolves") == prepared["recovery_id"] for row in entries):
            raise ValueError("prepared recovery was revoked")
        if prepared:
            completed = [row for row in entries if row.get("prepared_id") == prepared["entry_id"]
                         and row.get("resolution_type") == "send_lock_recovered"]
            if not all(_send_recovery_valid(row, entries, require_complete=False) for row in completed):
                raise ValueError("completed recovery has invalid or revoked evidence")
            expected = set(target["lock_entry_ids"])
            if target["intent_entry_id"]:
                expected.add(target["intent_entry_id"])
            if {row["resolves"] for row in completed} == expected:
                return dict(result, already_completed=True, applied=False)
        lock_path = target["lock"]["path"]
        archived = os.path.isfile(archive) and _bound_file(archive)["sha256"] == target["lock"]["sha256"]
        if os.path.lexists(lock_path):
            fresh, observed = _recovery_snapshot(home, entry_id)
        elif prepared and archived:
            fresh, observed = _recovery_snapshot(
                home, entry_id, archived_lock=archive, expected_lock=target["lock"])
        else:
            raise ValueError("missing lock without a bound prepared recovery/archive")
        if fresh["target"] != target:
            raise ValueError("recovery snapshot changed; obtain a fresh independent assessment")
        if dry_run:
            return result
        if prepared is None:
            _append_locked(home, ledger_path(home), observed)
            prepared = _append_locked(home, ledger_path(home), [
                _base("resolution", target, target["review_id"], target["point"],
                      resolution_type="send_lock_prepared", recovery_id=uuid.uuid4().hex,
                      evidence_source=evidence, evidence_sha256=evidence_sha256,
                      archive=archive, pack_archive=pack_archive)])[0]
        _publish_recovery_snapshot(target["pack"], pack_archive, target["pack_snapshot_sha256"])
        if not archived:
            _publish_recovery_snapshot(lock_path, archive, target["lock"]["sha256"])
        if os.path.lexists(lock_path):
            if (_bound_file(lock_path)["sha256"] != target["lock"]["sha256"]
                    or os.stat(lock_path).st_mtime_ns != target["lock"]["mtime_ns"]):
                raise ValueError("new send lock found; refusing removal")
            os.unlink(lock_path)
        resolved_ids = [*target["lock_entry_ids"]]
        if target["intent_entry_id"]:
            resolved_ids.append(target["intent_entry_id"])
        resolutions = [_base("resolution", target, target["review_id"], target["point"],
                             resolution_type="send_lock_recovered", resolves=resolved,
                             recovery_id=prepared["recovery_id"], prepared_id=prepared["entry_id"],
                             evidence_source=evidence, evidence_sha256=evidence_sha256,
                             archive=archive, pack_archive=pack_archive)
                       for resolved in resolved_ids]
        _append_locked(home, ledger_path(home), resolutions)
        return result
    finally:
        _release_ledger(mutex)


def _publish_recovery_snapshot(source: str, archive: str, expected_sha256: str) -> None:
    if os.path.lexists(archive):
        if _bound_file(archive)["sha256"] != expected_sha256:
            raise ValueError("existing recovery archive is invalid; preserve it for investigation")
        return
    with open(source, "rb") as fh:
        content = fh.read()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("recovery source changed before archival")
    pending = archive + ".pending-" + uuid.uuid4().hex
    with open(_long_path(pending), "xb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    if os.name == "nt":
        os.rename(_long_path(pending), _long_path(archive))
    else:
        os.link(pending, archive)
        os.unlink(pending)


def revoke_send_recovery(home: str | None, recovery_id: str, note: str) -> dict[str, Any]:
    if not isinstance(note, str) or not note.strip():
        raise ValueError("revocation requires its reason")
    mutex = _acquire(ledger_path(home))
    try:
        target = next((row for row in read_entries(home) if row.get("recovery_id") == recovery_id
                       and row.get("resolution_type") == "send_lock_prepared"), None)
        if target is None:
            raise ValueError("prepared recovery not found")
        _append_locked(home, ledger_path(home), [
            _base("resolution", target, target["review_id"], target["point"],
                  resolution_type="send_lock_recovery_revoked", resolves=recovery_id, note=note)])
        return {"revoked": recovery_id, "gate_passed": False, "local_only": True}
    finally:
        _release_ledger(mutex)


def _native_fixture_valid(target: dict[str, Any], review: dict[str, Any],
                          data: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    """Provenance is independently reviewed; hashes only validate retained bytes."""
    proof = review.get("native_provenance")

    def valid_file(item: object) -> bool:
        return (isinstance(item, dict) and isinstance(item.get("path"), str)
                and os.path.isabs(item["path"]) and _ordinary_unlinked_file(item["path"])
                and _file_sha256(item["path"]) == item.get("sha256"))

    if not valid_file(proof) or proof not in review["evidence"]:
        return False
    manifest = _read_json(proof["path"])
    if not isinstance(manifest, dict) or manifest.get("schema") != "research-session/native-fixture-provenance/v1":
        return False
    producer, verification = manifest.get("producer"), manifest.get("verification")
    if not valid_file(producer) or not valid_file(verification):
        return False
    run = _read_json(verification["path"])
    if (not isinstance(run, dict) or run.get("schema") != "research-session/verification/v1"
            or type(run.get("exit_code")) is not int or run["exit_code"] != 0
            or not isinstance(run.get("inputs"), list) or not isinstance(run.get("artifacts"), list)):
        return False
    producer_input = manifest.get("producer_input_path", producer["path"])
    if not isinstance(producer_input, str) or not os.path.isabs(producer_input):
        return False
    if not any(isinstance(item, dict) and norm_path(item.get("path", "")) == norm_path(producer_input)
               and item.get("sha256") == producer["sha256"]
               and item.get("after_sha256") == producer["sha256"] for item in run["inputs"]):
        return False
    if not any(valid_file(item) and norm_path(item["path"]) == norm_path(run.get("log", ""))
               for item in run["artifacts"]):
        return False
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        return False
    matched = [row for row in rows if isinstance(row, dict) and row.get("entry_id") == target["entry_id"]]
    if len(matched) != 1 or matched[0].get("target") != target:
        return False
    artifacts = matched[0].get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False
    originals = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return False
        original, preserved = artifact.get("original"), artifact.get("preserved")
        if (not valid_file(original) or not valid_file(preserved)
                or original["sha256"] != preserved["sha256"]
                or norm_path(original["path"]) == norm_path(preserved["path"])):
            return False
        key = norm_path(original["path"])
        if key in originals:
            return False
        originals[key] = original["sha256"]
    binding = data.get("binding")
    if (not isinstance(binding, dict) or data.get("schema") != "research-session/review/v1"
            or data.get("intent_id") != "fixture-intent" or data.get("guard_id") != "fixture-guard"
            or binding.get("model") != "fixture" or binding.get("route") != {"wire_model": "fixture"}
            or not isinstance(data.get("pack"), dict)
            or originals.get(norm_path(target["receipt"])) != target["receipt_sha256"]
            or norm_path(data["pack"].get("path", "")) not in originals
            or binding.get("review_id") != target["review_id"]
            or type(binding.get("point")) is not int or binding["point"] != target["point"]):
        return False
    # A local fixture never erases a real request or an unresolved send boundary.
    return not any(row.get("project_id") == target["project_id"]
                   and row.get("kind") in {"send_intent", "attempt", "lock"}
                   and (row.get("intent_id") == data["intent_id"]
                        or row.get("guard_id") == data["guard_id"]
                        or (row.get("review_id") == target["review_id"]
                            and row.get("point") == target["point"]
                            and row.get("pack_sha256") == binding.get("pack_sha256")))
                   for row in entries)


def _fixture_correction_valid(target: dict[str, Any], correction: dict[str, Any], canonical: str,
                              entries: list[dict[str, Any]] | None = None) -> bool:
    try:
        receipt = target["receipt"]
        attestation = correction["attestation"]
        if (("entry_id" in correction and correction["entry_id"] != _fingerprint(correction))
                or os.path.commonpath([norm_path(receipt), canonical]) == canonical
                or not _ordinary_unlinked_file(receipt)
                or not _ordinary_unlinked_file(attestation)
                or correction.get("project_root") != target.get("project_root")
                or correction.get("project_id") != target.get("project_id")
                or correction.get("receipt_sha256") != target.get("receipt_sha256")
                or correction.get("receipt") != receipt
                or _file_sha256(receipt) != target.get("receipt_sha256")
                or _file_sha256(attestation) != correction.get("attestation_sha256")):
            return False
        review = _read_json(attestation)
        keys = ("entry_id", "project_id", "project_root", "review_id", "point", "receipt", "receipt_sha256")
        if (not isinstance(review, dict) or review.get("schema") != "research-session/fixture-attestation/v1"
                or review.get("target") != {key: target[key] for key in keys}
                or type(review["target"].get("point")) is not int
                or review.get("decision") != "fixture"):
            return False
        if any(not isinstance(review.get(k), str) or not review[k].strip()
               for k in ("reviewer", "operator", "reviewed_at", "basis", "limitations")):
            return False
        if review["reviewer"].strip().casefold() == review["operator"].strip().casefold():
            return False
        moment = datetime.datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", review["reviewed_at"]))
        if moment.tzinfo is None:
            return False
        evidence = review.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return False
        record = review.get("review_record")
        if (not isinstance(record, dict) or not isinstance(record.get("path"), str)
                or norm_path(record["path"]) in {norm_path(receipt), norm_path(attestation)}):
            return False
        for item in [record, *evidence]:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                    or not os.path.isabs(item["path"]) or not _ordinary_unlinked_file(item["path"])
                    or _file_sha256(item["path"]) != item.get("sha256")):
                return False
        data = _read_json(receipt)
        if not isinstance(data, dict):
            return False
        if review.get("native_provenance") is not None:
            return entries is not None and _native_fixture_valid(target, review, data, entries)
        binding = data.get("binding")
        return (isinstance(data, dict) and isinstance(binding, dict)
                and data.get("schema") is None and data.get("pack") is None
                and data.get("intent_id") is None and binding.get("model") is None
                and binding.get("pack_sha256") is None
                and binding.get("review_id") == target.get("review_id")
                and binding.get("point") == target.get("point"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
        return False


def resolve_false_receipt(home: str | None, entry_id: str, evidence_source: str,
                          evidence_sha256: str, note: str) -> dict[str, Any]:
    raise ValueError("源码匹配不能证明来源；须提供独立逐条复核记录")


def fixture_attestation_template(home: str | None, entry_id: str) -> dict[str, Any]:
    target = next((e for e in read_entries(home) if e.get("entry_id") == entry_id), None)
    if target is None or target.get("kind") != "receipt":
        raise ValueError("未找到回执")
    keys = ("entry_id", "project_id", "project_root", "review_id", "point", "receipt", "receipt_sha256")
    return {"schema": "research-session/fixture-attestation/v1",
            "target": {key: target[key] for key in keys}, "decision": None,
            "reviewer": "", "operator": "", "reviewed_at": "", "basis": "", "limitations": "",
            "evidence": [], "review_record": {"path": "", "sha256": ""}}


def _prepare_fixture_correction(entries: list[dict[str, Any]], entry_id: str,
                                attestation: str, attestation_sha256: str) -> dict[str, Any]:
    target = next((e for e in entries if e.get("entry_id") == entry_id), None)
    if target is None or target.get("kind") != "receipt":
        raise ValueError("未找到回执")
    correction = _base("resolution", target, target.get("review_id"), target.get("point"),
                       resolution_type="false_fixture", resolves=entry_id,
                       receipt=target.get("receipt"), receipt_sha256=target.get("receipt_sha256"),
                       attestation=os.path.abspath(attestation), attestation_sha256=attestation_sha256)
    canonical = canonical_reviews(target["project_root"])
    if not _fixture_correction_valid(target, correction, canonical, entries):
        raise ValueError("夹具回执或证据绑定无效，停止纠错")
    revoked = {row.get("resolves") for row in entries if row.get("kind") == "resolution"
               and row.get("resolution_type") == "revoke_fixture"}
    active = [row for row in entries if row.get("kind") == "resolution"
              and row.get("resolution_type") == "false_fixture" and row.get("resolves") == entry_id
              and row.get("entry_id") not in revoked]
    if len(active) > 1:
        raise ValueError("存在冲突纠错，停止")
    if active:
        existing = active[0]
        if (not _fixture_correction_valid(target, existing, canonical, entries)
                or existing.get("attestation") != correction["attestation"]
                or existing.get("attestation_sha256") != attestation_sha256):
            raise ValueError("现有纠错失效或证明不同；先显式撤销再复核")
        return existing
    return correction


def resolve_reviewed_fixture(home: str | None, entry_id: str, attestation: str,
                             attestation_sha256: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Bind a separately reviewed attestation; identity and judgment remain external trust."""
    if (not isinstance(attestation_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", attestation_sha256)
            or not isinstance(attestation, str) or not os.path.isabs(attestation)):
        raise ValueError("需要复核记录绝对路径和 SHA256")
    if dry_run:
        return _prepare_fixture_correction(read_entries(home), entry_id, attestation, attestation_sha256)
    path = ledger_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _acquire(path)
    try:
        entries = read_entries(home)
        correction = _prepare_fixture_correction(entries, entry_id, attestation, attestation_sha256)
        if "entry_id" in correction:
            return correction
        correction["occurrence_id"] = uuid.uuid4().hex
        return _append_locked(home, path, [correction])[0]
    finally:
        try:
            _release_ledger(lock)
        except FileNotFoundError:
            pass


def revoke_false_receipt_resolution(home: str | None, entry_id: str, note: str) -> dict[str, Any]:
    if not isinstance(note, str) or not note.strip():
        raise ValueError("撤销须提供核查说明")
    path = ledger_path(home)
    lock = _acquire(path)
    try:
        entries = read_entries(home)
        target = next((x for x in reversed(entries) if x.get("kind") == "resolution" and x.get("resolution_type") == "false_fixture" and x.get("resolves") == entry_id), None)
        if target is None:
            raise ValueError("没有可撤销纠错")
        prior = next((x for x in entries if x.get("kind") == "resolution" and x.get("resolution_type") == "revoke_fixture" and x.get("resolves") == target["entry_id"]), None)
        if prior is not None:
            return prior
        return _append_locked(home, path, [_base("resolution", target, target.get("review_id"), target.get("point"), resolution_type="revoke_fixture", resolves=target["entry_id"], note=note.strip())])[0]
    finally:
        try:
            _release_ledger(lock)
        except FileNotFoundError:
            pass
