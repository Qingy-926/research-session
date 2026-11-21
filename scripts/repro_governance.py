"""Read-only, evidence-bounded paper reproduction checks (stdlib only).

PASS means the implemented checks passed, not that paper semantics are proven.
No command starts training, downloads data, executes profile commands, or changes
Git. init/freeze/snapshot write new files only when explicitly requested.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "rigorous-paper-reproduction/v1"
GATE_FIELDS = ("metric", "method", "split", "selection_semantics", "scope", "score_semantics", "purpose")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Non-finite JSON: {x}")))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")


def inside(root, rel):
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise ValueError(f"Expected a nonempty project-relative path: {rel!r}")
    root = Path(root).resolve()
    path = (root / rel).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"Path escapes project or resolves to root: {rel}")
    return path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, (str, list, dict)) and bool(value)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Report:
    def __init__(self, action):
        self.data = {"action": action, "passed": True, "checks": [], "failures": [],
                     "limitations": ["Checks validate recorded evidence; human/AI semantic review and truthful upstream artifacts remain necessary."]}

    def check(self, name, fn, context=""):
        try:
            detail = fn()
            self.data["checks"].append({"check": name, "context": str(context), "detail": detail})
            return detail
        except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
            self.data["failures"].append({"check": name, "context": str(context), "error": str(exc)})
            self.data["passed"] = False
            return None

    def merge(self, result):
        self.data["checks"].extend(result["checks"])
        self.data["failures"].extend(result["failures"])
        self.data["passed"] &= result["passed"]


def load_profile(path):
    path = Path(path).resolve()
    profile = read_json(path)
    require(isinstance(profile, dict) and profile.get("schema") == SCHEMA, "Missing/unsupported profile schema")
    require(nonempty(profile.get("project_root")), "project_root must be explicit")
    root = (path.parent / profile["project_root"]).resolve()
    require(root.is_dir(), f"Project root missing: {root}")
    return profile, root


def check_file(root, item):
    path = inside(root, item["path"])
    expected = item["sha256"]
    require(isinstance(expected, str) and len(expected) == 64, "Full SHA-256 required")
    actual = sha256(path)
    require(actual == expected.lower(), f"Hash mismatch: {item['path']}; actual={actual}")
    return {"path": str(path), "sha256": actual}


def protocol_shape(protocol):
    require(nonempty(protocol.get("id")), "Protocol id required")
    require(protocol.get("stage") in ("development", "formal", "diagnostic"), "stage must be explicit")
    methods = protocol.get("methods")
    require(isinstance(methods, list) and methods and all(isinstance(m, str) and m for m in methods), "Explicit methods required")
    require(len(methods) == len(set(methods)), "Duplicate methods")
    axes = protocol.get("random_axes")
    require(isinstance(axes, dict) and axes, "Explicit random axes and values required, even for one seed")
    count = len(methods)
    for key, values in axes.items():
        require(isinstance(values, list) and values and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in values), f"Invalid axis: {key}")
        require(len(values) == len(set(values)), f"Duplicate axis values: {key}")
        count *= len(values)
    require(count <= 100000, "Matrix exceeds validator safety limit (100000 cells)")
    assignment_axes = protocol.get("assignment_axes")
    require(isinstance(assignment_axes, dict) and assignment_axes, "Explicit assignment_axes mapping required")
    require(set(assignment_axes) <= {"cohort", "samples", "training"}, "Unknown assignment axis role")
    require(set(assignment_axes.values()) <= set(axes), "assignment_axes must reference declared axes")
    require(len(set(assignment_axes.values())) == len(assignment_axes), "Random axes must have separate meanings")
    require(isinstance(protocol.get("excluded_subjects"), list), "Explicit exclusions required; [] is allowed")
    require(nonempty(protocol.get("required_roles")), "Explicit dataset roles required")
    require(nonempty(protocol.get("metric_definitions")), "Metric definitions required")
    require(isinstance(protocol.get("dataset_hash"), str) and len(protocol["dataset_hash"]) == 64, "Dataset manifest/data SHA-256 required")
    return {"expected_runs": count, "methods": methods, "random_axes": axes}


def audit_paper(profile, root):
    r = Report("audit-paper")

    def audit():
        paper = profile["paper"]
        require(nonempty(paper.get("title")), "Paper title required")
        required = paper["required_items"]
        require(isinstance(required, list) and required, "List method/metric issues requiring source review")
        doc = read_json(inside(root, paper["evidence_file"]))
        items = doc["items"]
        require(isinstance(items, list) and items, "No source-review items")
        ids = [i["id"] for i in items]
        require(len(ids) == len(set(ids)) and set(required) <= set(ids), "Missing or duplicate source-review items")
        for item in items:
            check_file(root, {"path": item["source_file"], "sha256": item["source_sha256"]})
            check_file(root, {"path": item["implementation_file"], "sha256": item["implementation_sha256"]})
            for key in ("locator", "quote", "implementation_locator", "interpretation"):
                require(nonempty(item.get(key)), f"{item['id']}: missing {key}")
            # Use retained UTF-8 extracted text, not binary PDF decoding.
            text = inside(root, item["source_file"]).read_text(encoding="utf-8-sig")
            require(item["quote"] in text, f"{item['id']}: quote not found in retained source text")
            require(item.get("review_status") == "reviewed", f"{item['id']}: semantic review unresolved")
        return {"items": len(items), "semantic_status": "recorded_review_not_independently_proven"}

    r.check("paper_evidence", audit)
    return r.data


def preflight(path):
    r = Report("preflight")
    loaded = r.check("profile", lambda: load_profile(path))
    # Do not serialize the internal profile/Path as a check detail.
    if loaded is None:
        return r.data
    r.data["checks"][-1]["detail"] = "schema and root parsed"
    p, root = loaded
    r.data["project_root"] = str(root)
    r.merge(audit_paper(p, root))
    r.check("protocol_shape", lambda: protocol_shape(p["protocol"]))
    for section in ("code", "data"):
        items = p.get(section, {}).get("required_files", [])
        r.check("required_files", lambda items=items: require(isinstance(items, list) and bool(items), "Nonempty code/data file manifest required"), section)
        if isinstance(items, list):
            for item in items:
                r.check("file_sha256", lambda item=item: check_file(root, item), section)
    commit = r.check("git_head", lambda: subprocess.check_output(["git", "--no-optional-locks", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.PIPE, timeout=15).strip())
    r.check("canonical_commit", lambda: require(commit is not None and commit == p["code"]["canonical_commit"], "Canonical source commit differs or is missing"))
    r.check("clean_policy", lambda: require(p["code"].get("require_clean_git") is True, "Dirty source may be diagnosed but cannot be preflight-ready"))
    r.check("git_clean", lambda: require(not subprocess.check_output(["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=normal"], cwd=root, text=True, stderr=subprocess.PIPE, timeout=30).strip(), "Dirty canonical tree (including untracked files)"))

    def alignment():
        protocol = p["protocol"]
        check_file(root, {"path": protocol["file"], "sha256": protocol["sha256"]})
        frozen = read_json(inside(root, protocol["file"]))
        declared = {k: v for k, v in protocol.items() if k not in ("file", "sha256")}
        require(frozen == declared, "Runtime profile differs from frozen protocol content")
        require(p["data"]["excluded_subjects"] == protocol["excluded_subjects"], "Declared exclusions differ")
        require(protocol["dataset_hash"] in [x["sha256"] for x in p["data"]["required_files"]], "Dataset identifier is not bound to required data/manifest")
        return {"protocol_hash": protocol["sha256"]}

    r.check("protocol_alignment", alignment)

    def tests():
        spec = p["tests"]
        evidence = read_json(inside(root, spec["evidence_file"]))
        require(evidence.get("exit_code") == 0 and spec.get("required_exit_code") == 0, "Tests not recorded as successful")
        require(nonempty(evidence.get("command")), "Test command missing")
        check_file(root, {"path": evidence["log_file"], "sha256": evidence["log_sha256"]})
        return evidence

    evidence = r.check("tests_evidence", tests)
    r.check("tests_commit", lambda: require(evidence is not None and evidence.get("commit") == commit, "Test evidence bound to another/missing commit"))

    def disk():
        spec = p["resources"]
        needed = spec["minimum_free_gb"]
        require(finite(needed) and needed >= 0, "Non-negative explicit storage estimate required")
        free = shutil.disk_usage(root).free / 1e9
        require(free >= needed, f"Disk insufficient: {free:.3f} GB free vs {needed:.3f} GB required")
        return {"free_gb": free, "required_gb": needed, "gpu_status": "not_probed"}

    r.check("disk", disk)
    matrix = r.check("matrix_file", lambda: read_json(inside(root, p["matrix_file"])))
    if matrix is not None:
        r.data["checks"][-1]["detail"] = "matrix read"
        r.merge(verify_matrix(p, matrix, root))
    return r.data


def normalized_hash(mapping):
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def read_assignment(root, row, protocol, r):
    check_file(root, {"path": row["assignment_file"], "sha256": row["assignment_sha256"]})
    a = read_json(inside(root, row["assignment_file"]))
    roles, samples = a["roles"], a["samples"]
    require(isinstance(roles, dict) and set(roles) == set(protocol["required_roles"]) == set(samples), "Actual roles must match required_roles")
    for role, ids in roles.items():
        require(isinstance(ids, list) and ids and all(isinstance(x, str) and x for x in ids), f"Invalid/empty subjects in {role}")
        require(len(ids) == len(set(ids)), f"Duplicate subject in {role}")
        require(isinstance(samples[role], list) and samples[role], f"Samples missing: {role}")
        for x in samples[role]:
            require(isinstance(x.get("sample_id"), str) and x["sample_id"], "Sample id required")
            require(x["subject_id"] in ids, "Sample subject not in actual role")
        require(len({x["sample_id"] for x in samples[role]}) == len(samples[role]), f"Duplicate sample in {role}")
    actual = set().union(*(set(x) for x in roles.values()))
    r.check("excluded_subjects", lambda: require(not actual.intersection(protocol["excluded_subjects"]), "Excluded subjects present in actual roles"), row["assignment_file"])
    if "expected_cohort" in protocol:
        r.check("cohort_membership", lambda: require(actual == set(protocol["expected_cohort"]), "Actual cohort differs from declared universe"), row["assignment_file"])
    for left, right in protocol.get("disjoint_subject_roles", []):
        require(not set(roles[left]).intersection(roles[right]), f"Forbidden subject overlap: {left}/{right}")
    for left, right in protocol.get("disjoint_sample_roles", []):
        require(not {x["sample_id"] for x in samples[left]}.intersection(x["sample_id"] for x in samples[right]), f"Forbidden sample overlap: {left}/{right}")
    for left, right in protocol.get("disjoint_recording_roles", []):
        l = {(x["subject_id"], x["recording_id"]) for x in samples[left]}
        rr = {(x["subject_id"], x["recording_id"]) for x in samples[right]}
        require(not l.intersection(rr), f"Forbidden recording overlap: {left}/{right}")
    cohort = normalized_hash({k: sorted(v) for k, v in roles.items()})
    beats = normalized_hash({k: sorted(v, key=lambda x: x["sample_id"]) for k, v in samples.items()})
    for key, val in (("cohort_hash", cohort), ("beat_selection_hash", beats)):
        require(key not in row or row[key] == val, f"Claimed {key} differs from artifact")
    return cohort, beats


def verify_matrix(profile, rows, root=None):
    r = Report("verify-matrix")
    proto = profile.get("protocol", {})
    shape = r.check("matrix_shape", lambda: protocol_shape(proto))
    if shape is None:
        return r.data
    r.data["expected_runs"] = shape["expected_runs"]
    axes = list(proto["random_axes"])
    expected = set(itertools.product(proto["methods"], *(proto["random_axes"][k] for k in axes)))
    r.check("matrix_rows", lambda: require(isinstance(rows, list) and bool(rows), "Nonempty matrix rows required"))
    if not isinstance(rows, list):
        return r.data
    seen, assignments = [], []
    for i, row in enumerate(rows):
        key = r.check("matrix_cell", lambda row=row: (row["method"], *(row[k] for k in axes)), i)
        if key is None:
            continue
        seen.append(key)
        pair = r.check("assignment_evidence", lambda row=row: read_assignment(root, row, proto, r), i)
        if pair is not None:
            assignments.append((row, *pair))
    r.check("matrix_completeness", lambda: require(len(seen) == len(set(seen)) and set(seen) == expected, "Missing, unexpected, or duplicate method/seed cells"))
    ca = proto["assignment_axes"].get("cohort")
    sa = proto["assignment_axes"].get("samples")
    cohorts, samples = {}, {}
    for row, ch, bh in assignments:
        ckey = row[ca] if ca else "fixed"
        skey = (ckey, row[sa] if sa else "fixed")
        cohorts.setdefault(ckey, set()).add(ch)
        samples.setdefault(skey, set()).add(bh)
    r.check("paired_cohort", lambda: require(all(len(x) == 1 for x in cohorts.values()), "Cohort changed across methods/training/sample seeds"))
    r.check("paired_samples", lambda: require(all(len(x) == 1 for x in samples.values()), "Sample assignments changed across paired methods/training seeds"))
    if ca and len(proto["random_axes"][ca]) > 1:
        r.check("subject_seed_collapsed", lambda: require(len(set().union(*cohorts.values())) == len(proto["random_axes"][ca]), "Distinct subject seeds did not yield distinct subject-role assignments"))
    r.data["assignment_artifacts_verified"] = len(assignments)
    return r.data


def matches(row, gate):
    return all(row.get(k) == gate.get(k) and nonempty(gate.get(k)) for k in GATE_FIELDS)


def recompute(row, source, definition):
    adapter = definition["adapter"]
    require(row["adapter"] == adapter and row["unit"] == definition["unit"], "Adapter/unit differs from protocol")
    unit = row["unit"]
    require(unit in ("percent", "fraction"), "Unsupported unit")
    scale = 100 if unit == "percent" else 1
    if adapter == "count_rate":
        require("eer" not in row["metric"].lower(), "EER cannot be evaluated as arbitrary single count ratio")
        require(row["numerator_key"] == definition["numerator_key"] and row["denominator_key"] == definition["denominator_key"], "Count keys differ from preregistered definition")
        n, d = source[definition["numerator_key"]], source[definition["denominator_key"]]
        require(type(n) is int and type(d) is int and d > 0 and 0 <= n <= d, "Invalid integer counts")
        return scale * n / d
    if adapter == "curve_crossing_linear":
        # Curves must be fractions from independently validated scoring code.
        require(row["false_accept_key"] == definition["false_accept_key"] and row["false_reject_key"] == definition["false_reject_key"], "Curve keys differ from preregistered definition")
        x, y = source[definition["false_accept_key"]], source[definition["false_reject_key"]]
        require(isinstance(x, list) and isinstance(y, list) and len(x) == len(y) >= 2, "Two equal-length curves required")
        require(all(finite(v) and 0 <= v <= 1 for v in x + y), "Curves must contain finite fractions")
        require(all(a <= b for a, b in zip(x, x[1:])) and all(a >= b for a, b in zip(y, y[1:])), "FAR ascending / FRR descending order required")
        diffs = [a - b for a, b in zip(x, y)]
        require(diffs[0] <= 0 <= diffs[-1], "Curves do not bracket equal-error crossing; no invented EER")
        for i, d in enumerate(diffs):
            if d == 0:
                return scale * x[i]
            if d > 0:
                weight = -diffs[i - 1] / (d - diffs[i - 1])
                return scale * (x[i - 1] + weight * (x[i] - x[i - 1]))
    raise ValueError(f"Unsupported adapter: {adapter}; manual/project-specific validation required")


def audit_metrics(rows, profile=None, root=None):
    r = Report("audit-metrics")
    p = profile or {}
    protocol = p.get("protocol", {})
    axes = list(protocol.get("random_axes", {}))
    fields = (*GATE_FIELDS, "dataset_hash", "protocol_hash", "code_commit", "value", "unit", "source_json", "source_sha256", "adapter", "participates_in_gate", "assignment_sha256", *axes)
    r.check("metric_rows", lambda: require(isinstance(rows, list) and bool(rows), "Nonempty metric rows required"))
    if not isinstance(rows, list):
        return r.data
    for i, row in enumerate(rows):
        valid = r.check("metric_provenance", lambda row=row: (require(all(k in row and row[k] is not None for k in fields), "Missing metric provenance"), True)[1], i)
        if not valid:
            continue
        eligible = [g for g in protocol.get("gates", []) if matches(row, g)]
        if row["participates_in_gate"] is True:
            if row["selection_semantics"] == "test_oracle":
                r.check("oracle_in_gate", lambda eligible=eligible: require(any(g.get("allow_test_oracle") is True for g in eligible), "Oracle requires a matching explicit protocol gate; row claims do not authorize it"), i)
            r.check("gate_eligibility", lambda row=row, eligible=eligible: require(row["purpose"] == "formal" and len(eligible) == 1, "Gate row is diagnostic, unmatched, or ambiguous"), i)

        def provenance(row=row):
            require(bool(profile) and root is not None and bool(axes), "Bound profile/root/random axes required")
            require(type(row["participates_in_gate"]) is bool, "Gate flag must be Boolean")
            require(row["dataset_hash"] == protocol["dataset_hash"] and row["protocol_hash"] == protocol["sha256"] and row["code_commit"] == p["code"]["canonical_commit"], "Mixed data/protocol/source tree")
            require(row["method"] in protocol["methods"] and all(row[k] in protocol["random_axes"][k] for k in axes), "Undeclared method/seed")
            check_file(root, {"path": row["source_json"], "sha256": row["source_sha256"]})
            source = read_json(inside(root, row["source_json"]))
            context_keys = (*GATE_FIELDS, "dataset_hash", "protocol_hash", "code_commit", "assignment_sha256", *axes)
            require(isinstance(source.get("context"), dict) and all(source["context"].get(k) == row[k] for k in context_keys), "Original result context differs from summary row (or is missing)")
            matrix = read_json(inside(root, p["matrix_file"]))
            matching = [x for x in matrix if x["method"] == row["method"] and all(x[k] == row[k] for k in axes)]
            require(len(matching) == 1 and matching[0]["assignment_sha256"] == row["assignment_sha256"], "Metric assignment differs from actual matrix cell")
            return "source context, assignment, hash and profile bound"

        r.check("metric_source", provenance, i)

        def value(row=row):
            require(finite(row["value"]), "Metric must be finite numeric value")
            definition = protocol["metric_definitions"][row["metric"]]
            require(nonempty(definition.get("definition")), "Explicit metric definition required")
            actual = recompute(row, read_json(inside(root, row["source_json"])), definition)
            require(math.isclose(row["value"], actual, rel_tol=0, abs_tol=1e-8), f"Reported {row['value']} != recomputed {actual}")
            return {"reported": row["value"], "recomputed": actual}

        r.check("metric_value", value, i)
    return r.data


def verdict(path):
    r = Report("verdict")
    r.data["decision"] = "BLOCKED"
    r.merge(preflight(path))
    loaded = r.check("verdict_profile", lambda: load_profile(path))
    if loaded is None:
        return r.data
    r.data["checks"][-1]["detail"] = "parsed"
    p, root = loaded
    rows = r.check("metrics_file", lambda: read_json(inside(root, p["metrics_file"])))
    if rows is None:
        return r.data
    r.data["checks"][-1]["detail"] = "metrics read"
    r.merge(audit_metrics(rows, p, root))
    matrix = r.check("completed_runs", lambda: read_json(inside(root, p["matrix_file"])))
    if matrix is not None:
        r.data["checks"][-1]["detail"] = "matrix read"
        r.check("runs_completed", lambda: require(all(x.get("status") == "completed" for x in matrix), "Planned/pilot/incomplete cells are not formal results"))
    r.check("formal_stage", lambda: require(p["protocol"]["stage"] == "formal", "Development/diagnostic experiments cannot receive a formal verdict"))
    gates = p.get("protocol", {}).get("gates", [])
    r.check("gates_defined", lambda: require(isinstance(gates, list) and bool(gates), "No preregistered gates"))
    outcomes = []
    for gate in gates:
        def judge(gate=gate):
            require(all(nonempty(gate.get(k)) for k in (*GATE_FIELDS, "id")), "Incomplete gate definition")
            require(gate["aggregate"] == "mean", "Only predeclared arithmetic mean supported; other aggregations require adapter")
            axes = list(p["protocol"]["random_axes"])
            expected = set(itertools.product(*(p["protocol"]["random_axes"][k] for k in axes)))
            chosen = [x for x in rows if matches(x, gate) and x["participates_in_gate"] is True]
            observed = [tuple(x[k] for k in axes) for x in chosen]
            require(len(observed) == len(set(observed)) and set(observed) == expected, "Gate missing/duplicating seed cells; no selective omission")
            require(gate["operator"] in (">=", "<=") and finite(gate["threshold"]), "Explicit numeric criterion required")
            values = [x["value"] for x in chosen]
            require(all(finite(v) for v in values), "Non-finite gate value")
            mean = statistics.mean(values)
            passed = mean >= gate["threshold"] if gate["operator"] == ">=" else mean <= gate["threshold"]
            return {"gate": gate["id"], "mean": mean, "sd": statistics.stdev(values) if len(values) > 1 else None,
                    "n": len(values), "criterion_met": passed, "threshold": gate["threshold"], "unit": chosen[0]["unit"]}
        result = r.check("gate_matrix", judge, gate.get("id", ""))
        if result is not None:
            outcomes.append(result)
    r.data["gates"] = outcomes
    if r.data["passed"]:
        r.data["decision"] = "CRITERIA_MET" if all(x["criterion_met"] for x in outcomes) else "CRITERIA_NOT_MET"
    r.data["interpretation"] = "Protocol-bound arithmetic only. Not proof of paper impossibility, literature inconsistency, deployability, or causal improvement. SD across matrix cells is descriptive, not an independence-based significance test."
    return r.data


def init_profile(output, root):
    template = read_json(Path(__file__).resolve().parents[1] / "assets" / "profile.template.json")
    template["project_root"] = str(Path(root).resolve())
    write_new(output, template)
    return {"action": "init", "created": str(Path(output).resolve()), "ready": False}


def freeze_protocol(profile_path, output):
    p, _ = load_profile(profile_path)
    proto = {k: v for k, v in p["protocol"].items() if k not in ("file", "sha256")}
    protocol_shape(proto)
    write_new(output, proto)
    return {"action": "freeze-protocol", "created": str(Path(output).resolve()), "sha256": sha256(output),
            "next": "Bind file path/hash in profile; review before new test evaluations. No semantic approval is implied."}


def snapshot(profile_path, output):
    p, root = load_profile(profile_path)
    files = [check_file(root, x) for x in p["code"]["required_files"]]
    out = {"action": "snapshot-code", "canonical_commit": p["code"]["canonical_commit"], "files": files,
           "note": "Content manifest, not a backup of uncommitted source; retain canonical Git commit separately."}
    write_new(output, out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "init", "audit-paper", "freeze-protocol", "preflight", "verify-matrix", "audit-metrics", "verdict", "snapshot-code", "audit-logs", "archive"])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="New file only; existing files never overwritten")
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            require(args.project_root is not None, "init requires --project-root")
            result = init_profile(args.profile, args.project_root)
        elif args.action in ("freeze-protocol", "snapshot-code"):
            require(args.output is not None, "Explicit --output required")
            result = (freeze_protocol if args.action == "freeze-protocol" else snapshot)(args.profile, args.output)
        elif args.action in ("preflight", "status", "verdict"):
            result = (verdict if args.action == "verdict" else preflight)(args.profile)
        elif args.action in ("audit-logs", "archive"):
            import log_governance as logs
            p = logs.load_profile(args.profile)
            result = logs.audit_logs(p) if args.action == "audit-logs" else logs.archive_logs(p, apply=False)
        else:
            p, root = load_profile(args.profile)
            if args.action == "audit-paper":
                result = audit_paper(p, root)
            elif args.action == "verify-matrix":
                result = verify_matrix(p, read_json(inside(root, p["matrix_file"])), root)
            else:
                result = audit_metrics(read_json(inside(root, p["metrics_file"])), p, root)
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        if args.output and args.action not in ("init", "freeze-protocol", "snapshot-code"):
            write_new(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result.get("passed", True) else 2
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"action": args.action, "passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
