"""Safe, auditable governance for research logs.

The CLI accepts only fixed actions; it never deletes, overwrites protected
artifacts, or force-kills a running experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from encoding_utils import clean_bytes, detect_encoding
from summary_extractors import summarize_file

VERSION = "0.1.0"
TEXT_EXTS = {".log", ".stderr", ".txt", ".out"}
# These characters are strong indicators that a Chinese path was decoded with
# the wrong Windows code page (for example, "日志" becoming "鏃ュ織").
MOJIBAKE_PATH_TOKENS = ("鏃", "織", "鍏", "璇", "浼", "鎴", "鐢", "闂")
# These are evidence/model/config artifacts, not disposable logs. A profile may
# add protections but cannot weaken this floor.
ALWAYS_PROTECTED_EXTS = {".pt", ".pth", ".ckpt", ".onnx", ".json", ".yaml", ".yml", ".npy", ".npz"}


class ActivityError(RuntimeError):
    pass


def load_profile(path: Path | str) -> dict:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    root = Path(data.get("project_root") or p.resolve().parents[1])
    if not root.is_absolute():
        root = (p.resolve().parent / root).resolve()
    data["project_root"] = str(root)
    return data


def find_activity(profile: dict) -> list[dict]:
    needles = profile.get("activity_process_matches", [])
    if not needles:
        raise ActivityError("activity_process_matches is required for safe process checks")
    rows = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if any(n.lower() in cmd.lower() for n in needles):
                rows.append({"pid": proc.info["pid"], "name": proc.info["name"],
                             "cmdline": cmd, "create_time": proc.info.get("create_time")})
    except Exception:
        try:
            raw = subprocess.check_output(["wmic", "process", "get", "ProcessId,CommandLine"],
                                          text=True, errors="replace")
            for line in raw.splitlines():
                if any(n.lower() in line.lower() for n in needles):
                    rows.append({"cmdline": line})
        except Exception as exc:
            raise ActivityError("Process inspection unavailable; refusing to infer inactivity") from exc
    return rows


def _root(profile):
    return Path(profile["project_root"]).resolve()


def _under_root(profile, rel):
    root = _root(profile)
    path = (root / rel).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"路径越出 project_root: {rel}")
    return path


def find_suspicious_paths(profile: dict) -> list[dict]:
    """Report likely mojibake path components without changing anything."""
    root = _root(profile)
    rows = []
    for path in root.rglob("*"):
        if any(token in part for part in path.relative_to(root).parts
               for token in MOJIBAKE_PATH_TOKENS):
            rows.append({"path": str(path), "kind": "directory" if path.is_dir() else "file"})
    return rows


def _assert_safe_path(path: Path) -> None:
    if any(token in part for part in path.parts for token in MOJIBAKE_PATH_TOKENS):
        raise ValueError(f"拒绝使用疑似乱码路径: {path}")


def _iter_files(profile):
    root = _root(profile)
    dirs = profile.get("current_log_dirs", []) + profile.get("historical_log_dirs", [])
    for rel in dirs:
        d = _under_root(profile, rel)
        if d.exists():
            yield from (p for p in d.rglob("*") if p.is_file())
    for p in root.glob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_EXTS:
            yield p


def audit_logs(profile: dict) -> dict:
    files = []
    for path in _iter_files(profile):
        raw = path.read_bytes()
        info = detect_encoding(raw)
        try:
            text = raw.decode(info["encoding"], errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        files.append({"path": str(path), "size": len(raw),
                      "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                      "encoding": info["encoding"], "confidence": info["confidence"],
                      "mojibake_score": info["mojibake_score"],
                      "summary": summarize_file(path),
                      "active": False})
    return {"version": VERSION, "generated_at": datetime.now().isoformat(),
            "active_processes": find_activity(profile), "suspicious_paths": find_suspicious_paths(profile),
            "files": files}


def _write_json_md(path: Path, data: dict, title: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = [f"# {title}", "", f"生成时间：{datetime.now().isoformat()}", ""]
    if "files" in data:
        md.append(f"文件数：{len(data['files'])}")
        for f in data["files"]:
            md.append(f"- `{f['path']}` · {f.get('encoding')} · mojibake={f.get('mojibake_score')}")
    path.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def rewrite_logs(profile: dict) -> dict:
    activity = find_activity(profile)
    if activity:
        raise ActivityError(f"活动进程存在，拒绝 rewrite: {activity}")
    root = _root(profile)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    backup_root = _under_root(profile, profile.get("archive_root", "99-归档")) / "04-日志原始备份" / stamp
    manifest = []
    count = 0
    # Only current activity logs may be rewritten. Historical/archive originals
    # remain immutable; they are audited and summarized separately.
    current_dirs = [_under_root(profile, rel) for rel in profile.get("current_log_dirs", [])]
    paths = (p for d in current_dirs if d.exists() for p in d.rglob("*") if p.is_file())
    for path in paths:
        if path.suffix.lower() not in TEXT_EXTS:
            continue
        _under_root(profile, str(path.relative_to(root)))
        if any(path.match(g) for g in profile.get("protected_globs", [])):
            continue
        raw = path.read_bytes()
        clean = clean_bytes(raw)
        if not clean["changed"] and clean["encoding"] in ("utf-8", "utf-8-sig"):
            continue
        rel = path.relative_to(root)
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, backup)
        digest = hashlib.sha256(raw).hexdigest()
        path.write_text(clean["text"], encoding="utf-8", newline="")
        manifest.append({"source": str(path), "backup": str(backup),
                         "sha256_before": digest, "size": len(raw),
                         "encoding": clean["encoding"], "cleaned_at": datetime.now().isoformat(),
                         "tool_version": VERSION})
        count += 1
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    return {"rewritten": count, "backup_root": str(backup_root), "manifest": manifest}


def archive_logs(profile: dict, apply: bool = False) -> dict:
    activity = find_activity(profile)
    if activity and apply:
        raise ActivityError("活动进程存在，拒绝 archive --apply")
    root = _root(profile)
    # Keep archive destinations stable and UTF-8-safe; never derive names from
    # console output or a system code page.
    target = _under_root(profile, profile.get("archive_root", "99-归档")) / "01-运行记录"
    _assert_safe_path(target)
    protected = set(profile.get("protected_globs", []))
    items = []
    for rel in profile.get("current_log_dirs", []):
        d = _under_root(profile, rel)
        if not d.exists():
            continue
        for src in d.rglob("*"):
            if not src.is_file():
                continue
            if src.suffix.lower() in ALWAYS_PROTECTED_EXTS or any(src.match(glob) for glob in protected):
                continue
            dest = target / src.relative_to(d)
            _under_root(profile, str(src.relative_to(root)))
            _under_root(profile, str(dest.relative_to(root)))
            items.append({"source": str(src), "target": str(dest), "sha256": hashlib.sha256(src.read_bytes()).hexdigest()})
    # Check the complete plan before any moves (not an atomic filesystem transaction).
    if apply:
        targets = [item["target"] for item in items]
        if len(targets) != len(set(targets)):
            raise FileExistsError("Multiple sources map to the same archive target")
        collisions = [item["target"] for item in items if Path(item["target"]).exists()]
        if collisions:
            raise FileExistsError(f"归档目标已存在，未移动任何文件: {collisions}")
        manifest = target.parent / ("archive-manifest-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("x", encoding="utf-8") as f:
            json.dump({"state": "planned", "items": items}, f, ensure_ascii=False, indent=2)
        for item in items:
            src, dest = Path(item["source"]), Path(item["target"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    return {"apply": apply, "items": items, "active_processes": activity,
            "always_protected_extensions": sorted(ALWAYS_PROTECTED_EXTS)}


def summarize_logs(profile: dict) -> dict:
    rows = [summarize_file(p) for p in _iter_files(profile)
            if p.suffix.lower() in TEXT_EXTS | {".json"} and ".summary." not in p.name]
    for row in rows:
        source = Path(row["path"])
        summary_json = source.with_name(source.name + ".summary.json")
        summary_md = source.with_name(source.name + ".summary.md")
        summary_json.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
        summary_md.write_text("# 日志文件结论\n\n" + json.dumps(
            row, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    out = {"generated_at": datetime.now().isoformat(), "files": rows}
    path = _root(profile) / "04-验证" / "log_governance_summary.md"
    path.write_text("# 日志结论索引\n\n" + "\n".join(f"- `{r['path']}`" for r in rows) + "\n", encoding="utf-8")
    return out


def snapshot_code(profile: dict) -> dict:
    root = _root(profile)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    dest = _under_root(profile, profile.get("archive_root", "99-归档")) / "03-代码快照" / stamp
    paths = profile.get("snapshot_paths", [])
    manifest = []
    for rel in paths:
        src = _under_root(profile, rel)
        if not src.exists() or not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        manifest.append({"path": rel, "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                         "modified": datetime.fromtimestamp(src.stat().st_mtime).isoformat()})
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"snapshot": str(dest), "files": manifest}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("audit", "rewrite", "summarize", "archive", "snapshot-code", "status"))
    ap.add_argument("--profile", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    profile = load_profile(args.profile)
    if args.action == "audit":
        result = audit_logs(profile)
        out = _root(profile) / "04-验证" / "log_governance_audit.json"
        _write_json_md(out, result, "日志治理审计")
    elif args.action == "rewrite":
        result = rewrite_logs(profile)
    elif args.action == "summarize":
        result = summarize_logs(profile)
    elif args.action == "archive":
        result = archive_logs(profile, args.apply)
        (_root(profile) / "04-验证" / "log_governance_archive_plan.md").write_text(
            "# 归档计划\n\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.action == "snapshot-code":
        result = snapshot_code(profile)
    else:
        result = {"active_processes": find_activity(profile),
                  "audit": audit_logs(profile)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

