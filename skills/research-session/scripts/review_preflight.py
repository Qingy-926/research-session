"""Read-only local review binding inspection; no provider, ledger or send path."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_dispatch as dispatch
import run_manifest

LAST_RESULT: dict = {}


def runtime_context() -> dict:
    root = Path(__file__).resolve().parents[1]
    for module in (dispatch, dispatch.review_evidence, dispatch.review_ledger):
        if Path(module.__file__).resolve().parent != root / "scripts":
            raise ValueError("review modules are not from the loaded skill directory")
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    version = re.search(r"版本 v([0-9]+(?:\.[0-9]+){1,2})", text)
    if version is None:
        raise ValueError("loaded skill version is missing")
    manifests = {}
    for host in (".codex-plugin", ".trae-plugin"):
        package_path = root.parent.parent / host / "plugin.json"
        if package_path.is_file():
            manifests[host] = json.loads(package_path.read_bytes()).get("version")
    if len(set(manifests.values())) > 1:
        raise ValueError("loaded plugin package manifests disagree on version")
    package_version = next(iter(manifests.values())) if manifests else None
    if manifests and package_version != version.group(1):
        raise ValueError("loaded plugin package and skill versions do not match")
    rules = dispatch.rules_binding(dispatch.REFERENCE_MD)
    manifest = json.loads((root / "references/review-rules-compatibility.json").read_bytes())
    if manifest.get("current_rules_sha256") != rules["rules_sha256"]:
        raise ValueError("loaded source does not match its rules compatibility manifest")
    script = root / "scripts/audit_dispatch.py"
    return {"skill_root": str(root), "skill_version": version.group(1), "package_version": package_version,
            "dispatch_path": str(script), "dispatch_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "preflight_path": str(Path(__file__).resolve()),
            "preflight_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), **rules}


def main(argv: list[str] | None = None) -> int:
    started = run_manifest.started_at()
    LAST_RESULT.clear()
    try:
        code = _main(argv)
    except SystemExit as stopped:
        code = stopped.code if isinstance(stopped.code, int) else 2
        run_manifest.record_run(LAST_RESULT, code, started)
        raise
    run_manifest.record_run(LAST_RESULT, code, started)
    return code


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--point", required=True, type=int, choices=(1, 2, 3))
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--model")
    parser.add_argument("--task-approval")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    result = {"schema": "research-session/local-preflight/v1", "local_only": True,
              "ready": False, "review_completed": False, "gate_passed": False,
              "receipt_current": False, "compatible_for_continuation": False,
              "current_route_verified": False, "route_validation": "not_checked",
              "blocked_steps": ["register", "finalize", "automatic_rerun"],
              "available_work": ["inspect_evidence_with_existing_authorization",
                                 "recompute_metrics_with_existing_authorization",
                                 "draft_with_explicit_uncertainty"],
              "limitations": ["No provider, current route, ledger coverage, authorization or review gate is established.",
                              "This inspection does not grant send, registration, finalization or rerun permission."]}
    try:
        result["runtime"] = runtime_context()
        args.reference = dispatch.REFERENCE_MD
        args.transport = "responses"
        args.local_only = True
        model = args.model or dispatch.POINT_MODEL[args.point]
        _, items = dispatch.load_checklist(args.point, args.reference)
        if args.receipt:
            receipt = dispatch.load_receipt(args.receipt, len(items))
            if receipt.get("unattended"):
                raise ValueError("offline fixture receipts are not eligible for ordinary review continuity")
            binding = dispatch.make_binding(args, model, receipt["binding"]["pack_sha256"])
            if "route" in receipt["binding"]:
                route = receipt["binding"]["route"]
                legacy = (isinstance(route, dict)
                          and set(route) == {"transport", "url", "wire_model"}
                          and route["transport"] == "codex-plus")
                responses = (isinstance(route, dict)
                             and set(route) == {"transport", "url", "wire_model", "provider", "api_key_env"}
                             and route["transport"] == "responses"
                             and isinstance(route["provider"], str) and bool(route["provider"].strip())
                             and (route["api_key_env"] is None or
                                  isinstance(route["api_key_env"], str) and bool(route["api_key_env"])))
                if (not (legacy or responses)
                        or any(not isinstance(route[key], str) or not route[key].strip()
                               for key in ("url", "wire_model"))):
                    raise ValueError("invalid historical route binding")
                binding["route"] = dict(route)
                result["route_validation"] = "historical_binding_only"
            if Path(args.pack).resolve() != Path(receipt["pack"]["path"]).resolve():
                raise ValueError("local receipt check requires its original bound pack")
            result["receipt_decision"] = (receipt.get("report") or {}).get("decision", "未完成")
            result["binding"] = binding
            result["receipt_current"] = binding == receipt["binding"]
            if result["receipt_current"]:
                result["ready"] = True
            elif binding["rules_sha256"] != receipt["binding"]["rules_sha256"]:
                transition = dispatch.compatible_rules_transition(receipt["binding"]["rules_sha256"], binding)
                result["rules_transition"] = transition
                keys = set(binding) | set(receipt["binding"])
                same_evidence = all(binding.get(key) == receipt["binding"].get(key) for key in keys
                                    if key not in {"rules_sha256", "compatibility_manifest_sha256"})
                result["compatible_for_continuation"] = same_evidence
            if not result["receipt_current"]:
                result["error"] = "receipt is historical for this binding; no inherited approval or automatic resend"
        else:
            data = Path(args.pack).read_bytes()
            if not data.strip():
                raise ValueError("review pack is empty")
            result["binding"] = dispatch.make_binding(args, model, hashlib.sha256(data).hexdigest())
            result["ready"] = True
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result["error"] = str(exc)
    LAST_RESULT.update(result)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
