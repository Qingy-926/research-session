"""In-session unattended guard: bind an approved plan and track action state.

The guard only validates records and moves a state file forward inside the
currently open session. It never starts a background process, opens a network
connection or executes a plan action; closing the session stops all progress.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PLAN_SCHEMA = "research-session/session-plan/v1"
APPROVAL_SCHEMA = "research-session/session-approval/v1"
STATE_SCHEMA = "research-session/session-state/v1"
PHASES = ("required", "extension")
MAX_BYTES = 65536
HEX = re.compile(r"[0-9a-f]{64}")


class GuardError(ValueError):
    pass


def emit(value, code=0):
    print(json.dumps(value, ensure_ascii=True))
    return code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        emit({"status": "rejected", "error": message}, 2)
        raise SystemExit(2)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GuardError("duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    path = Path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise GuardError("cannot read %s: %s" % (path, exc)) from exc
    if len(raw) > MAX_BYTES:
        raise GuardError("%s exceeds %d bytes" % (path, MAX_BYTES))
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              GuardError("nonfinite JSON number: " + value)))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError("%s is not valid UTF-8 JSON" % path) from exc
    return raw, data


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def plan_digest(plan):
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    return digest(canonical(payload).encode("utf-8"))


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise GuardError(label + " has missing or unknown fields")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GuardError(label + " must be nonempty text")
    return value


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise GuardError(label + " must be an integer >= " + str(minimum))
    return value


def timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GuardError(label + " must be an absolute timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GuardError(label + " must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GuardError(label + " requires a timezone")
    return parsed.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def now_text():
    return now().isoformat()


def sha_field(value, label):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise GuardError(label + " must be a lowercase SHA256")
    return value


def validate_plan(plan):
    fields(plan, ("schema", "task_id", "project_id", "plan_sha256", "actions", "deadline",
                  "max_requests", "reserved_requests", "closeout_seconds"), "plan")
    if plan["schema"] != PLAN_SCHEMA:
        raise GuardError("unknown plan schema")
    text(plan["task_id"], "plan task_id")
    text(plan["project_id"], "plan project_id")
    sha_field(plan["plan_sha256"], "plan plan_sha256")
    computed = plan_digest(plan)
    if plan["plan_sha256"] != computed:
        raise GuardError("plan hash drift: plan_sha256 does not match the plan body")
    deadline = timestamp(plan["deadline"], "plan deadline")
    max_requests = integer(plan["max_requests"], "max_requests")
    reserved = integer(plan["reserved_requests"], "reserved_requests")
    closeout = integer(plan["closeout_seconds"], "closeout_seconds")
    if max_requests <= reserved:
        raise GuardError("max_requests must exceed reserved_requests")
    actions = plan["actions"]
    if not isinstance(actions, list) or not actions:
        raise GuardError("plan actions must be a nonempty list")
    seen = {}
    required_seconds = 0
    for action in actions:
        fields(action, ("id", "kind", "description", "seconds", "requires", "phase"), "action")
        action_id = text(action["id"], "action id")
        if action_id in seen:
            raise GuardError("duplicate action id: " + action_id)
        text(action["kind"], "action kind")
        text(action["description"], "action description")
        seconds = integer(action["seconds"], "action seconds")
        if action["phase"] not in PHASES:
            raise GuardError("action phase must be required or extension: " + action_id)
        requires = action["requires"]
        if not isinstance(requires, list) or any(not isinstance(item, str) or not item.strip()
                                                for item in requires):
            raise GuardError("action requires must be a text list: " + action_id)
        if len(set(requires)) != len(requires):
            raise GuardError("action repeats a dependency: " + action_id)
        for dependency in requires:
            if dependency not in seen:
                raise GuardError("dependency must point to an earlier action: " + action_id)
            if action["phase"] == "required" and seen[dependency]["phase"] == "extension":
                raise GuardError("required action cannot depend on an extension: " + action_id)
        seen[action_id] = action
        if action["phase"] == "required":
            required_seconds += seconds
    return computed, deadline, required_seconds


def validate_approval(approval, expected_digest, task_id, approval_path):
    required = {"schema", "task_id", "plan_sha256", "approved_at", "expires_at",
                "source", "revoked"}
    optional = {"threadId", "verifiedByHost"}
    if not isinstance(approval, dict) or not required <= set(approval) \
            or not set(approval) <= required | optional:
        raise GuardError("approval has missing or unknown fields")
    if "threadId" in approval:
        text(approval["threadId"], "approval threadId")
    if "verifiedByHost" in approval and approval["verifiedByHost"] is not True:
        raise GuardError("approval verifiedByHost must be true")
    if approval["schema"] != APPROVAL_SCHEMA:
        raise GuardError("unknown approval schema")
    if text(approval["task_id"], "approval task_id") != task_id:
        raise GuardError("approval task_id does not bind this plan")
    if sha_field(approval["plan_sha256"], "approval plan_sha256") != expected_digest:
        raise GuardError("approval does not bind this plan hash")
    if approval["revoked"] is not False:
        raise GuardError("approval is revoked")
    approved = timestamp(approval["approved_at"], "approved_at")
    expires = timestamp(approval["expires_at"], "expires_at")
    if approved >= expires:
        raise GuardError("approval expires_at must be after approved_at")
    source = approval["source"]
    fields(source, ("path", "sha256"), "approval source")
    source_path = Path(text(source["path"], "approval source path"))
    if not source_path.is_absolute():
        raise GuardError("approval source path must be absolute")
    if os.path.normcase(os.path.realpath(source_path)) == os.path.normcase(
            os.path.realpath(approval_path)):
        raise GuardError("approval source must be a separate user decision record")
    if not source_path.is_file():
        raise GuardError("approval source is missing or not a regular file")
    if digest(source_path.read_bytes()) != sha_field(source["sha256"], "approval source sha256"):
        raise GuardError("approval source sha256 does not match the file")
    return expires


def write_state(path, state):
    path = Path(path)
    if path.parent.is_symlink():
        raise GuardError("state directory cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                         suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_state(path):
    _, state = read_json(path)
    if state.get("schema") != STATE_SCHEMA:
        raise GuardError("unknown session state schema")
    if state.get("status") not in ("running", "paused", "stopped"):
        raise GuardError("session state has an unknown status")
    for key in ("task_id", "plan_sha256", "deadline", "approval_expires_at", "order", "actions"):
        if key not in state:
            raise GuardError("session state is missing " + key)
    return state


def touch(state, event, action=None, detail=None):
    state["updated_at"] = now_text()
    state["events"].append({"at": state["updated_at"], "event": event,
                            "action": action, "detail": detail})


def require_active(state):
    status = state["status"]
    if status == "paused":
        raise GuardError("session is paused; no action may run")
    if status != "running":
        raise GuardError("session is not running: " + status)


def require_running(state):
    require_active(state)
    moment = now()
    if moment >= timestamp(state["deadline"], "deadline"):
        raise GuardError("plan deadline exceeded")
    if moment >= timestamp(state["approval_expires_at"], "approval expires_at"):
        raise GuardError("approval expired")


def verify_bindings(state):
    _, plan = read_json(state["plan"]["path"])
    computed, _, _ = validate_plan(plan)
    if computed != state["plan_sha256"]:
        raise GuardError("plan hash drift: the plan no longer matches the approved plan")
    if plan["task_id"] != state["task_id"]:
        raise GuardError("plan task_id drifted from the running session")
    _, approval = read_json(state["approval"]["path"])
    validate_approval(approval, state["plan_sha256"], state["task_id"],
                      state["approval"]["path"])
    state["approval_expires_at"] = approval["expires_at"]


def action_of(state, action_id):
    action = state["actions"].get(action_id)
    if action is None:
        raise GuardError("action is not in the plan: " + str(action_id))
    return action


def remaining_required(state, skip=None):
    return sum(
        action["seconds"]
        for action in state["actions"].values()
        if action["phase"] == "required"
        and action["status"] != "completed"
        and action["id"] != skip
    )


def ensure_time(state, action):
    budget = (timestamp(state["deadline"], "deadline") - now()).total_seconds()
    needed = action["seconds"] + remaining_required(state, action["id"]) + state["closeout_seconds"]
    if budget < needed:
        raise GuardError("insufficient time budget for action: " + action["id"])


def ready_action(state, action_id):
    action = action_of(state, action_id)
    if action["status"] != "pending":
        if action["status"] == "in_flight":
            raise GuardError("action already claimed: " + action_id)
        raise GuardError("action is already %s: %s" % (action["status"], action_id))
    blocked = [dependency for dependency in action["requires"]
               if state["actions"][dependency]["status"] != "completed"]
    if blocked:
        raise GuardError("dependencies not satisfied for action: " + action_id)
    return action


def next_action(state):
    for action_id in state["order"]:
        action = state["actions"][action_id]
        if action["status"] != "pending":
            continue
        if all(state["actions"][dependency]["status"] == "completed"
               for dependency in action["requires"]):
            ensure_time(state, action)
            return action
    return None


def public_action(action):
    return {key: action[key] for key in ("id", "kind", "description", "seconds",
                                         "requires", "phase", "status")}


def start(args):
    raw_plan, plan = read_json(args.plan)
    _, approval = read_json(args.approval)
    computed, deadline, required_seconds = validate_plan(plan)
    expires = validate_approval(approval, computed, plan["task_id"], args.approval)
    if now() >= deadline:
        raise GuardError("plan deadline has already passed")
    if now() >= expires:
        raise GuardError("approval expired")
    if required_seconds + plan["closeout_seconds"] > (deadline - now()).total_seconds():
        raise GuardError("insufficient time budget for required actions and closeout")
    state_path = Path(args.state)
    if state_path.exists():
        raise GuardError("state file already exists; start cannot reset a session")
    for bound in (args.plan, args.approval):
        if os.path.normcase(os.path.realpath(bound)) == os.path.normcase(
                os.path.realpath(state_path)):
            raise GuardError("state path must differ from the plan and approval records")
    state = {
        "schema": STATE_SCHEMA,
        "task_id": plan["task_id"],
        "project_id": plan["project_id"],
        "plan_sha256": computed,
        "plan": {"path": str(Path(args.plan).resolve()), "sha256": digest(raw_plan)},
        "approval": {"path": str(Path(args.approval).resolve())},
        "status": "running",
        "created_at": now_text(),
        "updated_at": now_text(),
        "deadline": plan["deadline"],
        "approval_expires_at": approval["expires_at"],
        "max_requests": plan["max_requests"],
        "reserved_requests": plan["reserved_requests"],
        "closeout_seconds": plan["closeout_seconds"],
        "order": [action["id"] for action in plan["actions"]],
        "actions": {action["id"]: dict(action, status="pending", attempts=0,
                                       evidence=None, reason=None, claimed_at=None,
                                       completed_at=None)
                    for action in plan["actions"]},
        "events": [],
    }
    touch(state, "started", detail=computed)
    write_state(state_path, state)
    return emit({"status": "started", "state": state["status"], "task_id": state["task_id"],
                 "plan_sha256": computed, "state_path": str(state_path),
                 "actions": list(state["order"]), "execution_enabled": False})


def cmd_next(args):
    state = load_state(args.state)
    require_active(state)
    verify_bindings(state)
    require_running(state)
    action = next_action(state)
    if action is None:
        pending = [item for item in state["order"] if state["actions"][item]["status"] != "completed"]
        if pending:
            return emit({"status": "stop", "reason": "no_ready_action", "pending": pending})
        return emit({"status": "stop", "reason": "all_actions_complete"})
    budget = int((timestamp(state["deadline"], "deadline") - now()).total_seconds())
    return emit({"status": "ready", "action": public_action(action),
                 "remaining_seconds": budget, "execution_enabled": False})


def cmd_claim(args):
    state = load_state(args.state)
    require_active(state)
    verify_bindings(state)
    require_running(state)
    action = ready_action(state, args.action)
    ensure_time(state, action)
    action["status"] = "in_flight"
    action["attempts"] += 1
    action["claimed_at"] = now_text()
    touch(state, "claimed", args.action, action["claimed_at"])
    write_state(args.state, state)
    return emit({"status": "claimed", "action": public_action(action),
                 "attempts": action["attempts"], "execution_enabled": False})


def cmd_complete(args):
    state = load_state(args.state)
    if state["status"] == "paused":
        raise GuardError("session is paused; no action may run")
    if state["status"] != "running":
        raise GuardError("session is not running: " + state["status"])
    action = action_of(state, args.action)
    if action["status"] != "in_flight":
        raise GuardError("only a claimed action can complete: " + args.action)
    evidence = text(args.evidence, "evidence")
    action["status"] = "completed"
    action["evidence"] = evidence
    action["completed_at"] = now_text()
    touch(state, "completed", args.action, evidence)
    write_state(args.state, state)
    return emit({"status": "completed", "action": public_action(action),
                 "evidence": evidence,
                 "all_actions_complete": all(item["status"] == "completed"
                                             for item in state["actions"].values())})


def cmd_fail(args):
    state = load_state(args.state)
    action = action_of(state, args.action)
    if action["status"] != "in_flight":
        raise GuardError("only a claimed action can fail: " + args.action)
    reason = text(args.reason, "reason")
    action["status"] = "failed"
    action["reason"] = reason
    state["status"] = "stopped"
    state["stop_reason"] = "action_failed: %s: %s" % (args.action, reason)
    touch(state, "failed", args.action, reason)
    write_state(args.state, state)
    return emit({"status": "stop", "reason": state["stop_reason"],
                 "action": public_action(action), "execution_enabled": False})


def cmd_pause(args):
    state = load_state(args.state)
    if state["status"] != "running":
        raise GuardError("only a running session can pause: " + state["status"])
    reason = text(args.reason, "pause reason")
    state["status"] = "paused"
    state["pause_reason"] = reason
    touch(state, "paused", detail=reason)
    write_state(args.state, state)
    return emit({"status": "paused", "reason": reason, "state": "paused"})


def cmd_status(args):
    state = load_state(args.state)
    moment = now()
    counts = {key: 0 for key in ("pending", "in_flight", "completed", "failed")}
    for action in state["actions"].values():
        counts[action["status"]] = counts.get(action["status"], 0) + 1
    deadline = timestamp(state["deadline"], "deadline")
    return emit({
        "state": state["status"],
        "task_id": state["task_id"],
        "project_id": state["project_id"],
        "plan_sha256": state["plan_sha256"],
        "deadline": state["deadline"],
        "remaining_seconds": int((deadline - moment).total_seconds()),
        "expired": moment >= deadline,
        "counts": counts,
        "actions": [{"id": action_id, "phase": state["actions"][action_id]["phase"],
                     "status": state["actions"][action_id]["status"]}
                    for action_id in state["order"]],
        "stop_reason": state.get("stop_reason"),
        "pause_reason": state.get("pause_reason"),
        "execution_enabled": False,
    })


def build_parser():
    parser = Parser(prog="session_guard.py",
                    description="Bind an approved session plan and guard in-session progress.")
    commands = parser.add_subparsers(dest="command", required=True)

    start_parser = commands.add_parser("start")
    start_parser.add_argument("--plan", required=True)
    start_parser.add_argument("--approval", required=True)
    start_parser.add_argument("--state", required=True)
    start_parser.set_defaults(handler=start)

    for name, handler in (("next", cmd_next), ("status", cmd_status)):
        sub = commands.add_parser(name)
        sub.add_argument("--state", required=True)
        sub.set_defaults(handler=handler)

    claim = commands.add_parser("claim")
    claim.add_argument("--state", required=True)
    claim.add_argument("--action", required=True)
    claim.set_defaults(handler=cmd_claim)

    complete = commands.add_parser("complete")
    complete.add_argument("--state", required=True)
    complete.add_argument("--action", required=True)
    complete.add_argument("--evidence", required=True)
    complete.set_defaults(handler=cmd_complete)

    fail = commands.add_parser("fail")
    fail.add_argument("--state", required=True)
    fail.add_argument("--action", required=True)
    fail.add_argument("--reason", required=True)
    fail.set_defaults(handler=cmd_fail)

    pause = commands.add_parser("pause")
    pause.add_argument("--state", required=True)
    pause.add_argument("--reason", required=True)
    pause.set_defaults(handler=cmd_pause)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except GuardError as exc:
        return emit({"status": "rejected", "error": str(exc)}, 2)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return emit({"status": "rejected", "error": str(exc)}, 2)


if __name__ == "__main__":
    sys.exit(main())
