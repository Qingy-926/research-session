"""Relocating guidance must not remove it from the review's trusted binding."""

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class AuditModule(Protocol):
    def make_binding(self, args: argparse.Namespace, model: str, body_sha: str) -> dict[str, object]: ...


class SplitRuleBindingTest(unittest.TestCase):
    def test_changes_in_each_extracted_rule_invalidate_binding(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules), patch.object(sys, "path", list(sys.path)):
            root = Path(directory)
            copied = root / "skill"
            _ = shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
            script = copied / "scripts" / "audit_dispatch.py"
            spec = importlib.util.spec_from_file_location("split_binding_audit", script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            audit = cast(AuditModule, cast(object, module))
            source = root / "protocol.md"
            _ = source.write_bytes(b"fixture protocol\n")
            manifest = root / "inputs.json"
            _ = manifest.write_text(json.dumps({"review_id": "rule-split-fixture",
                "files": [{"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding="utf-8")
            args = argparse.Namespace(inputs=str(manifest), pack=str(root / "pack.md"),
                reference=str(copied / "references" / "codex-review-pack.md"), point=1)
            for name in ("project-work.md", "preregistration.md", "independent-review.md", "autonomous-start.md",
                         "paper-audit.md", "manuscript-writing.md"):
                with self.subTest(reference=name):
                    reference = copied / "references" / name
                    before = audit.make_binding(args, "fixture-review-model", "0" * 64)["rules_sha256"]
                    original = reference.read_bytes()
                    _ = reference.write_bytes(original + b"\nChanged rule fixture.\n")
                    after = audit.make_binding(args, "fixture-review-model", "0" * 64)["rules_sha256"]
                    self.assertNotEqual(before, after, f"Unbound extracted rule: {name}")
                    _ = reference.write_bytes(original)


if __name__ == "__main__":
    _ = unittest.main()
