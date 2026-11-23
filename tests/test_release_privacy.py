"""Scan the actual release archive, not just the source tree.

The scanner classifies hits by category. The synthetic fixture proves that a
non-empty detection fails the gate; the real archive must stay clean.
"""

import importlib.util
import io
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".json", ".py", ".yml", ".yaml", ".toml", ".txt", ".ini",
                 ".ps1", ".js", ".cjs", ".gitignore", ".gitattributes"}
PATTERNS = {
    "internal_identifier": (r"gpt-[0-9]\.[0-9]-sol", "dieq" "iyun"),
    "private_data": (r"project-0[0-9][0-9]", "HUA" "WEI", r"\.cc-" "switch",
                     r"skill-" "backups", r"[A-Za-z]:\\Users\\"),
    "credential": (r"\bsk-[A-Za-z0-9]{16,}", r"\bghp_[A-Za-z0-9]{20,}",
                   r"Bearer\s+(?!<|\[|\$|\{|secret-|fixture-|test-)[A-Za-z0-9._-]{16,}"),
}


def scan_zip(payload):
    hits = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = archive.read(name).decode("utf-8", "ignore")
            classes = [category for category, patterns in PATTERNS.items()
                       if any(re.search(pattern, text) for pattern in patterns)]
            if classes:
                hits[name] = classes
    return hits


def release_payload():
    spec = importlib.util.spec_from_file_location(
        "privacy_package_release", ROOT / "tools" / "package_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_archive(ROOT, module.collect_files(ROOT))


def test_release_archive_has_no_sensitive_markers():
    hits = scan_zip(release_payload())
    assert hits == {}, hits


def test_scanner_fails_on_a_synthetic_sensitive_fixture():
    sensitive = " ".join(("gpt-" + "9.9-sol", "dieq" + "iyun-fixture",
                          "project-" + "099", "sk-" + "A" * 24))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("fixture/leak.txt", sensitive + "\n")
    hits = scan_zip(buffer.getvalue())
    assert hits == {"fixture/leak.txt": ["internal_identifier", "private_data", "credential"]}
