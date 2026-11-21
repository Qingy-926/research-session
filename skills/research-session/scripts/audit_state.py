# -*- coding: utf-8 -*-
r"""audit_state.py -- 受限自动化的状态机骨架（影子模式最小切片）。

本文件**只实现状态语义与转移校验**，不跑实验、不读远端、不写任何项目证据文件。

三条结构性保证（本文件的全部价值所在）：
  1. `FORMALLY_ACCEPTED` **在代码里没有自动路径**：`write_state` 见到该值直接 raise，
     只有显式传入 `--human-confirm <ticket>` 才可写。不是"约定不写"。
  2. `MECHANICAL_PASS` / `AUDIT_PASS` 都是**非终态**：编排器见到它们必须继续走到
     `READY_FOR_HUMAN_DECISION` 或 `BLOCKED` 才能停——防止"exit 0 → 收工"。
  3. `BLOCKED` 是唯一允许从任何状态进入的转移（fail-closed 兜底），且进入时**必须**
     写 `reason` + `evidence_path`；写不出就拒绝进 BLOCKED，逼出具体原因。

边界（本切片不改任何项目文件）：
  - **不写** `科研/科研结果总账.md`（该文件在项目 git 仓库之外，写入需单独授权）；
    本文件只产出 `_ledger_draft__A*.md` 草稿，语义字段一律留 `【待人工填写】`。
  - 不碰 `autonomy-bounds.md`、预注册、数据/排除名单/指标定义/正式 gate、任何已有原始证据。
  - 不改 `v5_*_autopilot.py`：状态机是**包在外面**的一层。

纪律：**exit 0 ≠ 科研通过**。`exit0_means` 字段把"跑完了"与"门没过"变成机器可读；
`derive_mechanical()` 是唯一允许把退出码翻译成状态的地方。

用法（Git Bash；stdout 只输出一行 ASCII 转义 JSON，过程信息走 stderr）：
  python work/audit_state.py --selftest
  python work/audit_state.py --check-transition MECHANICAL_PASS AUDIT_PENDING
  python work/audit_state.py --check-plugin-whitelist
  python work/audit_state.py --derive --exit-code 0 --last-result SMALL_HOLD
  python work/audit_state.py --show --step v6_stageC_D4D5D6
  python work/audit_state.py --ledger-draft --step v6_stageC_D4D5D6 --out <dir>

退出码：0 = 通过；2 = 拒绝 / 校验失败 / 自检项失败。
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile

# ---------------------------------------------------------------- 状态枚举

SCHEMA = "audit_state/v1"

# terminal        : 流程真正的终点（只有正式裁决）
# auto_writable   : 自动化是否可以写这个状态（False = 代码里无路径）
# stop_for_agent  : 编排器见到它可以停（其余状态必须继续推进）
STATES = {
    "MECHANICAL_RUNNING": {"terminal": False, "auto_writable": True, "stop_for_agent": False},
    "MECHANICAL_PASS": {"terminal": False, "auto_writable": True, "stop_for_agent": False},
    "EVIDENCE_RECOMPUTED": {"terminal": False, "auto_writable": True, "stop_for_agent": False},
    "AUDIT_PENDING": {"terminal": False, "auto_writable": True, "stop_for_agent": False},
    "AUDIT_PASS": {"terminal": False, "auto_writable": True, "stop_for_agent": False},
    "READY_FOR_HUMAN_DECISION": {"terminal": False, "auto_writable": True, "stop_for_agent": True},
    "FORMALLY_ACCEPTED": {"terminal": True, "auto_writable": False, "stop_for_agent": True},
    "BLOCKED": {"terminal": False, "auto_writable": True, "stop_for_agent": True},
}

# 合法前向转移。BLOCKED 见 ALWAYS_ENTERABLE，从任何状态可入。
TRANSITIONS = {
    "MECHANICAL_RUNNING": {"MECHANICAL_PASS"},
    "MECHANICAL_PASS": {"EVIDENCE_RECOMPUTED"},
    "EVIDENCE_RECOMPUTED": {"AUDIT_PENDING"},
    "AUDIT_PENDING": {"AUDIT_PASS"},
    "AUDIT_PASS": {"READY_FOR_HUMAN_DECISION"},
    "READY_FOR_HUMAN_DECISION": {"FORMALLY_ACCEPTED"},
    "FORMALLY_ACCEPTED": set(),          # 终态：不得再转移
    "BLOCKED": {"MECHANICAL_RUNNING", "READY_FOR_HUMAN_DECISION"},
}

# fail-closed 兜底：任何状态都可进 BLOCKED
ALWAYS_ENTERABLE = {"BLOCKED"}

# `last_result` 里出现这些词 ⇒ 机械流程跑完了但**门没过**（本项目真实语义，
# 见 `v5_biosppy_autopilot.py` L715：`if verdict != "GO": ... return 0`）。
GATE_NOT_MET_TOKENS = ("HOLD", "CLOSE", "INCOMPLETE", "FAIL")

# 总账文件：本文件**没有任何代码路径**可写它。仅用于自检断言"没写"。
LEDGER_FILENAME = "科研结果总账.md"

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
PLUGIN_SCRIPTS_DIR = os.path.join(PLUGIN_ROOT, "scripts")
PLUGIN_MAIN_CLI = "repro_governance.py"
PLUGIN_SECOND_CLI = "log_governance.py"

# 实测映射（2026-09-19 读 `repro_governance.py` main() 分派得到）：
#   init            -> init_profile(...)               写盘（profile）
#   freeze-protocol -> write_new(output, ...)          写盘
#   snapshot-code   -> write_new(output, ...)          写盘
#   preflight/status/verdict -> preflight()/verdict()  只读
#   audit-paper / verify-matrix / audit-metrics        只读
#   audit-logs / archive    -> import log_governance   本项目不可用（init 生成的 profile 缺键）
# 另：`--output` 对 init/freeze-protocol/snapshot-code **之外**的动作会 write_new（拒绝覆盖）。
PLUGIN_READONLY_SUBCOMMANDS = (
    "status",
    "preflight",
    "verdict",
    "audit-paper",
    "verify-matrix",
    "audit-metrics",
)
PLUGIN_WRITE_SUBCOMMANDS = ("init", "freeze-protocol", "snapshot-code")
PLUGIN_UNUSABLE_SUBCOMMANDS = ("audit-logs", "archive")

# 编排器不得出现的字符串（会被 `--check-plugin-whitelist` 扫描）
FORBIDDEN_TOKENS = (
    PLUGIN_SECOND_CLI,
    "archive --apply",
    "snapshot-code",
    "freeze-protocol",
)


class Refused(Exception):
    """状态写入被结构性拒绝。调用方必须停，不得绕过。"""


# ---------------------------------------------------------------- 基础工具


def log(msg):
    sys.stderr.write(
        "[audit_state] %s\n" % msg.encode("ascii", "backslashreplace").decode("ascii")
    )
    sys.stderr.flush()


def emit(obj, code):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 状态校验


def validate_state_name(state):
    if state not in STATES:
        raise Refused("未知状态 %r（合法值：%s）" % (state, ", ".join(sorted(STATES))))
    return state


def check_transition(src, dst):
    """返回 None 表示合法，否则返回拒绝理由字符串。"""
    validate_state_name(src)
    validate_state_name(dst)
    # 终态优先：FORMALLY_ACCEPTED 连 BLOCKED 都不接受——否则"停机"就成了覆写
    # 正式裁决记录的后门。后续步骤要 BLOCKED，另开新 step 的新状态文件。
    if src == "FORMALLY_ACCEPTED":
        return ("FORMALLY_ACCEPTED 是终态：不得再转移（含 BLOCKED）——"
                "否则 BLOCKED 会成为覆写正式裁决记录的后门；新步骤请另开新 step 的状态文件")
    if dst in ALWAYS_ENTERABLE:
        return None
    if src in ALWAYS_ENTERABLE and dst in TRANSITIONS[src]:
        return None
    if dst in TRANSITIONS[src]:
        return None
    return "非法转移 %s -> %s（合法后继：%s%s）" % (
        src,
        dst,
        ", ".join(sorted(TRANSITIONS[src])) or "（无）",
        "，或 BLOCKED（fail-closed 兜底）",
    )


def derive_mechanical(exit_code, last_result, markers=None):
    """把「退出码 + last_result + marker」翻译成 (状态, exit0_means, reason)。

    这是**唯一**允许解释退出码的地方。规则：
      - exit_code != 0            -> BLOCKED，exit0_means = "n/a"
      - exit_code == 0 且 last_result 命中 GATE_NOT_MET_TOKENS
                                  -> BLOCKED，exit0_means = "gate_not_met"
      - exit_code == 0 其余        -> MECHANICAL_PASS，exit0_means = "task_ran"
    注意：MECHANICAL_PASS **不等于**科研通过。
    """
    lr = (last_result or "").upper()
    if exit_code != 0:
        return (
            "BLOCKED",
            "n/a",
            "机械步骤退出码 %s（非 0）：读日志尾部报告，不擅自重试、不改参数（AGENTS.md §13）"
            % exit_code,
        )
    hit = [tok for tok in GATE_NOT_MET_TOKENS if tok in lr]
    if hit:
        return (
            "BLOCKED",
            "gate_not_met",
            "exit 0 但 last_result=%s 命中 %s：跑完了 ≠ 门过了（本项目真实语义，"
            "见 v5_biosppy_autopilot.py L715 `if verdict != \"GO\": return 0`）"
            % (last_result, "/".join(hit)),
        )
    if not lr:
        return (
            "BLOCKED",
            "unknown",
            "exit 0 但 last_result 为空：退出码语义不可判，按 fail-closed 停",
        )
    return (
        "MECHANICAL_PASS",
        "task_ran",
        "exit 0 且 last_result=%s 未命中门未过记号；机械通过**不代表**科研结论成立"
        % last_result,
    )


def build_state(step_id, state, prereg=None, mechanical=None, recompute=None,
                audit=None, human=None, blocked=None):
    validate_state_name(state)
    if state == "BLOCKED":
        if not blocked or not blocked.get("reason") or not blocked.get("evidence_path"):
            raise Refused(
                "进入 BLOCKED 必须写 reason + evidence_path（不许 '未知错误'）；"
                "写不出具体原因就说明还没定位，应继续停在 MECHANICAL_RUNNING"
            )
    return {
        "schema": SCHEMA,
        "step_id": step_id,
        "state": state,
        "terminal": STATES[state]["terminal"],
        "orchestrator_may_stop": STATES[state]["stop_for_agent"],
        "updated": now_iso(),
        "prereg": prereg or {},
        "mechanical": mechanical or {},
        "recompute": recompute or {},
        "audit": audit or {},
        "human": human or {"required": state == "READY_FOR_HUMAN_DECISION",
                           "question": None, "options": [], "decided": None},
        "blocked": blocked,
    }


def write_state(path, obj, human_confirm=None, prior=None):
    """写状态文件。返回写入后的 dict。

    拒绝条件（全部结构性，不是约定）：
      - state == FORMALLY_ACCEPTED 且未给 human_confirm
      - state == BLOCKED 缺 reason/evidence_path
      - 从 prior 到 obj['state'] 为非法转移（除非目标是 BLOCKED）
      - prior 已是 FORMALLY_ACCEPTED（终态不得改写）
    """
    state = validate_state_name(obj.get("state"))
    if state == "FORMALLY_ACCEPTED" and not human_confirm:
        raise Refused(
            "FORMALLY_ACCEPTED 在自动化里**没有代码路径**：必须显式 --human-confirm <ticket>"
            "（并在会话中经用户确认）才可写。"
        )
    if state == "BLOCKED":
        b = obj.get("blocked") or {}
        if not b.get("reason") or not b.get("evidence_path"):
            raise Refused("进入 BLOCKED 必须写 reason + evidence_path")

    if prior is not None:
        if prior.get("state") == "FORMALLY_ACCEPTED":
            raise Refused("既存状态已是 FORMALLY_ACCEPTED（终态），拒绝改写")
        why = check_transition(prior.get("state"), state)
        if why:
            raise Refused(why)

    if human_confirm:
        obj.setdefault("human", {})
        obj["human"]["decided"] = human_confirm
        obj["human"]["confirmed_by"] = "human-confirm-ticket"

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return obj


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 总账草稿

LEDGER_MECHANICAL_FIELDS = (
    "归属",
    "门链路（逐位复算）",
    "单变量与输入",
    "判据（出数前写死）",
    "实测（n=…）",
    "产物",
)
LEDGER_SEMANTIC_FIELDS = ("判定", "诚实边界", "证据层级", "决策")
PENDING = "【待人工填写】"


def render_ledger_draft(obj, entry_id="A??"):
    """把状态文件渲染成总账条目**草稿**。

    6 个机械字段填满（带 sha256）；4 个语义字段一律 `【待人工填写】`。
    人填完才能进总账——机械保证字段与哈希齐全，人保留措辞与裁决。
    """
    mech = obj.get("mechanical") or {}
    pre = obj.get("prereg") or {}
    rec = obj.get("recompute") or {}
    aud = obj.get("audit") or {}

    def kv(d):
        return " / ".join("%s=%s" % (k, v) for k, v in sorted(d.items())) or "（空）"

    inp = rec.get("input_sha256") or {}
    lines = []
    lines.append("### %s · <标题待人工填写>（%s，**%s**）" % (entry_id, now_iso()[:10], PENDING))
    lines.append("")
    lines.append("> 本条目由 `work/audit_state.py --ledger-draft` 从 `%s` 生成。"
                 % obj.get("step_id"))
    lines.append("> **机械字段已自动填好并带哈希；语义字段一律留 `%s`，人填完才能进总账。**"
                 % PENDING)
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|---|---|")

    lines.append("| 归属 | 预注册 `%s`；sha256 `%s` |"
                 % (pre.get("path") or PENDING, pre.get("sha256") or PENDING))
    lines.append("| 门链路（逐位复算） | exit_code=%s；last_result=%s；**exit0_means=%s**；"
                 "markers=%s；state=%s |"
                 % (mech.get("exit_code"), mech.get("last_result"),
                    mech.get("exit0_means"), ",".join(mech.get("markers") or []) or "（无）",
                    obj.get("state")))
    lines.append("| 单变量与输入 | 输入 sha256：%s |" % (kv(inp),))
    lines.append("| 判据（出数前写死） | 预注册 sha256 `%s`（判据原文见该文件；本行不改写判据） |"
                 % (pre.get("sha256") or PENDING))
    lines.append("| 实测（n=…） | 重算层 `%s`；输出 `%s`；%s |"
                 % (rec.get("recompute_layer") or PENDING,
                    rec.get("output_path") or PENDING,
                    kv(rec.get("output") or {})))
    prod = rec.get("output_path") or PENDING
    lines.append("| 产物 | `%s`（sha256 `%s`）；审计结果 `%s`（verdict=%s） |"
                 % (prod, rec.get("output_sha256") or PENDING,
                    aud.get("result_path") or "（未做）", aud.get("verdict") or "（未做）"))
    for f in LEDGER_SEMANTIC_FIELDS:
        lines.append("| %s | %s |" % (f, PENDING))
    lines.append("")
    lines.append("> 未决：审计 verdict = `%s`（**审计意见不是证据，不改变证据层级**）；"
                 "机械通过仅代表流程跑完。"
                 % (aud.get("verdict") or "（未做）"))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 自检


def _selftest(tmpdir):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    sp = os.path.join(tmpdir, "_audit_state__selftest.json")

    # 1. FORMALLY_ACCEPTED 自动写入必须被拒
    obj = build_state("selftest", "FORMALLY_ACCEPTED")
    try:
        write_state(sp, obj)
        add("refuse_formally_accepted_without_human", False, "竟然写成功了")
    except Refused as exc:
        add("refuse_formally_accepted_without_human", True, str(exc)[:120])

    # 2. 带 human_confirm 才可写（且只能从 READY_FOR_HUMAN_DECISION 进）
    try:
        prior = build_state("selftest", "READY_FOR_HUMAN_DECISION")
        write_state(sp, prior)
        write_state(sp, build_state("selftest", "FORMALLY_ACCEPTED"),
                    human_confirm="ticket-0001", prior=prior)
        add("allow_formally_accepted_with_human_ticket", True, "")
    except Refused as exc:
        add("allow_formally_accepted_with_human_ticket", False, str(exc)[:120])

    # 3. 终态不得改写
    try:
        prior = load_state(sp)
        write_state(sp, build_state("selftest", "BLOCKED",
                                    blocked={"reason": "x", "evidence_path": "y"}), prior=prior)
        add("refuse_rewrite_after_formally_accepted", False, "竟然改写成功")
    except Refused as exc:
        add("refuse_rewrite_after_formally_accepted", True, str(exc)[:120])

    # 4. BLOCKED 缺 reason / evidence_path 必须被拒
    for label, blk in (("no_reason", {"evidence_path": "p"}),
                       ("no_evidence", {"reason": "r"}),
                       ("empty", None)):
        try:
            write_state(os.path.join(tmpdir, "_blk_%s.json" % label),
                        build_state("selftest", "BLOCKED", blocked=blk))
            add("refuse_blocked_without_%s" % label, False, "竟然写成功了")
        except Refused:
            add("refuse_blocked_without_%s" % label, True, "")

    # 5. 合法 / 非法转移
    add("transition_mechanical_pass_to_audit_pending_illegal",
        check_transition("MECHANICAL_PASS", "AUDIT_PENDING") is not None, "")
    add("transition_mechanical_pass_to_recomputed_legal",
        check_transition("MECHANICAL_PASS", "EVIDENCE_RECOMPUTED") is None, "")
    add("transition_any_nonterminal_to_blocked_legal",
        all(check_transition(s, "BLOCKED") is None
            for s in STATES if s != "FORMALLY_ACCEPTED"), "")
    add("transition_formally_accepted_is_terminal",
        all(check_transition("FORMALLY_ACCEPTED", d) is not None for d in STATES), "")

    # 6. exit0_means：门未过必须 BLOCKED
    st, e0, _ = derive_mechanical(0, "SMALL_HOLD")
    add("exit0_with_hold_is_blocked", st == "BLOCKED" and e0 == "gate_not_met",
        "state=%s exit0_means=%s" % (st, e0))
    st, e0, _ = derive_mechanical(0, "PROBE_HOLD")
    add("exit0_with_probe_hold_is_blocked", st == "BLOCKED" and e0 == "gate_not_met", st)
    st, e0, _ = derive_mechanical(0, "MATRIX_INCOMPLETE")
    add("exit0_with_incomplete_is_blocked", st == "BLOCKED" and e0 == "gate_not_met", st)
    st, e0, _ = derive_mechanical(0, "MATRIX_DONE")
    add("exit0_with_done_is_mechanical_pass",
        st == "MECHANICAL_PASS" and e0 == "task_ran", "state=%s exit0_means=%s" % (st, e0))
    st, e0, _ = derive_mechanical(10, "DATASET_GATE_FAIL")
    add("nonzero_exit_is_blocked", st == "BLOCKED" and e0 == "n/a", st)
    st, e0, _ = derive_mechanical(0, "")
    add("exit0_without_last_result_is_blocked", st == "BLOCKED", st)

    # 7. 非终态保证
    add("mechanical_pass_not_terminal", STATES["MECHANICAL_PASS"]["terminal"] is False
        and STATES["MECHANICAL_PASS"]["stop_for_agent"] is False, "")
    add("audit_pass_not_terminal", STATES["AUDIT_PASS"]["terminal"] is False
        and STATES["AUDIT_PASS"]["stop_for_agent"] is False, "")
    add("only_formally_accepted_is_terminal",
        [s for s, v in STATES.items() if v["terminal"]] == ["FORMALLY_ACCEPTED"], "")
    add("only_formally_accepted_not_auto_writable",
        [s for s, v in STATES.items() if not v["auto_writable"]] == ["FORMALLY_ACCEPTED"], "")

    # 8. 总账草稿：机械字段满、语义字段空
    demo = build_state(
        "selftest_ledger", "READY_FOR_HUMAN_DECISION",
        prereg={"path": "00-立项/x.md", "sha256": "a" * 64},
        mechanical={"exit_code": 0, "last_result": "STAGE_C_DONE",
                    "exit0_means": "task_ran", "markers": ["PULL_VERIFIED"]},
        recompute={"recompute_layer": "raw_per_record", "output_path": "runs/x.json",
                   "output_sha256": "b" * 64, "input_sha256": {"y": "c" * 64}},
        audit={"verdict": "NEEDS_HUMAN", "result_path": "reviews/x.json"},
    )
    md = render_ledger_draft(demo)
    mech_ok = all(("| %s |" % f) in md and PENDING not in md.split("| %s |" % f)[1].split("\n")[0]
                  for f in LEDGER_MECHANICAL_FIELDS)
    add("ledger_draft_mechanical_fields_filled", mech_ok, "")
    sem_ok = all(("| %s | %s |" % (f, PENDING)) in md for f in LEDGER_SEMANTIC_FIELDS)
    add("ledger_draft_semantic_fields_pending", sem_ok, "")

    # 9. 本文件没有写总账的代码路径
    src = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    write_ops = ("open(", "write_text", "shutil.move", "os.replace", "os.remove")
    ledger_write = False
    for i, line in enumerate(src.splitlines(), 1):
        if LEDGER_FILENAME in line and any(op in line for op in write_ops):
            ledger_write = True
    add("no_code_path_writes_ledger", not ledger_write,
        "发现可能写总账的行" if ledger_write else "")
    add("selftest_writes_only_to_tmpdir", True, tmpdir)

    return checks


def check_plugin_whitelist(repo_root):
    """白名单：只允许 repro_governance.py 的只读子命令；禁止 log_governance.py。

    同时扫描 `work/` 下的编排器源码，确认没有出现禁用串。
    """
    problems = []
    overlap = set(PLUGIN_READONLY_SUBCOMMANDS) & set(PLUGIN_WRITE_SUBCOMMANDS)
    if overlap:
        problems.append("白名单与写子命令重叠：%s" % sorted(overlap))
    if PLUGIN_SECOND_CLI in PLUGIN_READONLY_SUBCOMMANDS:
        problems.append("%s 不得进白名单" % PLUGIN_SECOND_CLI)

    scanned = []
    workdir = os.path.join(repo_root, "work")
    for name in sorted(os.listdir(workdir)) if os.path.isdir(workdir) else []:
        if not name.endswith(".py"):
            continue
        if name in ("audit_state.py",):     # 本文件含禁用串（它就在白名单里定义）
            continue
        path = os.path.join(workdir, name)
        scanned.append(name)
        try:
            text = open(path, "r", encoding="utf-8").read()
        except Exception as exc:
            problems.append("%s 不可读：%s" % (name, exc))
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for tok in FORBIDDEN_TOKENS:
                if tok in line:
                    problems.append("%s:%d 出现禁用串 %r" % (name, i, tok))
    return problems, scanned


# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="跑结构性自检")
    ap.add_argument("--check-transition", nargs=2, metavar=("FROM", "TO"))
    ap.add_argument("--derive", action="store_true",
                    help="由退出码/ last_result 推导状态（不写文件）")
    ap.add_argument("--exit-code", type=int, default=None)
    ap.add_argument("--last-result", default="")
    ap.add_argument("--marker", action="append", default=[])
    ap.add_argument("--check-plugin-whitelist", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--step", help="step_id")
    ap.add_argument("--ledger-draft", action="store_true")
    ap.add_argument("--entry-id", default="A??")
    ap.add_argument("--out", help="输出目录（ledger-draft 用；只写新文件）")
    ap.add_argument("--repo-root", default=os.getcwd(), help="只读扫描根；默认当前目录")
    ap.add_argument("--plugin-root", default=PLUGIN_ROOT, help="公开插件根；其 scripts/ 下核对治理 CLI")
    ap.add_argument("--runs-dir", default=None, help="状态文件目录；缺省 <repo-root>/runs")
    args = ap.parse_args()

    repo_root = args.repo_root
    runs_dir = args.runs_dir or os.path.join(repo_root, "runs")
    state_path = os.path.join(runs_dir, "_audit_state__%s.json" % args.step) if args.step else None

    if args.selftest:
        with tempfile.TemporaryDirectory(prefix="audit-state-selftest-") as tmpdir:
            checks = _selftest(tmpdir)
        failed = [c for c in checks if not c["ok"]]
        emit({"ok": not failed, "stage": "selftest",
              "total": len(checks), "failed": len(failed), "checks": checks},
             0 if not failed else 2)

    if args.check_transition:
        why = check_transition(args.check_transition[0], args.check_transition[1])
        emit({"ok": why is None, "stage": "check_transition",
              "from": args.check_transition[0], "to": args.check_transition[1],
              "legal": why is None, "reason": why}, 0 if why is None else 2)

    if args.derive:
        if args.exit_code is None:
            emit({"ok": False, "stage": "args", "error": "--derive 需要 --exit-code"}, 2)
        st, e0, why = derive_mechanical(args.exit_code, args.last_result, args.marker)
        emit({"ok": True, "stage": "derive", "state": st, "exit0_means": e0,
              "terminal": STATES[st]["terminal"],
              "orchestrator_may_stop": STATES[st]["stop_for_agent"],
              "reason": why,
              "note": "MECHANICAL_PASS 不等于科研通过"}, 0)

    if args.check_plugin_whitelist:
        problems, scanned = check_plugin_whitelist(repo_root)
        exists = os.path.isfile(os.path.join(args.plugin_root, "scripts", PLUGIN_MAIN_CLI))
        emit({"ok": not problems, "stage": "plugin_whitelist",
              "plugin_main_cli": PLUGIN_MAIN_CLI,
              "plugin_main_cli_exists": exists,
              "plugin_second_cli": PLUGIN_SECOND_CLI,
              "readonly_subcommands": list(PLUGIN_READONLY_SUBCOMMANDS),
              "write_subcommands_denied": list(PLUGIN_WRITE_SUBCOMMANDS),
              "unusable_subcommands": list(PLUGIN_UNUSABLE_SUBCOMMANDS),
              "scanned_files": scanned, "problems": problems},
             0 if not problems else 2)

    if args.show:
        if not state_path:
            emit({"ok": False, "stage": "args", "error": "--show 需要 --step"}, 2)
        obj = load_state(state_path)
        emit({"ok": obj is not None, "stage": "show", "path": state_path, "state": obj},
             0 if obj else 2)

    if args.ledger_draft:
        if not state_path or not args.out:
            emit({"ok": False, "stage": "args",
                  "error": "--ledger-draft 需要 --step 与 --out"}, 2)
        obj = load_state(state_path)
        if obj is None:
            emit({"ok": False, "stage": "read_state", "error": "状态文件不存在：%s" % state_path}, 2)
        md = render_ledger_draft(obj, args.entry_id)
        os.makedirs(args.out, exist_ok=True)
        target = os.path.join(args.out, "_ledger_draft__%s.md" % args.entry_id)
        if os.path.exists(target):
            emit({"ok": False, "stage": "refuse_overwrite",
                  "error": "目标已存在，拒绝覆盖已有证据：%s" % target}, 2)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(md)
        emit({"ok": True, "stage": "ledger_draft", "out": target,
              "sha256": sha256_text(md),
              "semantic_fields_pending": list(LEDGER_SEMANTIC_FIELDS),
              "note": "语义字段一律留白；草稿不是总账条目，须人填后才可登记"}, 0)

    ap.print_help(sys.stderr)
    emit({"ok": False, "stage": "args", "error": "未指定动作"}, 2)


if __name__ == "__main__":
    try:
        main()
    except Refused as exc:
        emit({"ok": False, "stage": "refused", "error": str(exc)}, 2)
