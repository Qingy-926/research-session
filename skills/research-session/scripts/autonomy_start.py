"""Inspect a conversation startup card locally; never authorize or execute it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


RESOURCES = ("seconds", "model_calls", "gpu_seconds", "disk_bytes", "cost_minor")
ACTION_KINDS = {"inspect", "recompute", "candidate_patch", "test", "draft", "approved_task"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("startup file exceeds 64 KiB")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))
    return raw, data


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError(label + " has missing or unknown fields")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")
    return value


def texts(value, label, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(label + " must be a text list")
    for item in value:
        text(item, label)
    if len(set(value)) != len(value):
        raise ValueError(label + " contains duplicates")
    return value


def integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + " must be an integer >= " + str(minimum))
    return value


def deadline(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("deadline must be an absolute UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def inspect_card_v1(card, now):
    fields(card, "schema project_root goal expires_at_utc context budgets prohibited stop_conditions recommended_option options", "card")
    if card["schema"] != "research-session/start-card/v1":
        raise ValueError("unknown startup card schema")
    text(card["goal"], "goal")
    expires = deadline(card["expires_at_utc"])
    if expires <= now:
        raise ValueError("startup card expired")
    root = Path(text(card["project_root"], "project_root"))
    if not root.is_absolute() or str(root).startswith(("\\\\", "//")) or not root.is_dir():
        raise ValueError("project_root must be an existing absolute local directory")
    root = root.resolve()
    if not isinstance(card["context"], list) or not card["context"]:
        raise ValueError("context must bind the current project README and relevant evidence")
    bound = set()
    for item in card["context"]:
        fields(item, "path sha256", "context item")
        path = Path(text(item["path"], "context path"))
        if not path.is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError("context path is outside project")
        path = local_path(path, root, regular=True)
        if path in bound or not re.fullmatch("[0-9a-f]{64}", str(item["sha256"])):
            raise ValueError("duplicate context or invalid context SHA")
        with path.open("rb") as stream:
            actual_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_sha != item["sha256"]:
            raise ValueError("context drift: " + str(path))
        bound.add(path)
    if (root / "README.md").resolve() not in bound:
        raise ValueError("current project README is not bound")
    budgets = card["budgets"]
    fields(budgets, "runtime_seconds closeout_seconds max_action_seconds model_calls gpu_seconds disk_bytes cost_minor currency cost_metered", "budgets")
    for key in ("runtime_seconds", "closeout_seconds", "max_action_seconds", "model_calls", "gpu_seconds", "disk_bytes", "cost_minor"):
        integer(budgets[key], key, minimum=1 if key.endswith("seconds") and key != "gpu_seconds" else 0)
    if not re.fullmatch(r"[A-Z]{3}", str(budgets["currency"])):
        raise ValueError("currency must be an explicit three-letter code")
    if budgets["closeout_seconds"] >= budgets["runtime_seconds"] or type(budgets["cost_metered"]) is not bool:
        raise ValueError("invalid closeout reserve or cost metering declaration")
    texts(card["prohibited"], "prohibited")
    texts(card["stop_conditions"], "stop_conditions")
    options = card["options"]
    if not isinstance(options, list) or not 1 <= len(options) <= 3:
        raise ValueError("provide one to three concrete options, not filler")
    checked, option_ids = [], set()
    for option in options:
        fields(option, "id deliverables reason write_scope new_permissions actions", "option")
        option_id = text(option["id"], "option id")
        if option_id in option_ids or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", option_id):
            raise ValueError("duplicate or invalid option id")
        option_ids.add(option_id)
        texts(option["deliverables"], "deliverables")
        text(option["reason"], "recommendation reason")
        texts(option["write_scope"], "write_scope", empty=True)
        texts(option["new_permissions"], "new_permissions", empty=True)
        if not isinstance(option["actions"], list) or not option["actions"]:
            raise ValueError("option has no fixed actions")
        totals = dict.fromkeys(RESOURCES, 0)
        blockers, action_ids, required_ids = [], [], set()
        for action in option["actions"]:
            fields(action, "id kind description context_paths phase requires condition estimate", "action")
            action_id = text(action["id"], "action id")
            if action_id in action_ids or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", action_id) or action["kind"] not in ACTION_KINDS:
                raise ValueError("duplicate/invalid action or unsupported action kind")
            text(action["description"], "concrete action description")
            for context_path in texts(action["context_paths"], "action context_paths"):
                if not Path(context_path).is_absolute() or Path(context_path).resolve() not in bound:
                    raise ValueError("action references an unbound context path")
            dependencies = texts(action["requires"], "requires", empty=True)
            if not set(dependencies) <= set(action_ids):
                raise ValueError("dependencies must name earlier fixed actions")
            if action["phase"] == "required":
                if action["condition"] is not None or not set(dependencies) <= required_ids:
                    raise ValueError("required actions cannot depend on optional branches")
                required_ids.add(action_id)
            elif action["phase"] == "extension":
                if not dependencies:
                    raise ValueError("extension requires a completed predecessor")
                text(action["condition"], "extension evidence condition")
            else:
                raise ValueError("unknown action phase")
            fields(action["estimate"], " ".join(RESOURCES), "estimate")
            action_seconds = action["estimate"]["seconds"]
            if action_seconds is not None and integer(action_seconds, "action seconds") > budgets["max_action_seconds"]:
                blockers.append("max_action_seconds")
            for resource in RESOURCES:
                value = action["estimate"][resource]
                if value is None:
                    blockers.append("unknown_" + resource)
                else:
                    totals[resource] += integer(value, resource)
            action_ids.append(action_id)
        if not required_ids:
            raise ValueError("option must include a required action")
        total_seconds = totals["seconds"] + budgets["closeout_seconds"]
        if total_seconds > min(budgets["runtime_seconds"], int((expires - now).total_seconds())):
            blockers.append("runtime_seconds")
        for resource in RESOURCES[1:]:
            if totals[resource] > budgets[resource]:
                blockers.append(resource)
        if totals["cost_minor"] and not budgets["cost_metered"]:
            blockers.append("unmetered_cost")
        checked.append({"id": option_id, "deliverables": option["deliverables"], "reason": option["reason"],
                        "write_scope": option["write_scope"], "new_permissions": option["new_permissions"],
                        "action_ids": action_ids, "estimate_totals": totals,
                        "estimated_seconds_including_closeout": total_seconds,
                        "fits_budget": not blockers, "blockers": sorted(set(blockers))})
    if card["recommended_option"] not in option_ids:
        raise ValueError("recommendation is not a declared option")
    return checked, expires


V2_ACTION_KINDS = {"inspect", "recompute", "candidate_patch", "test", "draft", "independent_review"}


def check_bound_path(value, root, bound, label):
    path = Path(text(value, label))
    if not path.is_absolute():
        raise ValueError(label + " must be absolute")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved not in bound:
        raise ValueError(label + " is outside project or not hash-bound")
    return resolved


def inspect_card_v2(card, now, *, existing_state=None):
    fields(card, "schema project_root run_id revision previous_card_sha256 goal expires_at_utc context budgets prohibited stop_conditions recommended_option options", "card")
    if card["schema"] != "research-session/start-card/v2":
        raise ValueError("unknown startup card schema")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", text(card["run_id"], "run_id")):
        raise ValueError("invalid run_id")
    integer(card["revision"], "revision", minimum=1)
    if card["revision"] == 1:
        if card["previous_card_sha256"] is not None:
            raise ValueError("first card revision cannot have a parent")
    elif not re.fullmatch(r"[0-9a-f]{64}", str(card["previous_card_sha256"])):
        raise ValueError("amended card must bind the previous card SHA")
    text(card["goal"], "goal")
    expires = deadline(card["expires_at_utc"])
    if expires <= now:
        raise ValueError("approval validity expired")
    root = Path(text(card["project_root"], "project_root"))
    if not root.is_absolute() or str(root).startswith(("\\\\", "//")) or not root.is_dir():
        raise ValueError("project_root must be an existing absolute local directory")
    if root != root.resolve():
        raise ValueError("project_root must name the canonical project directory")
    if not isinstance(card["context"], list) or not card["context"]:
        raise ValueError("context must bind current project state and task evidence")
    bound = {}
    for item in card["context"]:
        fields(item, "path sha256", "context item")
        path = Path(text(item["path"], "context path"))
        if not path.is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError("context path is outside project")
        path = path.resolve()
        if path in bound or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
            raise ValueError("duplicate context or invalid context SHA")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                raise ValueError("context drift: " + str(path))
        bound[path] = item["sha256"]
    if (root / "README.md").resolve() not in bound:
        raise ValueError("current project README is not bound")
    budgets = card["budgets"]
    budget_fields = "max_action_seconds gpu_seconds disk_bytes cost_soft_limit_cny_fen currency"
    fields(budgets, budget_fields + (" rate_cny_fen_per_million" if "rate_cny_fen_per_million" in budgets else ""), "budgets")
    if "rate_cny_fen_per_million" in budgets:
        rates = budgets["rate_cny_fen_per_million"]
        fields(rates, "input cached_input output", "user-provided CNY token rates")
        for key, rate in rates.items():
            integer(rate, key + " rate")
    integer(budgets["max_action_seconds"], "max_action_seconds", minimum=1)
    integer(budgets["gpu_seconds"], "gpu_seconds")
    integer(budgets["disk_bytes"], "disk_bytes")
    integer(budgets["cost_soft_limit_cny_fen"], "cost_soft_limit_cny_fen", minimum=1)
    if budgets["currency"] != "CNY":
        raise ValueError("v2 cost soft gate is denominated in CNY fen")
    texts(card["prohibited"], "prohibited")
    texts(card["stop_conditions"], "stop_conditions")
    options = card["options"]
    if not isinstance(options, list) or not 1 <= len(options) <= 3:
        raise ValueError("provide one to three concrete options")
    checked, option_ids = [], set()
    for option in options:
        fields(option, "id deliverables reason write_scope new_permissions state_path actions", "option")
        option_id = text(option["id"], "option id")
        if option_id in option_ids or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", option_id):
            raise ValueError("duplicate or invalid option id")
        option_ids.add(option_id)
        texts(option["deliverables"], "deliverables")
        text(option["reason"], "recommendation reason")
        write_scope = texts(option["write_scope"], "write_scope", empty=True)
        for scope in write_scope:
            relative = Path(scope)
            if relative.anchor or not relative.parts or ".." in relative.parts or relative.as_posix() != scope:
                raise ValueError("write scopes must be canonical project-relative directories")
            local_path(root / relative, root, must_exist=False)
        texts(option["new_permissions"], "new_permissions", empty=True)
        state_rel = Path(text(option["state_path"], "state_path"))
        if state_rel.is_absolute() or ".." in state_rel.parts or not any(
                state_rel.as_posix().startswith(scope.rstrip("/") + "/") for scope in write_scope):
            raise ValueError("state_path must be inside the approved write_scope")
        state_path = local_path(root / state_rel, root, must_exist=False, regular=True)
        if not state_path.is_relative_to(root) or (state_path.exists() and state_path != existing_state):
            raise ValueError("state_path escapes project or already exists")
        for parent in [state_path.parent, *state_path.parent.parents]:
            if parent == root.parent:
                break
            if parent.exists() and parent.is_symlink():
                raise ValueError("state_path parent cannot be a symlink")
        if not isinstance(option["actions"], list) or not option["actions"]:
            raise ValueError("option has no fixed actions")
        checked_actions, ids, required_ids = [], [], set()
        totals = {"gpu_seconds": 0, "disk_bytes": 0, "cost_cny_fen": 0, "model_calls": 0}
        unknown = set()
        review_rounds, review_ids = {}, {}
        by_id = {}
        for action in option["actions"]:
            fields(action, "id kind description context_paths phase requires condition continue_on_review_failure review paid estimate watchdog_seconds operation outputs", "action")
            action_id = text(action["id"], "action id")
            if action_id in ids or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", action_id) or action["kind"] not in V2_ACTION_KINDS:
                raise ValueError("duplicate/invalid action or unsupported registered action kind")
            text(action["description"], "action description")
            for context_path in texts(action["context_paths"], "action context_paths"):
                check_bound_path(context_path, root, bound, "action context path")
            dependencies = texts(action["requires"], "requires", empty=True)
            if not set(dependencies) <= set(ids):
                raise ValueError("dependencies must name earlier fixed actions")
            if action["phase"] == "required":
                if action["condition"] is not None or not set(dependencies) <= required_ids:
                    raise ValueError("required actions cannot depend on optional branches")
                required_ids.add(action_id)
            elif action["phase"] == "extension":
                if not dependencies:
                    raise ValueError("extension requires a completed predecessor")
                condition = action["condition"]
                fields(condition, "action result", "extension condition")
                if condition["action"] not in dependencies or condition["result"] not in {"succeeded", "review_not_passed"}:
                    raise ValueError("extension condition must identify a fixed predecessor and result")
                if condition["result"] == "review_not_passed" and (
                    not action["continue_on_review_failure"] or by_id[condition["action"]]["kind"] != "independent_review"
                ):
                    raise ValueError("review failure condition needs an explicitly independent continuation")
            else:
                raise ValueError("unknown action phase")
            if type(action["continue_on_review_failure"]) is not bool or type(action["paid"]) is not bool:
                raise ValueError("action continuation and paid flags must be booleans")
            if action["continue_on_review_failure"] and action["kind"] not in {"independent_review", "candidate_patch", "test", "draft"}:
                raise ValueError("review-failure continuation is not valid for this action kind")
            if action["continue_on_review_failure"] and (action["phase"] != "extension" or action["condition"]["result"] != "review_not_passed"):
                raise ValueError("continuation must designate the exact non-pass it consumes")
            review = action["review"]
            if action["kind"] == "independent_review":
                fields(review, "review_id point round pack_path inputs_path previous_action verification_action prepare_action transport max_output_tokens", "review action")
                text(review["review_id"], "review_id")
                if type(review["point"]) is not int or review["point"] not in (1, 2, 3):
                    raise ValueError("review point must be 1, 2, or 3")
                if review_ids.setdefault(review["point"], review["review_id"]) != review["review_id"]:
                    raise ValueError("cannot change a review object id within one point of a task package")
                round_number = integer(review["round"], "review round", minimum=1)
                if round_number > 3:
                    raise ValueError("unattended review rounds cannot exceed three")
                key = (review["review_id"], review["point"])
                rounds = review_rounds.setdefault(key, [])
                if (round_number != len(rounds) + 1 or review["previous_action"] != (rounds[-1] if rounds else None)
                    or (rounds and (action["phase"] != "extension" or rounds[-1] not in dependencies
                                   or action["condition"] != {"action": rounds[-1], "result": "review_not_passed"}))):
                    raise ValueError("review rounds must be ordered and depend on the exact preceding non-pass")
                verification = by_id.get(review["verification_action"])
                if (rounds and (verification is None or verification["kind"] != "test" or
                                verification["id"] not in dependencies or rounds[-1] not in verification["requires"] or
                                not verification["continue_on_review_failure"] or not verification["operation"] or
                                verification["operation"]["adapter"] != "json-match")) or (not rounds and review["verification_action"] is not None):
                    raise ValueError("incremental review requires an earlier local verification of the exact prior non-pass")
                if (not action["paid"] or review["transport"] not in {"responses"} or
                    not 1 <= integer(review["max_output_tokens"], "review output cap") <= 100000):
                    raise ValueError("review requires an explicit paid transport and output cap")
                for key_name in ("pack_path", "inputs_path"):
                    relative = Path(text(review[key_name], key_name))
                    if (relative.is_absolute() or relative.anchor or ".." in relative.parts or
                        not any(relative.as_posix().startswith(scope.rstrip("/") + "/") for scope in write_scope)):
                        raise ValueError("review pack and manifest must be in the approved write scope")
                prepared = by_id.get(review["prepare_action"])
                if (prepared is None or prepared["kind"] != "draft" or prepared["id"] not in dependencies or
                    not {review["pack_path"], review["inputs_path"]} <= set(prepared["outputs"])):
                    raise ValueError("review pack and manifest require a fixed predecessor with exact output paths")
                if verification and not verification["outputs"]:
                    raise ValueError("incremental review must consume newly recorded verification evidence")
                if any(a["review"] and a["review"]["pack_path"] == review["pack_path"] for a in by_id.values()):
                    raise ValueError("each review action needs its own pack")
                rounds.append(action_id)
            elif review is not None:
                raise ValueError("only registered review actions may bind review metadata")
            estimate = action["estimate"]
            fields(estimate, "duration gpu model_calls input_tokens output_tokens cost_cny_fen cost_basis disk_bytes", "estimate")
            duration = estimate["duration"]
            fields(duration, "min_seconds max_seconds basis evidence_path evidence_sha256", "duration estimate")
            basis = duration["basis"]
            if basis not in {"conservative", "historical", "unknown"}:
                raise ValueError("duration basis must be conservative, historical, or unknown")
            if basis == "unknown":
                if duration["min_seconds"] is not None or duration["max_seconds"] is not None:
                    raise ValueError("unknown duration cannot contain invented numeric estimates")
            else:
                low = integer(duration["min_seconds"], "duration min_seconds", minimum=1)
                high = integer(duration["max_seconds"], "duration max_seconds", minimum=low)
                if basis == "historical":
                    evidence = check_bound_path(duration["evidence_path"], root, bound, "duration evidence")
                    if duration["evidence_sha256"] != bound[evidence]:
                        raise ValueError("historical duration estimate must bind evidence SHA")
                elif duration["evidence_path"] is not None or duration["evidence_sha256"] is not None:
                    raise ValueError("conservative estimate cannot mislabel historical evidence")
            watchdog = integer(action["watchdog_seconds"], "watchdog_seconds", minimum=1)
            if watchdog > budgets["max_action_seconds"]:
                raise ValueError("watchdog exceeds project max_action_seconds")
            if basis == "unknown":
                if watchdog != budgets["max_action_seconds"]:
                    raise ValueError("unknown duration must use the project action limit as watchdog")
                unknown.add("duration:" + action_id)
            elif basis == "conservative" and watchdog < high * 3:
                raise ValueError("watchdog must be about three times the conservative upper estimate")
            elif basis == "historical" and watchdog < high:
                raise ValueError("watchdog cannot be shorter than the historical upper estimate")
            gpu = estimate["gpu"]
            fields(gpu, "status device count min_seconds max_seconds basis evidence_path evidence_sha256", "GPU estimate")
            if gpu["status"] == "not_applicable":
                if gpu["count"] != 0 or gpu["min_seconds"] != 0 or gpu["max_seconds"] != 0:
                    raise ValueError("not-applicable GPU estimate must be zero")
            elif gpu["status"] == "unknown":
                if gpu["count"] is not None or gpu["min_seconds"] is not None or gpu["max_seconds"] is not None:
                    raise ValueError("unknown GPU usage cannot contain invented numbers")
                text(gpu["basis"], "unknown GPU estimate basis")
                unknown.add("gpu_duration:" + action_id)
            elif gpu["status"] == "estimated":
                text(gpu["device"], "GPU device")
                integer(gpu["count"], "GPU count", minimum=1)
                low = integer(gpu["min_seconds"], "GPU min_seconds", minimum=0)
                high = integer(gpu["max_seconds"], "GPU max_seconds", minimum=low)
                text(gpu["basis"], "GPU estimate basis")
                if gpu["evidence_path"] is not None:
                    evidence = check_bound_path(gpu["evidence_path"], root, bound, "GPU evidence")
                    if gpu["evidence_sha256"] != bound[evidence]:
                        raise ValueError("GPU evidence SHA is not bound")
                totals["gpu_seconds"] += high * gpu["count"]
            else:
                raise ValueError("unknown GPU estimate status")
            for key in ("model_calls", "disk_bytes"):
                totals[key] += integer(estimate[key], key)
            for key in ("input_tokens", "output_tokens"):
                if estimate[key] is not None:
                    integer(estimate[key], key)
            cost = estimate["cost_cny_fen"]
            if cost is None:
                text(estimate["cost_basis"], "unknown cost basis")
                unknown.add("cost:" + action_id)
            else:
                totals["cost_cny_fen"] += integer(cost, "cost_cny_fen")
                text(estimate["cost_basis"], "cost estimate basis")
            operation = action["operation"]
            outputs = texts(action["outputs"], "exact action outputs", empty=True)
            for path in outputs:
                relative = Path(path)
                if (relative.anchor or ".." in relative.parts or relative.as_posix() != path or
                    not any(path.startswith(scope.rstrip("/") + "/") for scope in write_scope)):
                    raise ValueError("action output is outside the approved write scope")
                local_path(root / relative, root, must_exist=False, regular=True)
            if set(outputs).intersection(out for prior in by_id.values() for out in prior["outputs"]):
                raise ValueError("action outputs must have one producer")
            if action["kind"] == "independent_review":
                if operation is not None or outputs or estimate["model_calls"] != 1:
                    raise ValueError("review adapter uses fixed metadata and exactly one call")
            elif operation is not None:
                is_match = isinstance(operation, dict) and operation.get("adapter") == "json-match"
                fields(operation, "adapter source destination" + (" expected finding_ids" if is_match else ""), "fixed operation")
                if operation["adapter"] not in {"hash", "copy", "json-check", "json-sum", "json-match"}:
                    raise ValueError("unknown local adapter")
                if is_match:
                    if (action["kind"] != "test" or not action["continue_on_review_failure"] or
                            operation["expected"] not in action["context_paths"] or
                            not any(by_id[dep]["kind"] == "candidate_patch" and
                                    operation["source"] in by_id[dep]["outputs"] for dep in dependencies)):
                        raise ValueError("json-match requires an approved expectation and an explicit prior candidate output")
                    texts(operation["finding_ids"], "fixed findings to verify")
                elif operation["source"] not in action["context_paths"]:
                    raise ValueError("fixed operation source must be hash-bound to this action")
                dest = Path(text(operation["destination"], "operation destination"))
                if (dest.is_absolute() or dest.anchor or ".." in dest.parts or
                    not any(dest.as_posix().startswith(scope.rstrip("/") + "/") for scope in write_scope)):
                    raise ValueError("operation destination is outside the approved write scope")
                local_path(root / dest, root, must_exist=False, regular=True)
                if outputs != [dest.as_posix()]:
                    raise ValueError("fixed local output must match its single registered destination")
                if action["paid"] or estimate["model_calls"] or gpu["status"] != "not_applicable":
                    raise ValueError("fixed local operation cannot consume model calls or GPU")
            by_id[action_id] = action
            ids.append(action_id)
            checked_actions.append({"id": action_id, "kind": action["kind"], "phase": action["phase"],
                                    "requires": dependencies, "condition": action["condition"],
                                    "continue_on_review_failure": action["continue_on_review_failure"],
                                    "review": review, "operation": operation, "outputs": outputs, "paid": action["paid"], "watchdog_seconds": watchdog,
                                    "gpu_estimate": gpu, "estimate": estimate})
        if not required_ids:
            raise ValueError("option must include a required action")
        blockers = []
        if totals["gpu_seconds"] > budgets["gpu_seconds"]:
            blockers.append("gpu_seconds")
        if totals["disk_bytes"] > budgets["disk_bytes"]:
            blockers.append("disk_bytes")
        threshold = budgets["cost_soft_limit_cny_fen"]
        warning = totals["cost_cny_fen"] >= (threshold * 80 // 100) or totals["cost_cny_fen"] > threshold
        checked.append({"id": option_id, "deliverables": option["deliverables"], "reason": option["reason"],
                        "write_scope": write_scope, "state_path": str(state_path),
                        "new_permissions": option["new_permissions"], "actions": checked_actions,
                        "action_ids": ids, "estimate_totals": totals, "cost_soft_warning": warning,
                        "unknown_estimates": sorted(unknown), "fits_budget": not blockers,
                        "blockers": blockers})
    if card["recommended_option"] not in option_ids:
        raise ValueError("recommendation is not a declared option")
    return checked, expires


def local_path(path, root, *, must_exist=True, regular=False):
    path, root = Path(path), Path(root)
    if not path.is_absolute():
        path = root / path
    if ".." in path.parts or not path.is_relative_to(root):
        raise ValueError("path leaves the project root")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("project path cannot contain a link or junction")
        if ":" in part.name:
            raise ValueError("project path cannot contain an alternate stream")
    if (must_exist or path.exists()) and (not path.is_file() if regular else not path.exists()):
        raise ValueError("project path is missing or of the wrong type")
    if regular and path.exists() and path.stat().st_nlink != 1:
        raise ValueError("project file cannot have multiple hard links")
    return path


def inspect_card(card, now):
    if isinstance(card, dict) and card.get("schema") == "research-session/start-card/v2":
        return inspect_card_v2(card, now)
    return inspect_card_v1(card, now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", required=True)
    parser.add_argument("--choice")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    result = {"schema": "research-session/start-preview/v1", "state": "invalid_card",
              "selected_option": None, "confirmation_binding_matches": False,
              "authorization_verified": False, "execution_enabled": False, "gate_passed": False,
              "limitations": ["Card consistency, estimates and a matching local record do not authenticate human approval.",
                              "Cost metering is declared, not verified; no resource cap is enforced by this checker.",
                              "This command only checks a card; it does not execute actions."]}
    try:
        raw, card = load_json(args.card)
        now = datetime.now(timezone.utc)
        options, expires = inspect_card(card, now)
        is_v2 = card.get("schema") == "research-session/start-card/v2"
        if is_v2:
            result.update(schema="research-session/start-preview/v2", controller_available=True, controller_scope="offline_fixture_only",
                          limitations=["The local record does not authenticate human approval.",
                                        "The run controller is workflow governance, not an OS sandbox; host tools must honor each watchdog.",
                                        "Cost is estimated or unknown, not a guaranteed bill or hard cap."])
        result.update(card_sha256=hashlib.sha256(raw).hexdigest(), options=options,
                      recommended_option=card["recommended_option"], state="awaiting_choice")
        if args.choice:
            selected = next((option for option in options if option["id"] == args.choice), None)
            if selected is None or not selected["fits_budget"]:
                raise ValueError("selected option is unknown or exceeds available budget")
            result.update(selected_option=args.choice, state="awaiting_confirmation")
        if args.confirmation:
            _, record = load_json(args.confirmation)
            fields(record, "schema card_sha256 option_id expires_at_utc revoked", "confirmation")
            expected_confirmation = ("research-session/start-selection/v2" if is_v2
                                     else "research-session/start-selection/v1")
            if (not args.choice or record["schema"] != expected_confirmation
                    or record["card_sha256"] != result["card_sha256"] or record["option_id"] != args.choice
                    or type(record["revoked"]) is not bool or record["revoked"]
                    or not now < deadline(record["expires_at_utc"]) <= expires):
                raise ValueError("confirmation is stale, revoked, unselected or bound to another card/option")
            result.update(confirmation_binding_matches=True, state="preparation_complete")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result.update(state="invalid_card", error=str(exc))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "preparation_complete" else 2


if __name__ == "__main__":
    sys.exit(main())
