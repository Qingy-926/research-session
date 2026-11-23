"""Persist bounded unattended action state; never execute arbitrary commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import autonomy_start
import run_manifest


SCHEMA_VERSION = 1
TABLES = {
    "run": ["id", "run_id", "revision", "card_path", "card_sha256", "project_root", "option_id", "state", "cost_soft_limit_cny_fen", "created_at", "updated_at", "pause_reason", "card_json", "confirmation_path"],
    "actions": ["action_id", "position", "kind", "action_json", "state", "attempt", "claimed_at", "finished_at", "outcome", "evidence_path", "evidence_sha256", "gate_passed", "cost_cny_fen", "cost_source", "dispatch_used", "output_bytes", "outputs_json"],
    "events": ["seq", "at", "event", "action_id", "detail"],
}


class RunError(ValueError):
    pass


def now_text():
    return datetime.now(timezone.utc).isoformat()


LAST_EMITTED: dict = {}


def emit(value, code=0):
    LAST_EMITTED.clear()
    LAST_EMITTED.update(value)
    print(json.dumps(value, ensure_ascii=True))
    return code


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fixture_root(root):
    root = Path(root).resolve()
    configured = os.environ.get("RESEARCH_SESSION_FIXTURE_TEMP_ROOT")
    temporary = Path(configured).resolve() if configured else Path(tempfile.gettempdir()).resolve()
    valid_configured_root = (not configured or
                             (temporary.is_dir() and temporary != Path(temporary.anchor) and not temporary.is_symlink()))
    # 显式夹具根只接受直接子项；默认系统临时根沿用既有语义，允许 pytest 等嵌套临时目录。
    inside = root.parent == temporary if configured else root.is_relative_to(temporary)
    if not valid_configured_root or not inside or root.name != "project-999-fixture":
        raise RunError("only an explicitly selected temporary synthetic project is executable; trusted host approval is not integrated")
    return root


def controller_sha():
    digest = hashlib.sha256()
    for name in ("autonomy_start.py", "autonomy_run.py", "audit_dispatch.py", "review_ledger.py", "review_evidence.py"):
        digest.update(bytes.fromhex(sha256(Path(__file__).with_name(name))))
    return digest.hexdigest()


def sha256(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def read_card(path, existing_state=None):
    raw, card = autonomy_start.load_json(path)
    if card.get("schema") != "research-session/start-card/v2":
        raise RunError("runtime requires start-card/v2")
    fixture_root(card["project_root"])
    options, expires = autonomy_start.inspect_card_v2(card, datetime.now(timezone.utc), existing_state=existing_state)
    return raw, card, options, expires


def option_for(options, option_id):
    option = next((item for item in options if item["id"] == option_id), None)
    if option is None or not option["fits_budget"]:
        raise RunError("selected option is unknown or exceeds hard project resources")
    return option


def validate_confirmation(path, raw, option_id, expires):
    _, record = autonomy_start.load_json(path)
    autonomy_start.fields(record, "schema card_sha256 option_id expires_at_utc revoked", "confirmation")
    now = datetime.now(timezone.utc)
    if (record["schema"] != "research-session/start-selection/v2"
            or record["card_sha256"] != hashlib.sha256(raw).hexdigest()
            or record["option_id"] != option_id or type(record["revoked"]) is not bool
            or record["revoked"] or not now < autonomy_start.deadline(record["expires_at_utc"]) <= expires):
        raise RunError("confirmation does not bind the live card and selected option")


def db_connect(path, readonly=False):
    path = Path(path).absolute()
    if path != fixture_root(path.parent.parent.parent) / "runs/run-001/state.sqlite3" or path != path.resolve():
        raise RunError("fixture state path is fixed; refusing to open other files")
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise RunError("run state is missing or has an unsafe file identity")
    mode = "ro" if readonly else "rw"
    connection = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise RunError("unknown run-state schema; refusing rebuild")
        objects = connection.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if {row["name"] for row in objects} != set(TABLES) or any(row["type"] != "table" for row in objects):
            raise RunError("run-state tables do not match")
        for table, columns in TABLES.items():
            names = [row["name"] for row in connection.execute("PRAGMA table_info(" + table + ")")]
            if names != columns:
                raise RunError("run-state columns do not match")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RunError("run-state integrity check failed")
        run = run_row(connection)
        if path != fixture_root(run["project_root"]) / "runs/run-001/state.sqlite3":
            raise RunError("fixture state path is fixed; use the same run for continuation")
        option = next(item for item in json.loads(run["card_json"])["options"] if item["id"] == run["option_id"])
        if path != autonomy_start.local_path(option["state_path"], run["project_root"], regular=True):
            raise RunError("run state has moved or been aliased")
        return connection
    except Exception:
        connection.close()
        raise


def create_db(path, card_path, raw, card, option, confirmation_path):
    path = Path(path)
    if path.exists():
        raise RunError("run-state already exists; start cannot reset a run")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RunError("run-state directory cannot be a symlink")
    with path.open("xb"):
        pass
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
          CREATE TABLE run (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL, revision INTEGER NOT NULL,
            card_path TEXT NOT NULL, card_sha256 TEXT NOT NULL, project_root TEXT NOT NULL, option_id TEXT NOT NULL,
            state TEXT NOT NULL, cost_soft_limit_cny_fen INTEGER NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, pause_reason TEXT, card_json TEXT NOT NULL, confirmation_path TEXT NOT NULL);
          CREATE TABLE actions (action_id TEXT PRIMARY KEY, position INTEGER NOT NULL, kind TEXT NOT NULL,
            action_json TEXT NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL, claimed_at TEXT,
            finished_at TEXT, outcome TEXT, evidence_path TEXT, evidence_sha256 TEXT, gate_passed INTEGER,
            cost_cny_fen INTEGER, cost_source TEXT, dispatch_used INTEGER NOT NULL DEFAULT 0,
            output_bytes INTEGER NOT NULL DEFAULT 0, outputs_json TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, event TEXT NOT NULL,
            action_id TEXT, detail TEXT);
          PRAGMA user_version=1;
        """)
        now = now_text()
        connection.execute("INSERT INTO run VALUES (1,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                           (card["run_id"], card["revision"], str(Path(card_path).resolve()),
                            hashlib.sha256(raw).hexdigest(), card["project_root"], option["id"], "RUNNING",
                            card["budgets"]["cost_soft_limit_cny_fen"], now, now, canonical(card),
                            str(Path(confirmation_path).absolute())))
        actions = next(item["actions"] for item in card["options"] if item["id"] == option["id"])
        for index, action in enumerate(actions):
            connection.execute("INSERT INTO actions (action_id,position,kind,action_json,state,attempt) VALUES (?,?,?,?,?,0)",
                               (action["id"], index, action["kind"], canonical(action), "PENDING"))
        connection.execute("INSERT INTO events (at,event,detail) VALUES (?,?,?)",
                           (now, "STARTED", controller_sha()))
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    connection.close()


def run_row(connection):
    row = connection.execute("SELECT * FROM run WHERE id=1").fetchone()
    if row is None:
        raise RunError("run state has no run row")
    return row


def action_rows(connection):
    return connection.execute("SELECT * FROM actions ORDER BY position").fetchall()


def snapshot(connection):
    run = dict(run_row(connection))
    del run["card_json"]
    actions = []
    for row in action_rows(connection):
        value = dict(row)
        value["action"] = json.loads(value.pop("action_json"))
        value["fixture_gate_passed"] = value.pop("gate_passed")
        actions.append(value)
    events = [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY seq")]
    costs = [item["cost_cny_fen"] for item in actions if item["cost_cny_fen"] is not None]
    estimates = sum(item["cost_cny_fen"] for item in actions if item["cost_source"] == "token_rate_estimate")
    unknown = sum(1 for item in actions if item["action"]["paid"] and item["state"] in {"SUCCEEDED", "FAILED", "UNCERTAIN"} and item["cost_cny_fen"] is None)
    total = sum(costs)
    return {"run": run, "actions": actions, "events": events, "offline_fixture": True, "gate_passed": False,
            "cost": {"known_cny_fen": total, "visible_receipt_cny_fen": total - estimates,
                     "token_estimate_cny_fen": estimates, "unknown_paid_actions": unknown,
                     "threshold_cny_fen": run["cost_soft_limit_cny_fen"],
                     "soft_warning": total >= run["cost_soft_limit_cny_fen"] * 0.8 or bool(unknown)}}


def current_card(connection, state):
    run = run_row(connection)
    if connection.execute("SELECT detail FROM events WHERE event='STARTED'").fetchone()[0] != controller_sha():
        raise RunError("controller implementation changed; hand off without replay")
    raw, card, options, expires = read_card(run["card_path"], Path(state).absolute())
    if hashlib.sha256(raw).hexdigest() != run["card_sha256"] or canonical(card) != run["card_json"]:
        raise RunError("live startup card differs from the approved revision")
    validate_confirmation(run["confirmation_path"], raw, run["option_id"], expires)
    return run, card, option_for(options, run["option_id"])


def safe_project_file(path, root, *, must_exist=True):
    return autonomy_start.local_path(path, root, must_exist=must_exist, regular=True)


def record(connection, event, action=None, detail=None):
    connection.execute("INSERT INTO events (at,event,action_id,detail) VALUES (?,?,?,?)",
                       (now_text(), event, action, detail))


def set_state(connection, state, reason=None):
    connection.execute("UPDATE run SET state=?, pause_reason=?, updated_at=? WHERE id=1",
                       (state, reason, now_text()))


def write_path(path, run, option, *, must_exist=True):
    target = safe_project_file(path, run["project_root"], must_exist=must_exist)
    if not any(target.is_relative_to(Path(run["project_root"]) / scope) for scope in option["write_scope"]):
        raise RunError("file is outside the confirmed output scope")
    return target


def by_action(connection, action_id):
    row = connection.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
    if row is None:
        raise RunError("unknown action id")
    return row, json.loads(row["action_json"])


def current_evidence(row, root):
    if not row["evidence_path"]:
        raise RunError("predecessor has no evidence")
    path = safe_project_file(row["evidence_path"], root)
    if sha256(path) != row["evidence_sha256"]:
        raise RunError("predecessor evidence changed; no dependent action can start")
    return path


def recorded_outputs(row, root):
    outputs = json.loads(row["outputs_json"])
    for name, digest in outputs.items():
        if sha256(safe_project_file(name, root)) != digest:
            raise RunError("predecessor output changed: " + name)
    return outputs


def valid_review(connection, dependency, root):
    import audit_dispatch

    _, action = by_action(connection, dependency)
    key = action["review"]["review_id"], action["review"]["point"]
    for row in reversed(action_rows(connection)):
        review = json.loads(row["action_json"])["review"]
        if not review or (review["review_id"], review["point"]) != key:
            continue
        if row["state"] != "SUCCEEDED" or not row["gate_passed"]:
            continue
        path = current_evidence(row, root)
        _, items = audit_dispatch.load_checklist(key[1], audit_dispatch.REFERENCE_MD)
        receipt = audit_dispatch.load_receipt(path, len(items))
        expected = {"run_id": run_row(connection)["run_id"], "action_id": row["action_id"], "round": review["round"]}
        if (receipt.get("unattended") != expected or not receipt.get("review_counted") or not row["dispatch_used"] or
                (receipt.get("report") or {}).get("decision") != "通过" or
                any(receipt["binding"][field] != value for field, value in
                    audit_dispatch.rules_binding(audit_dispatch.REFERENCE_MD).items())):
            raise RunError("saved approval no longer matches the run, verdict or review rules")
        pack = safe_project_file(receipt["pack"]["path"], root)
        if sha256(pack) != receipt["pack"]["after_sha256"]:
            raise RunError("review pack drift: gate no longer current")
        for item in receipt["binding"]["files"]:
            if sha256(safe_project_file(item["path"], root)) != item["sha256"]:
                raise RunError("review inputs drifted; gate no longer current")
        return True
    return False


def dependencies(connection, run, action):
    for dependency in action["requires"]:
        row, _ = by_action(connection, dependency)
        if row["state"] != "SUCCEEDED":
            raise RunError("predecessor not complete: " + dependency)
        current_evidence(row, run["project_root"])
        if row["kind"] != "independent_review":
            recorded_outputs(row, run["project_root"])
        nonpass_input = action["continue_on_review_failure"] and action["condition"]["action"] == dependency
        if row["kind"] == "independent_review" and not nonpass_input:
            if not valid_review(connection, dependency, run["project_root"]):
                raise RunError("review gate is not passed: " + dependency)
    condition = action["condition"]
    if condition and condition["result"] == "review_not_passed":
        row, _ = by_action(connection, condition["action"])
        if row["gate_passed"]:
            raise RunError("extension is not triggered: prior review passed")


def claim(connection, state, action_id, controller):
    run, _, option = current_card(connection, state)
    row, action = by_action(connection, action_id)
    if row["action_id"] not in option["action_ids"]:
        raise RunError("action is not in the confirmed option")
    approved = next(item for item in json.loads(run["card_json"])["options"] if item["id"] == option["id"])
    if action != next(item for item in approved["actions"] if item["id"] == action_id):
        raise RunError("stored action differs from the confirmed option")
    if run["state"] not in ({"RUNNING"} if action["paid"] else {"RUNNING", "PAUSED_COST"}) or row["state"] != "PENDING":
        raise RunError("run paused/closed or action already attempted; do not replay")
    if connection.execute("SELECT 1 FROM actions WHERE state='CLAIMED'").fetchone():
        raise RunError("another action is still in flight")
    fixed = action["operation"] is not None or action["kind"] == "independent_review"
    if fixed != controller:
        raise RunError("fixed adapters require execute; host actions require claim")
    if action["estimate"]["gpu"]["status"] != "not_applicable":
        raise RunError("no resource-enforcing GPU adapter is registered; estimate is planning only")
    dependencies(connection, run, action)
    for path in action["outputs"]:
        if write_path(path, run, option, must_exist=False).exists():
            raise RunError("action output already exists; cannot take credit for a stale file")
    cost = snapshot(connection)["cost"]
    estimate = action["estimate"]["cost_cny_fen"]
    if action["paid"] and (cost["soft_warning"] or estimate is None or
                           cost["known_cny_fen"] + estimate > cost["threshold_cny_fen"]):
        set_state(connection, "PAUSED_COST", "next paid action is not covered by the soft estimate")
        record(connection, "HANDOFF_COST", action_id)
        return None
    connection.execute("UPDATE actions SET state='CLAIMED', attempt=1, claimed_at=? WHERE action_id=?",
                       (now_text(), action_id))
    record(connection, "CLAIMED", action_id)
    return run, option, action


def finalize(connection, action_id, outcome, evidence=None, *, gate=None, cost=None, cost_source=None, output_bytes=0, outputs=None):
    row, action = by_action(connection, action_id)
    if row["state"] != "CLAIMED":
        raise RunError("only an in-flight action can receive a result")
    if action["paid"] and cost is None:
        cost_source = "unknown"
    connection.execute("""UPDATE actions SET state=?,outcome=?,finished_at=?,evidence_path=?,evidence_sha256=?,
                          gate_passed=?,cost_cny_fen=?,cost_source=?,output_bytes=?,outputs_json=? WHERE action_id=?""",
                       (outcome.upper(), outcome, now_text(), str(evidence) if evidence else None,
                        sha256(evidence) if evidence else None, gate, cost, cost_source, output_bytes,
                        canonical(outputs or {}), action_id))
    record(connection, outcome.upper(), action_id)
    if run_row(connection)["state"] == "PAUSE_REQUESTED":
        set_state(connection, "PAUSED", run_row(connection)["pause_reason"])
    elif outcome == "uncertain":
        set_state(connection, "PAUSED", "uncertain action; diagnose, do not replay")
    elif action["paid"] and snapshot(connection)["cost"]["soft_warning"]:
        set_state(connection, "PAUSED_COST", "soft threshold approached after current action")
        record(connection, "HANDOFF_COST", action_id)


def cost_value(args):
    if args.usage:
        if args.cost_cny_fen is not None or args.cost_source:
            raise RunError("provide a usage record or a typed amount, not both")
        _, data = autonomy_start.load_json(args.usage)
        if "billed_cny_fen" in data:
            autonomy_start.fields(data, "billed_cny_fen", "visible usage receipt")
            return autonomy_start.integer(data["billed_cny_fen"], "billed_cny_fen"), "visible_receipt"
        names = ("input_tokens", "cached_input_tokens", "output_tokens",
                 "input_fen_per_million", "cached_input_fen_per_million", "output_fen_per_million")
        autonomy_start.fields(data, " ".join(names), "token and user-supplied rate record")
        tokens, cached, output, input_rate, cached_rate, output_rate = [autonomy_start.integer(data[key], key) for key in names]
        return estimate_cost(tokens, cached, output, input_rate, cached_rate, output_rate)
    if (args.cost_cny_fen is None) != (args.cost_source is None) or (args.cost_cny_fen is not None and args.cost_cny_fen < 0):
        raise RunError("known cost needs a nonnegative amount and source")
    return args.cost_cny_fen, args.cost_source


def estimate_cost(tokens, cached, output, input_rate, cached_rate, output_rate):
    if cached > tokens:
        raise RunError("cached input tokens exceed total input")
    return ((tokens - cached) * input_rate + cached * cached_rate + output * output_rate + 999999) // 1000000, "token_rate_estimate"


@contextmanager
def host_lock(state):
    path = Path(state).absolute()
    with path.open("rb+") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0x3F000000)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def recover(connection, state, args):
    run = run_row(connection)
    if run["state"] == "FINISHED" or run["option_id"] != args.choice:
        raise RunError("cannot reopen a finished run or change the selected option")
    if Path(args.card).absolute() != Path(run["card_path"]):
        raise RunError("resume must use the original card path")
    if connection.execute("SELECT 1 FROM actions WHERE state='CLAIMED'").fetchone():
        for row in action_rows(connection):
            if row["state"] == "CLAIMED":
                finalize(connection, row["action_id"], "uncertain")
        return 3
    raw, card, options, expires = read_card(args.card, Path(state).absolute())
    if connection.execute("SELECT detail FROM events WHERE event='STARTED'").fetchone()[0] != controller_sha():
        raise RunError("controller implementation changed; hand off without replay")
    sha = hashlib.sha256(raw).hexdigest()
    validate_confirmation(args.confirmation, raw, args.choice, expires)
    option_for(options, args.choice)
    amended = sha != run["card_sha256"]
    if amended:
        old = json.loads(run["card_json"])
        if card["revision"] != run["revision"] + 1 or card["previous_card_sha256"] != run["card_sha256"]:
            raise RunError("revised card must bind the immediately previous revision")
        expected = json.loads(run["card_json"])
        for key in ("revision", "previous_card_sha256", "expires_at_utc"):
            expected[key] = card[key]
        expected["budgets"]["cost_soft_limit_cny_fen"] = card["budgets"]["cost_soft_limit_cny_fen"]
        for original, current in zip(expected["context"], card["context"]):
            if Path(original["path"]) == Path(run["project_root"]) / "README.md":
                original["sha256"] = current["sha256"]
        if expected != card or (run["state"] == "PAUSED_COST" and
                                 card["budgets"]["cost_soft_limit_cny_fen"] <= old["budgets"]["cost_soft_limit_cny_fen"]):
            raise RunError("revision may only renew validity, rebind README or raise the paused soft threshold")
    elif run["state"] == "PAUSED_COST":
        raise RunError("cost handoff requires a newly approved estimate/threshold; no automatic paid continuation")
    connection.execute("""UPDATE run SET revision=?,card_sha256=?,card_json=?,confirmation_path=?,
                          cost_soft_limit_cny_fen=? WHERE id=1""",
                       (card["revision"], sha, canonical(card), str(Path(args.confirmation).absolute()),
                        card["budgets"]["cost_soft_limit_cny_fen"]))
    set_state(connection, "RUNNING")
    record(connection, "RESUMED", detail="amended" if amended else "same card")
    return 0


def local_paths(run, option, action, connection):
    operation = action["operation"]
    source = safe_project_file(operation["source"], run["project_root"])
    target = write_path(operation["destination"], run, option, must_exist=False)
    used = connection.execute("SELECT SUM(output_bytes) FROM actions").fetchone()[0]
    disk = json.loads(run["card_json"])["budgets"]["disk_bytes"]
    cap = min(disk - used, action["estimate"]["disk_bytes"])
    if cap < 1 or target.exists():
        raise RunError("no output allowance or destination already exists")
    return source, target, cap


def match_binding(connection, run, action):
    import audit_dispatch

    operation = action["operation"]
    previous_id = action["condition"]["action"]
    previous, previous_action = by_action(connection, previous_id)
    receipt_path = current_evidence(previous, run["project_root"])
    _, items = audit_dispatch.load_checklist(previous_action["review"]["point"], audit_dispatch.REFERENCE_MD)
    receipt = audit_dispatch.load_receipt(receipt_path, len(items))
    active = audit_dispatch.review_contract(len(items), receipt)["active_blocker_ids"]
    if not active or set(operation["finding_ids"]) != set(active):
        raise RunError("verification does not cover the exact current blocking findings; hand off")
    return {"previous_receipt_sha256": previous["evidence_sha256"], "finding_ids": sorted(active)}


def execute_local(state, run, option, action, connection):
    _, target, _ = local_paths(run, option, action, connection)
    proc = subprocess.run([sys.executable, "-E", "-B", str(Path(__file__).resolve()),
                           "_local", str(Path(state).absolute()), action["id"]],
                          timeout=action["watchdog_seconds"], capture_output=True)
    if proc.returncode:
        raise RunError("registered local operation failed: " + proc.stderr[-512:].decode(errors="replace"))
    return target, target.stat().st_size


def local_operation(adapter, source, target, cap, *, expected=None, binding=None):
    source = Path(source)
    if adapter == "hash":
        data = (canonical({"sha256": sha256(source)}) + "\n").encode()
    else:
        with source.open("rb") as stream:
            data = stream.read((cap if adapter == "copy" else 1024 * 1024) + 1)
        if adapter != "copy":
            if len(data) > 1024 * 1024:
                raise RunError("structured input exceeds adapter limit")
            value = json.loads(data)
            if adapter == "json-sum":
                if not isinstance(value, list) or any(type(item) is not int for item in value):
                    raise RunError("json-sum accepts an array of integers only")
                result = {"count": len(value), "sum": sum(value)}
            elif adapter == "json-match":
                with expected.open("rb") as stream:
                    reference = stream.read(1024 * 1024 + 1)
                if len(reference) > 1024 * 1024 or canonical(value) != canonical(json.loads(reference)):
                    raise RunError("candidate does not match the frozen fixture expectation")
                result = {**binding, "candidate_sha256": hashlib.sha256(data).hexdigest(),
                          "expected_sha256": hashlib.sha256(reference).hexdigest(), "matched": True}
            else:
                result = {"valid_json": True}
            data = (canonical(result) + "\n").encode()
    if len(data) > cap:
        raise RunError("adapter output exceeds disk allowance")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(data)


def review_arguments(state, connection, run, option, action):
    review = action["review"]
    pack = write_path(review["pack_path"], run, option)
    inputs = write_path(review["inputs_path"], run, option)
    predecessor, _ = by_action(connection, review["prepare_action"])
    outputs = recorded_outputs(predecessor, run["project_root"])
    if not {str(pack), str(inputs)} <= set(outputs):
        raise RunError("review package was not recorded by its predecessor")
    relative = ["--pack", str(pack), "--inputs", str(inputs), "--point", str(review["point"]),
                "--transport", review["transport"], "--max-output-tokens", str(review["max_output_tokens"]),
                "--timeout", str(action["watchdog_seconds"]), "--unattended-state", str(Path(state).absolute()),
                "--unattended-action", action["id"]]
    if review["previous_action"]:
        previous, _ = by_action(connection, review["previous_action"])
        relative += ["--previous", str(current_evidence(previous, run["project_root"]))]
    return relative, pack, inputs


def review_ticket(state, action_id, args, binding, *, consume=False, publish=False):
    with closing(db_connect(state)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        run, _, option = current_card(connection, state)
        row, action = by_action(connection, action_id)
        if (run["state"] not in ({"RUNNING", "PAUSE_REQUESTED"} if publish else {"RUNNING"}) or
                row["state"] != "CLAIMED" or action["kind"] != "independent_review" or bool(row["dispatch_used"]) != publish):
            raise RunError("no unused unattended review ticket")
        dependencies(connection, run, action)
        expected, pack, inputs = review_arguments(state, connection, run, option, action)
        for flag in ("pack", "inputs", "previous", "point", "transport", "max-output-tokens", "timeout"):
            key = "--" + flag
            value = expected[expected.index(key) + 1] if key in expected else None
            actual = getattr(args, flag.replace("-", "_"))
            if (str(actual) if actual is not None else None) != value:
                raise RunError("unattended dispatcher arguments differ from fixed card: " + flag)
        if binding["review_id"] != action["review"]["review_id"] or sha256(pack) != binding["pack_sha256"]:
            raise RunError("review id/pack does not match the run ticket")
        allowed = set(action["context_paths"])
        for dependency in action["requires"]:
            predecessor, _ = by_action(connection, dependency)
            if predecessor["kind"] == "independent_review":
                allowed.add(str(current_evidence(predecessor, run["project_root"])))
            else:
                allowed.update(recorded_outputs(predecessor, run["project_root"]))
        bound_files = set()
        for item in binding["files"]:
            path = safe_project_file(item["path"], run["project_root"])
            bound_files.add(str(path))
            if str(path) not in allowed:
                raise RunError("review input outside the confirmed scope")
        if action["review"]["verification_action"]:
            verification, verification_action = by_action(connection, action["review"]["verification_action"])
            if not set(recorded_outputs(verification, run["project_root"])) <= bound_files:
                raise RunError("incremental review manifest omits local verification evidence")
            evidence = json.loads(current_evidence(verification, run["project_root"]).read_bytes())
            operation = verification_action["operation"]
            dependencies(connection, run, verification_action)
            expected = {**match_binding(connection, run, verification_action), "matched": True,
                        "candidate_sha256": sha256(safe_project_file(operation["source"], run["project_root"])),
                        "expected_sha256": sha256(safe_project_file(operation["expected"], run["project_root"]))}
            if evidence != expected:
                raise RunError("incremental review lacks a matching fixed-adapter result")
        for suffix in (".review.json", ".review.json.lock"):
            if write_path(str(pack) + suffix, run, option, must_exist=False).exists():
                raise RunError("review artifact already exists; do not reuse or overwrite")
        if consume:
            connection.execute("UPDATE actions SET dispatch_used=1 WHERE action_id=?", (action_id,))
            record(connection, "DISPATCH_CONSUMED", action_id)
        ledger = Path(state).absolute().parent / "fixture-ledger"
        write_path(ledger / "ledger.jsonl", run, option, must_exist=False)
        return {"run_id": run["run_id"], "action_id": action_id, "ledger_dir": str(ledger),
                "round": action["review"]["round"], "state_path": str(Path(state).absolute())}


def execute_review(state, run, option, action, connection):
    import audit_dispatch

    arguments, pack, _ = review_arguments(state, connection, run, option, action)
    proc = subprocess.run([sys.executable, "-E", "-B", str(Path(__file__).with_name("audit_dispatch.py")), *arguments],
                          capture_output=True, timeout=action["watchdog_seconds"])
    result = json.loads(proc.stdout)
    if not result.get("review_counted") or result.get("reused"):
        return None, None, 0, result.get("usage"), "review not counted: " + str(result.get("stage")) + "; diagnose without re-send"
    path = write_path(str(pack) + ".review.json", run, option)
    review = action["review"]
    _, checklist = audit_dispatch.load_checklist(review["point"], audit_dispatch.REFERENCE_MD)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise RunError("review receipt exceeds bounded reader limit")
    receipt = audit_dispatch.load_receipt(path, len(checklist))
    row, _ = by_action(connection, action["id"])
    if (receipt.get("unattended") != {"run_id": run["run_id"], "action_id": action["id"], "round": review["round"]}
            or receipt.get("round") != review["round"] or receipt.get("binding", {}).get("review_id") != review["review_id"]
            or receipt.get("review_counted") is not True or not row["dispatch_used"] or
            not receipt.get("intent_id") or result.get("ledger_error")):
        raise RunError("receipt does not bind this round or has a ledger error; no gate advances")
    passed = receipt["report"]["decision"] == "通过"
    return path, passed, path.stat().st_size, result.get("usage"), None


def start(args):
    raw, card, options, expiry = read_card(args.card)
    project = fixture_root(card["project_root"])
    if card["revision"] != 1:
        raise RunError("a revised card cannot start a new run")
    option = option_for(options, args.choice)
    if option["new_permissions"]:
        raise RunError("fixture mode cannot grant new permissions")
    validate_confirmation(args.confirmation, raw, args.choice, expiry)
    state = option["state_path"]
    if Path(state) != project / "runs/run-001/state.sqlite3" or card["run_id"] != "run-001":
        raise RunError("fixture requires its fixed run-001 identity and state path")
    create_db(state, args.card, raw, card, option, args.confirmation)
    with closing(db_connect(state, readonly=True)) as connection:
        return emit({"state_path": state, "mode": "offline_fixture", "authorization_verified": False, **snapshot(connection)})


def do_execute(connection, state, args):
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        claimed = claim(connection, state, args.action, True)
    if claimed is None:
        return 3, {"state": "PAUSED_COST"}
    run, option, action = claimed
    gate, size, evidence, outcome = None, 0, None, "succeeded"
    message, cost, source = None, None, None
    try:
        if action["kind"] == "independent_review":
            evidence, gate, size, usage, message = execute_review(state, run, option, action, connection)
            if message:
                outcome = "uncertain"
            if isinstance(usage, dict) and type(usage.get("billed_cny_fen")) is int and usage["billed_cny_fen"] >= 0:
                cost, source = usage["billed_cny_fen"], "visible_receipt"
            elif usage and "input_tokens" in usage and "output_tokens" in usage:
                rates = json.loads(run["card_json"])["budgets"].get("rate_cny_fen_per_million")
                if rates and 0 <= usage.get("cached_input_tokens", 0) <= usage["input_tokens"]:
                    cost, source = estimate_cost(usage["input_tokens"], usage.get("cached_input_tokens", 0),
                                                 usage["output_tokens"], rates["input"], rates["cached_input"], rates["output"])
        else:
            evidence, size = execute_local(state, run, option, action, connection)
    except subprocess.TimeoutExpired:
        outcome, message = "uncertain", "action watchdog stopped the owned child; inspect partial work; do not replay"
    except (ValueError, OSError, KeyError, RunError) as exc:
        outcome, message = "failed", str(exc)
        if action["kind"] == "independent_review":
            outcome = "uncertain"
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            current_card(connection, state)
            if action["kind"] != "independent_review":
                dependencies(connection, run, action)
        except (RunError, ValueError, OSError, KeyError, TypeError) as exc:
            outcome, gate, message = "uncertain", None, str(exc)
        outputs = {str(evidence): sha256(evidence)} if evidence and action["operation"] else {}
        finalize(connection, args.action, outcome, evidence, gate=gate, cost=cost, cost_source=source, output_bytes=size, outputs=outputs)
        if message:
            record(connection, "DIAGNOSTIC", args.action, message)
    return (0 if outcome == "succeeded" else 3), {"action_state": outcome.upper(), "error": message}


def operate(connection, state, args):
    verb = args.verb
    if verb == "execute":
        return do_execute(connection, state, args)
    with connection:
        if verb != "status":
            connection.execute("BEGIN IMMEDIATE")
        if verb == "claim":
            claimed = claim(connection, state, args.action, False)
            if claimed is None:
                return 3, {"state": "PAUSED_COST"}
            return 0, {"action": claimed[2]}
        if verb == "complete":
            run = run_row(connection)
            row, action = by_action(connection, args.action)
            if action["operation"] is not None or action["kind"] == "independent_review":
                raise RunError("only the registered adapter may complete this action")
            cost, source = cost_value(args)
            if not action["paid"] and cost not in (None, 0):
                raise RunError("paid work was not authorized for this action")
            evidence = safe_project_file(args.evidence, run["project_root"])
            option = next(item for item in json.loads(run["card_json"])["options"] if item["id"] == run["option_id"])
            if str(evidence) not in action["context_paths"] and str(evidence) not in {
                    str(Path(run["project_root"]) / name) for name in action["outputs"]}:
                raise RunError("result evidence is not an approved input or declared output")
            outcome, outputs, size = args.outcome, {}, 0
            try:
                current_card(connection, state)
                dependencies(connection, run, action)
            except (RunError, ValueError, OSError, KeyError, TypeError) as exc:
                outcome = "uncertain"
                record(connection, "DIAGNOSTIC", args.action, str(exc))
            if row["claimed_at"] and (datetime.now(timezone.utc) - datetime.fromisoformat(
                    re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", row["claimed_at"]))).total_seconds() > action["watchdog_seconds"]:
                outcome = "uncertain"
            if outcome == "succeeded":
                targets = [write_path(name, run, option) for name in action["outputs"]]
                if targets and evidence not in targets:
                    raise RunError("successful action requires new output evidence")
                size = sum(path.stat().st_size for path in targets)
                if size > action["estimate"]["disk_bytes"]:
                    outcome = "uncertain"
                else:
                    outputs = {str(path): sha256(path) for path in targets}
            finalize(connection, args.action, outcome, evidence, cost=cost, cost_source=source, output_bytes=size, outputs=outputs)
            return (3 if outcome == "uncertain" else 0), {"action_state": outcome.upper()}
        if verb == "resume":
            return recover(connection, state, args), {}
        if verb in {"pause", "finish"}:
            run = run_row(connection)
            in_flight = connection.execute("SELECT 1 FROM actions WHERE state='CLAIMED'").fetchone() is not None
            if run["state"] == "FINISHED" or (verb == "finish" and in_flight):
                raise RunError("run finished or action still in flight")
            if verb == "pause" and in_flight:
                set_state(connection, "PAUSE_REQUESTED", args.reason)
                record(connection, "PAUSE_REQUESTED", detail=args.reason)
                return 0, {}
            if verb == "finish":
                current_card(connection, state)
                for row in action_rows(connection):
                    action = json.loads(row["action_json"])
                    if action["phase"] != "required":
                        continue
                    if row["state"] != "SUCCEEDED" or (row["kind"] == "independent_review" and
                            not valid_review(connection, row["action_id"], run["project_root"])):
                        raise RunError("required work did not pass; use pause to hand it off")
                    current_evidence(row, run["project_root"])
                    recorded_outputs(row, run["project_root"])
            if run["state"] != "PAUSED_COST" or verb == "finish":
                set_state(connection, "PAUSED" if verb == "pause" else "FINISHED", args.reason)
            record(connection, verb.upper(), detail=args.reason)
    return 0, {}


def main(argv=None):
    started = run_manifest.started_at()
    LAST_EMITTED.clear()
    try:
        code = _run_main(argv)
    except SystemExit as stopped:
        exit_code = stopped.code if isinstance(stopped.code, int) else 2
        run_manifest.record_run(LAST_EMITTED, exit_code, started)
        raise
    run_manifest.record_run(LAST_EMITTED, code if isinstance(code, int) else 0, started)
    return code


def _run_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("start", "status", "pause", "resume", "finish", "claim", "complete", "execute"):
        cmd = sub.add_parser(verb)
        if verb != "start":
            cmd.add_argument("--state", required=True)
        if verb in {"start", "resume"}:
            for name in ("card", "choice", "confirmation"):
                cmd.add_argument("--" + name, required=True)
        if verb == "start":
            cmd.add_argument("--fixture", action="store_true", required=True)
        if verb in {"claim", "complete", "execute"}:
            cmd.add_argument("--action", required=True)
        if verb in {"pause", "finish"}:
            cmd.add_argument("--reason", required=True)
        if verb == "complete":
            cmd.add_argument("--outcome", choices=("succeeded", "failed", "uncertain"), required=True)
            cmd.add_argument("--evidence", required=True)
            cmd.add_argument("--usage")
            cmd.add_argument("--cost-cny-fen", type=int)
            cmd.add_argument("--cost-source", choices=("visible_receipt", "token_rate_estimate"))
    try:
        if argv is None and sys.argv[1:2] == ["_local"]:
            state, action_id = sys.argv[2:]
            with closing(db_connect(state, readonly=True)) as connection:
                run, _, option = current_card(connection, state)
                row, action = by_action(connection, action_id)
                approved = next(item for item in json.loads(run["card_json"])["options"] if item["id"] == option["id"])
                allowed = {"RUNNING", "PAUSE_REQUESTED"} if action["paid"] else {"RUNNING", "PAUSE_REQUESTED", "PAUSED_COST"}
                if (run["state"] not in allowed or row["state"] != "CLAIMED" or not action["operation"] or
                        action not in approved["actions"]):
                    raise RunError("no valid fixed local action claim")
                dependencies(connection, run, action)
                source, target, cap = local_paths(run, option, action, connection)
                operation = action["operation"]
                if operation["adapter"] == "json-match":
                    expected = safe_project_file(operation["expected"], run["project_root"])
                    local_operation("json-match", source, target, cap, expected=expected,
                                    binding=match_binding(connection, run, action))
                else:
                    local_operation(operation["adapter"], source, target, cap)
            return 0
        args = parser.parse_args(argv)
        if args.verb == "start":
            return start(args)
        with closing(db_connect(args.state, readonly=args.verb == "status")) as connection:
            if args.verb == "status":
                with connection:
                    connection.execute("BEGIN")
                    return emit(snapshot(connection))
            if args.verb == "pause":
                code, extra = operate(connection, args.state, args)
                return emit({**snapshot(connection), **extra}, code)
            with host_lock(args.state):
                code, extra = operate(connection, args.state, args)
                return emit({**snapshot(connection), **extra}, code)
    except (RunError, ValueError, OSError, sqlite3.Error, KeyError, TypeError, StopIteration) as exc:
        return emit({"state": "BLOCKED", "error": str(exc)}, 3)


if __name__ == "__main__":
    sys.exit(main())
