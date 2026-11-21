# -*- coding: utf-8 -*-
r"""shadow_compare.py -- 影子模式的记账单元 + 唯一硬失败信号。

影子模式循环：
    编排器跑机械步 → 收集证据 → 层级独立重算 → raw 审计 → 生成待裁决报告 + 自动建议
    然后**并列记录**：自动建议 | 人工裁决 → 比对。

**不做什么**（结构性）：
    ✗ 不推进正式实验   ✗ 不改协议   ✗ 不写 FORMALLY_ACCEPTED
本文件只写**它自己的**记账文件 `02-执行/runs/_shadow_compare.jsonl`，
不碰任何项目证据、不碰总账、不碰状态机以外的东西。

**唯一硬失败信号**：`gate_illegally_crossed` 非 null ⇒ **立即停机并报告**。
它的含义是"自动化在人工裁决之前就越过了门"——影子模式的全部价值就在这一点上，
所以它一旦出现，本机制立即作废并交人。

**防篡改（廉价但真）**：每行带 `prev_line_sha256`，形成哈希链。
`--show` 逐行校验链；任何一行被事后改写/插入/删除 → 链断 → exit 2。
（与 `audit_state.py` 的"不可覆盖证据"同一条纪律：已有审计记录不得改写。）

用法（Git Bash）：
  python work/shadow_compare.py --show
  python work/shadow_compare.py --append --step v6_stageC_D4D5D6 \
      --auto-suggestion "..." --human-decision "..." [--auto-missed x --auto-false-alarm y]
  python work/shadow_compare.py --append --step ... --gate-illegally-crossed "越过了 <gate>"

退出码：0 = 正常且无越门；2 = 检测到越门 / 哈希链断裂 / 参数错误。
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = "shadow_compare/v1"

# 自动建议与人工裁决的四种关系（记账用，不是判据）
RELATIONS = ("agree", "auto_missed", "auto_false_alarm", "undetermined")


def log(msg):
    sys.stderr.write(
        "[shadow_compare] %s\n" % msg.encode("ascii", "backslashreplace").decode("ascii")
    )
    sys.stderr.flush()


def emit(obj, code):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def line_sha(record):
    return hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def read_records(path):
    """读全部记录。返回 (records, chain_errors)。"""
    if not os.path.exists(path):
        return [], []
    records, errors, prev = [], [], None
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception as exc:
                errors.append("第 %d 行不是合法 JSON：%s" % (lineno, exc))
                continue
            if rec.get("prev_line_sha256") != prev:
                errors.append(
                    "第 %d 行哈希链断裂（prev_line_sha256 与上一行不符）"
                    "⇒ 该行之前被改写/插入/删除过" % lineno
                )
            body = {k: v for k, v in rec.items() if k != "line_sha256"}
            if rec.get("line_sha256") != line_sha(body):
                errors.append("第 %d 行自身 sha256 不符 ⇒ 行内容被改写过" % lineno)
            records.append(rec)
            prev = rec.get("line_sha256")
    return records, errors


def append_record(rec, path):
    records, errors = read_records(path)
    if errors:
        raise RuntimeError(
            "拒绝追加：既有记账已被改动（%s）。先人工核对原始记录，不得在断裂的链上续写。"
            % "; ".join(errors)
        )
    rec = dict(rec)
    rec["schema"] = SCHEMA
    rec["round"] = len(records) + 1
    rec["prev_line_sha256"] = records[-1]["line_sha256"] if records else None
    rec["line_sha256"] = line_sha(rec)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:      # 只追加，绝不重写
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def summarize(records, chain_errors):
    crossed = [r for r in records if r.get("gate_illegally_crossed")]
    relations = {}
    for r in records:
        relations[r.get("relation", "undetermined")] = \
            relations.get(r.get("relation", "undetermined"), 0) + 1
    return {
        "n_rounds": len(records),
        "relations": relations,
        "n_gate_illegally_crossed": len(crossed),
        "gates_illegally_crossed": [
            {"round": r.get("round"), "step": r.get("step"),
             "detail": r.get("gate_illegally_crossed")} for r in crossed],
        "chain_errors": chain_errors,
        "chain_ok": not chain_errors,
        "last": records[-1] if records else None,
    }


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--show", action="store_true", help="读全部记账并校验哈希链")
    ap.add_argument("--append", action="store_true", help="追加一轮记录")
    ap.add_argument("--step", help="step_id")
    ap.add_argument("--auto-suggestion", default=None)
    ap.add_argument("--human-decision", default=None)
    ap.add_argument("--auto-missed", action="append", default=[],
                    help="自动建议漏掉的（人工抓到）")
    ap.add_argument("--auto-false-alarm", action="append", default=[],
                    help="自动建议误报的（人工否决）")
    ap.add_argument("--evidence-cited-correctly", dest="ev_ok",
                    action="store_true", default=None)
    ap.add_argument("--evidence-cited-wrong", dest="ev_ok",
                    action="store_false", default=None)
    ap.add_argument("--verdict", default=None,
                    help="本轮审计 verdict（PASS/FAIL/NEEDS_HUMAN/UNPARSEABLE）")
    ap.add_argument("--gate-illegally-crossed", default=None,
                    help="仅在自动化真的越过门时填；任何非空值 ⇒ 立即停机")
    ap.add_argument("--relation", choices=RELATIONS, default=None,
                    help="自动建议与人工裁决的关系。**语义关系机器判不了**"
                         "（逐字比对太弱），故以人工显式声明为准；未声明则尝试逐字比对，"
                         "不一致即记 undetermined，绝不猜")
    ap.add_argument("--supersedes", type=int, default=None,
                    help="本行更正的是第几轮（追加式更正，不得改写旧行）")
    ap.add_argument("--path", help="显式合成影子日志路径；不提供隐式项目默认值")
    args = ap.parse_args()
    if not args.path:
        emit({"ok": False, "stage": "args", "error": "--show 或 --append 均须显式提供 --path"}, 2)

    if args.show:
        records, errors = read_records(args.path)
        s = summarize(records, errors)
        hard_fail = s["n_gate_illegally_crossed"] > 0 or not s["chain_ok"]
        if s["n_gate_illegally_crossed"] > 0:
            log("*** 硬失败：出现 gate_illegally_crossed ⇒ 影子模式立即停机并报告 ***")
        if not s["chain_ok"]:
            log("*** 记账哈希链断裂 ⇒ 已有审计记录被改动，停止 ***")
        emit({"ok": not hard_fail, "stage": "show", "path": args.path, **s,
              "note": ("ok=false 即硬停：要么自动化越过门，要么记账被改写；"
                       "两者都不得继续运行，必须报告用户")},
             0 if not hard_fail else 2)

    if args.append:
        if not args.step:
            emit({"ok": False, "stage": "args", "error": "--append 需要 --step"}, 2)
        if args.gate_illegally_crossed:
            # 允许记账（要留证据），但记完立即以 exit 2 硬停
            log("*** 记录越门事件后立即停机 ***")
        relation = args.relation
        if relation is None:
            # 逐字比对太弱，只作为兜底；语义关系必须由人显式声明
            relation = "undetermined"
            if args.auto_suggestion and args.human_decision:
                if args.auto_missed and args.auto_false_alarm:
                    relation = "undetermined"
                elif args.auto_missed:
                    relation = "auto_missed"
                elif args.auto_false_alarm:
                    relation = "auto_false_alarm"
                elif args.auto_suggestion.strip() == args.human_decision.strip():
                    relation = "agree"
        rec = {
            "step": args.step,
            "ts": now_iso(),
            "relation_source": "human_explicit" if args.relation else "mechanical_fallback",
            "supersedes_round": args.supersedes,
            "auto_suggestion": args.auto_suggestion,
            "human_decision": args.human_decision,
            "relation": relation,
            "auto_missed": list(args.auto_missed),
            "auto_false_alarm": list(args.auto_false_alarm),
            "evidence_cited_correctly": args.ev_ok,
            "audit_verdict": args.verdict,
            "gate_illegally_crossed": args.gate_illegally_crossed,
            "executed": False,
            "note": ("本轮仅记录；自动建议**未执行**。影子模式不推进正式实验、"
                     "不改协议、不写 FORMALLY_ACCEPTED。"),
        }
        try:
            written = append_record(rec, args.path)
        except RuntimeError as exc:
            emit({"ok": False, "stage": "append_refused", "error": str(exc)}, 2)
        records, errors = read_records(args.path)
        s = summarize(records, errors)
        hard_fail = bool(args.gate_illegally_crossed) or not s["chain_ok"]
        emit({"ok": not hard_fail, "stage": "append", "path": args.path,
              "written": written, "summary": s,
              "hard_stop": ("gate_illegally_crossed 非空 ⇒ 立即停机并报告用户"
                            if args.gate_illegally_crossed else None)},
             0 if not hard_fail else 2)

    ap.print_help(sys.stderr)
    emit({"ok": False, "stage": "args", "error": "需要 --show 或 --append"}, 2)


if __name__ == "__main__":
    main()
