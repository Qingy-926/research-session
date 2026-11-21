"""Minimal secret-free JSON run manifest; standard library only.

Disabled by default: a manifest is written only when
RESEARCH_SESSION_RUN_MANIFEST_DIR names an existing or creatable directory.
No network, no telemetry, and an existing <run_id>.json is never overwritten.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

SCHEMA = "research-session/run-manifest/v1"
REDACTED = "[REDACTED]"
SENSITIVE_KEY = re.compile(r"(?i)(token|secret|api_key|authorization|password|bearer|credential)")
BEARER = re.compile(r"(?i)\bbearer\s+\S+")
SECRET_SHAPE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}")
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def started_at():
    return datetime.datetime.now(datetime.timezone.utc)


def redact(value):
    """Recursively mask credential-bearing keys and bearer/secret-shaped strings."""
    if isinstance(value, dict):
        return {
            key: REDACTED if isinstance(key, str) and SENSITIVE_KEY.search(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_SHAPE.sub(REDACTED, BEARER.sub(REDACTED, value))
    return value


def config_sha256(route):
    """Hash the canonical {url, model, provider} of a route; never api_key_env or credentials."""
    if not isinstance(route, dict):
        return None
    config = {
        "url": route.get("url"),
        "model": route.get("wire_model") or route.get("model"),
        "provider": route.get("provider"),
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def component_versions():
    versions = {"python": "%d.%d.%d" % sys.version_info[:3]}
    package = {}
    for host in (".codex-plugin", ".trae-plugin"):
        try:
            package = json.loads((PLUGIN_ROOT / host / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(package, dict) and isinstance(package.get("version"), str) and package["version"]:
            break
        package = {}
    if isinstance(package, dict) and isinstance(package.get("version"), str) and package["version"]:
        versions["plugin"] = package["version"]
    return versions


def host_info():
    return {"name": os.environ.get("RESEARCH_SESSION_HOST") or "unknown",
            "version": os.environ.get("RESEARCH_SESSION_HOST_VERSION") or "unknown"}


def status_for(payload, exit_code):
    """gate_passed only when the CLI really reports it; exit 0 alone is just "completed"."""
    if isinstance(payload, dict) and payload:
        if payload.get("gate_passed") is True:
            return "gate_passed"
        return "completed" if exit_code == 0 else "stopped"
    return "stopped" if exit_code else "no_result"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(rows):
    return [{"path": row["path"], "sha256": row["sha256"]} for row in rows or []
            if isinstance(row, dict) and isinstance(row.get("path"), str) and isinstance(row.get("sha256"), str)]


def build(payload, exit_code, started):
    payload = payload if isinstance(payload, dict) else {}
    binding = payload.get("binding") if isinstance(payload.get("binding"), dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else binding.get("route")
    artifacts = []
    receipt = payload.get("receipt")
    if isinstance(receipt, str) and os.path.isfile(receipt):
        artifacts.append({"path": receipt, "sha256": file_sha256(receipt)})
    finished = datetime.datetime.now(datetime.timezone.utc)
    manifest = {
        "schema": SCHEMA,
        "run_id": uuid.uuid4().hex,
        "created_at": finished.isoformat(),
        "host": host_info(),
        "component_versions": component_versions(),
        "config_sha256": config_sha256(route),
        "status": status_for(payload, exit_code),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "artifacts": artifacts,
        "references": _rows(binding.get("files")),
        "error": None if payload.get("error") is None else str(payload["error"]),
    }
    return redact(manifest)


def emit_for_run(directory, manifest):
    """Write <run_id>.json into directory; refuse to overwrite an existing file."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / (manifest["run_id"] + ".json")
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return str(path)


def record_run(payload, exit_code, started):
    """Best-effort manifest hook for CLI wrappers; a write failure never changes the exit code."""
    directory = os.environ.get("RESEARCH_SESSION_RUN_MANIFEST_DIR")
    if not directory:
        return None
    try:
        return emit_for_run(directory, build(payload, exit_code, started))
    except (OSError, ValueError, TypeError):
        return None
