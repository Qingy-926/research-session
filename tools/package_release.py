#!/usr/bin/env python3
"""Build a deterministic research-session release archive.

Standard library only; runs on Windows and Ubuntu. The same source tree always
produces byte-identical output: entries are sorted by POSIX relative path, the
timestamp and file mode are fixed, and deflate uses a fixed compression level.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dist"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644
COMPRESS_LEVEL = 9
EXPECTED_SKILLS = 16

INCLUDE_DIRS = (".trae-plugin", ".codex-plugin", "skills", "scripts", "references",
                "contracts", "assets", "docs", ".github", "tests", "tools")
INCLUDE_FILES = ("LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md",
                 "SECURITY.md", "THIRD_PARTY_NOTICES.md", ".gitignore", "pytest.ini")
REQUIRED_PATHS = INCLUDE_DIRS + INCLUDE_FILES

EXCLUDE_DIR_NAMES = {".git", "dist", "__pycache__", ".pytest_cache", ".venv", "venv",
                     "env", ".research-session-runs"}
EXCLUDE_FILE_NAMES = {"provider-config.local.json", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log")
PRIVATE_EVIDENCE_PREFIX = "04-验证"


class PackageError(RuntimeError):
    pass


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_version(root):
    codex = read_json(root / ".codex-plugin" / "plugin.json")["version"]
    trae = read_json(root / ".trae-plugin" / "plugin.json")["version"]
    if codex != trae:
        raise PackageError("manifest versions disagree: codex=%s trae=%s" % (codex, trae))
    return codex


def contract_versions(root):
    versions = {}
    for path in sorted((root / "contracts").glob("*.json")):
        payload = read_json(path)
        versions[path.name] = payload.get("contract_version", "unknown")
    if not versions:
        raise PackageError("no contract files found under contracts/")
    return versions


def exclusion_reason(relative):
    for part in relative.parts[:-1]:
        if part in EXCLUDE_DIR_NAMES:
            return "directory " + part
        if part.startswith(PRIVATE_EVIDENCE_PREFIX):
            return "private evidence " + part
    name = relative.name
    if name in EXCLUDE_FILE_NAMES:
        return "file name " + name
    if name.endswith(EXCLUDE_SUFFIXES):
        return "file suffix"
    if ".bak" in name:
        return "backup file"
    return None


def collect_files(root):
    files = set()
    for name in INCLUDE_FILES:
        path = root / name
        if not path.is_file():
            raise PackageError("missing required file: " + name)
        files.add(PurePosixPath(name))
    for name in INCLUDE_DIRS:
        base = root / name
        if not base.is_dir():
            raise PackageError("missing required directory: " + name)
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if exclusion_reason(relative):
                continue
            files.add(relative)
    return sorted(files, key=lambda item: item.as_posix())


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def build_archive(root, files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=COMPRESS_LEVEL) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative.as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = FILE_MODE << 16
            info.internal_attr = 0
            archive.writestr(info, (root / relative.as_posix()).read_bytes(),
                             compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=COMPRESS_LEVEL)
    return buffer.getvalue()


def verify_archive(payload, version, expected_names):
    with tempfile.TemporaryDirectory(prefix="research-session-verify-") as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.testzip() is not None:
                raise PackageError("archive failed its internal CRC check")
            names = archive.namelist()
            archive.extractall(target)
        if names != expected_names:
            raise PackageError("archive entries do not match the sorted source list")
        for name in names:
            relative = PurePosixPath(name)
            reason = exclusion_reason(relative)
            if reason:
                raise PackageError("excluded path leaked into archive: %s (%s)" % (name, reason))
        for name in REQUIRED_PATHS:
            if not (target / name).exists():
                raise PackageError("required path missing after extraction: " + name)
        codex = read_json(target / ".codex-plugin" / "plugin.json")["version"]
        trae = read_json(target / ".trae-plugin" / "plugin.json")["version"]
        if codex != trae or codex != version:
            raise PackageError("extracted manifests disagree: %s / %s / %s" % (codex, trae, version))
        skills = sorted((target / "skills").glob("*/SKILL.md"))
        if len(skills) != EXPECTED_SKILLS:
            raise PackageError("expected %d skills, found %d" % (EXPECTED_SKILLS, len(skills)))
        for relative in expected_names:
            extracted = target / relative
            if digest_bytes(extracted.read_bytes()) != digest_bytes(
                    (ROOT / relative).read_bytes()):
                raise PackageError("content mismatch after extraction: " + relative)


def build_manifest(root, version, files, payload):
    entries = []
    for relative in files:
        data = (root / relative.as_posix()).read_bytes()
        entries.append({"path": relative.as_posix(), "sha256": digest_bytes(data),
                        "bytes": len(data)})
    return {
        "schema": "research-session-release/v1",
        "name": "research-session",
        "version": version,
        "contract_versions": contract_versions(root),
        "archive": {"filename": "research-session-%s.zip" % version,
                    "sha256": digest_bytes(payload), "bytes": len(payload)},
        "file_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "files": entries,
        "exclusion_rules": {
            "directories": sorted(EXCLUDE_DIR_NAMES),
            "file_names": sorted(EXCLUDE_FILE_NAMES),
            "suffixes": list(EXCLUDE_SUFFIXES),
            "private_evidence_prefix": PRIVATE_EVIDENCE_PREFIX,
            "root_allowlist_dirs": list(INCLUDE_DIRS),
            "root_allowlist_files": list(INCLUDE_FILES),
        },
    }


def run(check_only, out_dir):
    version = artifact_version(ROOT)
    files = collect_files(ROOT)
    payload = build_archive(ROOT, files)
    verify_archive(payload, version, [item.as_posix() for item in files])
    manifest = build_manifest(ROOT, version, files, payload)
    digest = manifest["archive"]["sha256"]

    if check_only:
        print(json.dumps({"check-only": "ok", "version": version,
                          "files": manifest["file_count"], "sha256": digest},
                         ensure_ascii=False))
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / manifest["archive"]["filename"]
    zip_path.write_bytes(payload)
    if hashlib.sha256(zip_path.read_bytes()).hexdigest() != digest:
        raise PackageError("written archive does not match its computed digest")

    sha_path = out_dir / (zip_path.name + ".sha256")
    sha_path.write_text("%s  %s\n" % (digest, zip_path.name), encoding="utf-8")

    manifest_path = out_dir / ("%s.manifest.json" % version)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    verify_archive(zip_path.read_bytes(), version, [item.as_posix() for item in files])
    print("%s  %s" % (digest, zip_path.name))
    print("package: %s" % zip_path)
    print("manifest: %s" % manifest_path)
    print("files: %d; skills: %d" % (manifest["file_count"], EXPECTED_SKILLS))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a deterministic release zip.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: <repo>/dist)")
    parser.add_argument("--check-only", action="store_true",
                        help="build and verify in memory without writing dist")
    args = parser.parse_args(argv)
    try:
        run(args.check_only, args.out.resolve())
    except PackageError as error:
        print("package_release: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
