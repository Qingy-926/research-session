# -*- coding: utf-8 -*-
r"""audit_raw.py -- raw-reading 独立审计通道（显式合成输入）。

与既有复核点（`audit_dispatch.py`，pack-only）的**根本差别**：
  - pack-only 读**一个自包含包** ⇒ 能发现包内不自洽，**发现不了包遗漏**；
  - raw-reading 读**原始日志 / 原始数组层 / 判据原文** ⇒ 能发现"执行方没写进包的东西"。
两条通道**不合并、不互相替代**：失效模式不同，正是独立性的来源。

**污染隔离（本文件的核心约束，不是建议）**：
  prompt 三段、顺序固定、执行方结论必须最后给且标注为自述：
    [段1 原始事实]      路径 + sha256 + 内容/结构摘要 —— 只给产物与代码
    [段2 协议与判据]    预注册判据**原文** —— 不给"我认为命中了哪条"
    [段3 执行方自述]    ⚠ 标注"属自述、非事实、不得作为推理前提"
  禁止进入段1/段2 的路径（硬断言，违反即 exit 2）：
    `README.md`、`科研结果总账.md`、`03-交付/`、`04-验证/`
  → 否则 raw-reading 会比 pack-only **更容易**被执行方散文结论污染。

**硬约束**：
  - 审计模型**只能提供意见**：不改协议、不提交正式结论、不绕过机械 gate；
  - 输出**必须写新文件**（沿用插件 `--output` 语义：已存在即拒绝，绝不覆盖证据）；
  - `verdict` **永远不直接产生** `FORMALLY_ACCEPTED`（见 `audit_state.py`）。

**输入边界**：`--inputs` 指向显式 JSON，包含 `raw_inputs[{path,role}]`、
`criteria_path` 与可选 `selfreport_path`。公开版不携带任何项目回放素材。
`--replay` 只是合成用例名；`--expect-not-pass` 用于已知错误夹具。

用法：
  python audit_raw.py --replay synthetic-case --inputs inputs.json --list-inputs
  python audit_raw.py --replay synthetic-case --inputs inputs.json --dry-run
  python audit_raw.py --replay synthetic-case --inputs inputs.json --out <绝对目录> \
      --provider-config provider.json

退出码：0 = 完成且符合显式预期；2 = 拒绝 / 通道失败 / 未满足预期。
"""

import argparse
import datetime
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile

# ---------------------------------------------------------------- 通道复用

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_PATH = os.path.join(HERE, "audit_dispatch.py")


def _load_dispatch():
    """复用 `audit_dispatch.py` 的 HTTP 接口，不复制其代码（避免两份实现漂移）。"""
    spec = importlib.util.spec_from_file_location("_audit_dispatch", DISPATCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 输入契约

SCHEMA = "raw_audit/v1"

# 污染面：这些路径**不得**进入审计输入（硬断言）
FORBIDDEN_PATTERNS = (
    "README.md",
    "科研结果总账.md",
    os.sep + "03-交付" + os.sep,
    "03-交付/",
    os.sep + "04-验证" + os.sep,
    "04-验证/",
)

CHECKS = (
    "split_confusion",
    "seed_identity_confusion",
    "wrong_dataset_or_exclusion_list",
    "metrics_recomputable_from_raw",
    "prereg_gate_crossed",
    "correlation_as_causation",
    "pilot_as_formal",
    "overinterpretation",
)


class Refused(Exception):
    pass


def log(msg):
    sys.stderr.write(
        "[audit_raw] %s\n" % msg.encode("ascii", "backslashreplace").decode("ascii")
    )
    sys.stderr.flush()


def emit(obj, code):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def now_stamp():
    return datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def assert_clean(paths):
    """污染面硬断言：任一命中即拒绝送审。"""
    bad = []
    for p in paths:
        norm = p.replace("\\", "/")
        for pat in FORBIDDEN_PATTERNS:
            if pat.replace("\\", "/") in norm:
                bad.append((p, pat))
    if bad:
        raise Refused(
            "审计输入命中污染面，拒绝送审：%s。"
            "raw-reading 通道只发原料 + 判据，不发执行方散文结论。"
            % "; ".join("%s <- %s" % (p, pat) for p, pat in bad)
        )


# ---------------------------------------------------------------- 判据抽取

def extract_criteria(prereg_path, start="### 5.0", stop="## 7."):
    """从预注册原文抽取判据段（§5.0–§6.2），**逐字不改写**。"""
    text = read_text(prereg_path)
    i = text.find(start)
    j = text.find(stop, i + 1) if i >= 0 else -1
    if i < 0:
        raise Refused("预注册里找不到判据起始标记 %r：%s" % (start, prereg_path))
    if j < 0:
        raise Refused("预注册里找不到判据结束标记 %r：%s" % (stop, prereg_path))
    return text[i:j].rstrip() + "\n"


def extract_selfreport(prereg_path):
    """从显式文本提取自述段并剥掉引用块，避免事后更正替代独立重算。"""
    text = read_text(prereg_path)
    i = text.find("### 1.1")
    j = text.find("### 1.3", i + 1)
    if i < 0 or j < 0:
        return "（未取到自述段）\n"
    kept = [ln for ln in text[i:j].splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(kept).strip() + "\n"


# ---------------------------------------------------------------- 回放场素材

def replay_inputs(manifest_path):
    """从显式清单返回段1、段2、段3；清单路径相对清单所在目录解析。"""
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        raw_inputs = manifest["raw_inputs"]
        criteria_path = manifest["criteria_path"]
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise ValueError("raw_inputs 必须是非空数组")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused("输入清单不可读或格式无效：%s" % exc)
    base = os.path.dirname(os.path.abspath(manifest_path))

    def resolve(path):
        if not isinstance(path, str) or not path:
            raise Refused("输入路径必须是非空字符串")
        return path if os.path.isabs(path) else os.path.join(base, path)

    items = []
    for entry in raw_inputs:
        if not isinstance(entry, dict):
            raise Refused("raw_inputs 每项必须是对象")
        path = resolve(entry.get("path"))
        if not os.path.isfile(path):
            raise Refused("原始输入缺失：%s" % path)
        items.append({"role": entry.get("role") or os.path.basename(path), "path": path,
                      "sha256": sha256_file(path), "content": read_text(path)})
    criteria_path = resolve(criteria_path)
    if not os.path.isfile(criteria_path):
        raise Refused("判据原文缺失：%s" % criteria_path)
    criteria = read_text(criteria_path)
    selfreport_path = manifest.get("selfreport_path")
    selfreport = read_text(resolve(selfreport_path)) if selfreport_path else "（未提供执行方自述）\n"
    return items, criteria, selfreport, criteria_path


# ---------------------------------------------------------------- prompt 组装

HEADER = """你是独立审计模型。你**没有**参与执行，看到的执行方文字全部是**自述**。

任务：对下面给出的原始产物做独立审计，判断其**科研结论是否成立**。
你必须自己重算，不得复述执行方的算式或结论。

## 你必须遵守的判定纪律

1. **三层区分**（每一条结论都要标明来源）：
   - 原始事实：直接写在原始文件里的字节
   - 程序检查结果：脚本跑出来、rc=0 的输出
   - **独立重算结果**：你**自己**从最原始那一层重新算出的数
   - 模型推断：你的判断
   - 需人工裁决：你判不了的
2. **重算必须在最原始的那一层**。执行方的派生汇总可能已经把错误吸收进去；
   复现它的算式 = 复现它的错误，**不是核验**。
3. **exit 0 不等于通过**。脚本 rc=0 只说明"跑完了"，不说明"算对了"、更不说明"判据过了"。
4. 你**只能提供意见**：不改协议、不提交正式结论、不绕过任何 gate。
5. 判不了就写 `NEEDS_HUMAN`，并在 `cannot_judge` 里写明缺什么。**不许猜、不许和事佬**。

## 必查 8 项

{checks}

## 输出格式（只输出**一个** JSON 对象，不要任何其他文字）

```json
{{
  "schema": "raw_audit/v1",
  "verdict": "PASS | FAIL | NEEDS_HUMAN",
  "auditor": {{"model": "<你的模型名>", "context": "fresh",
               "inherit_executor_summary": false}},
  "inputs_read": [{{"path": "...", "sha256": "..."}}],
  "checks": [{{"id": "<上列 8 个 id 之一>", "result": "PASS|FAIL|NEEDS_HUMAN",
               "evidence_path": "...", "detail": "..."}}],
  "issues": [{{"severity": "BLOCKING|MAJOR|MINOR", "impact_scope": "...",
               "detail": "..."}}],
  "suggested_next_step": "...",
  "cannot_judge": ["..."],
  "source_labels_applied": true
}}
```

`checks` 必须**恰好包含**上述 8 个 id 各一条。
若发现的问题不在 8 项之内，仍写进 `issues`，不要另造 check id。
"""


def build_prompt(segment1, criteria, selfreport, extra_expectation=""):
    parts = [HEADER.format(checks="\n".join("- `%s`" % c for c in CHECKS))]
    if extra_expectation:
        parts.append(extra_expectation)

    parts.append("\n\n" + "=" * 78)
    parts.append("### 段1 · 原始事实（路径 + sha256 + 原始内容）")
    parts.append("> 只给产物与代码。**不含**任何执行方结论、README、总账、对外文稿、验证记录。")
    for it in segment1:
        parts.append("\n---\n**role**: %s\n**path**: `%s`\n**sha256**: `%s`\n\n```\n%s\n```"
                     % (it["role"], it["path"], it["sha256"], it["content"]))

    parts.append("\n\n" + "=" * 78)
    parts.append("### 段2 · 协议与判据（预注册**原文**，逐字未改）")
    parts.append("> 只给文本。**不给**「执行方认为命中了哪一条」。")
    parts.append("\n```markdown\n%s\n```" % criteria)

    parts.append("\n\n" + "=" * 78)
    parts.append("### 段3 · 执行方自述")
    parts.append("⚠ **以下为执行方结论，属自述，非事实，不得作为推理前提。**")
    parts.append("⚠ 你**不得**因为自述里写了某个数字就采纳它；必须回到段1 独立重算。")
    parts.append("\n```markdown\n%s\n```" % selfreport)

    parts.append("\n\n" + "=" * 78)
    parts.append("现在输出**唯一一个** JSON 对象（schema `raw_audit/v1`）。")
    return "\n".join(parts)


REPLAY_EXPECTATION = """
## 本件性质：回放场（已知存在错误的算例）

这是一次**回放验证**：段1 与段3 来自一份**事后被确认含公式错误**的起草期算例。
本件**不告诉你错在哪**，也不告诉你正确答案是多少——那正是要检验的。
若你独立重算后认定该算例的方差结构读数**成立**，就照实写 PASS；
但若发现它与段1 最原始那层数据不自洽，必须写出 `FAIL` 并给出 `evidence_path`。
"""


# ---------------------------------------------------------------- 审计输出解析

def parse_audit_json(text, model):
    """从模型文本里抽 `raw_audit/v1` JSON。抽不出 → UNPARSEABLE（按 NEEDS_HUMAN 处理）。"""
    candidates = []
    start = text.find("{")
    while start >= 0:
        depth, end = 0, -1
        in_str, esc = False, False
        for k in range(start, len(text)):
            ch = text[k]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        if end > 0:
            candidates.append(text[start:end])
        start = text.find("{", start + 1)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("verdict") in ("PASS", "FAIL", "NEEDS_HUMAN"):
            obj.setdefault("schema", SCHEMA)
            obj.setdefault("auditor", {})
            obj["auditor"].setdefault("model", model)
            obj["auditor"].setdefault("context", "fresh")
            obj["auditor"]["inherit_executor_summary"] = False
            # 缺失的 check id 一律补 NEEDS_HUMAN，不当作通过
            have = {c.get("id") for c in obj.get("checks", []) if isinstance(c, dict)}
            for cid in CHECKS:
                if cid not in have:
                    obj.setdefault("checks", []).append(
                        {"id": cid, "result": "NEEDS_HUMAN", "evidence_path": None,
                         "detail": "审计模型未给出该项结论；按未判断处理，不计通过"})
            return obj, None
    return None, "UNPARSEABLE"


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--replay", required=True, help="合成用例名")
    ap.add_argument("--inputs", required=True, help="显式原始输入 JSON 清单")
    ap.add_argument("--expect-not-pass", action="store_true", help="已知错误夹具不得判 PASS")
    ap.add_argument("--dry-run", action="store_true", help="只组装 prompt，不发送")
    ap.add_argument("--list-inputs", action="store_true", help="列出审计输入并做污染检查")
    ap.add_argument("--out", help="审计结果输出绝对目录（只写新文件）")
    ap.add_argument("--model")
    ap.add_argument("--provider-config", help="Responses provider JSON；凭据只从环境变量读取")
    ap.add_argument("--responses-url", help="显式 Responses 端点")
    ap.add_argument("--api-key-env", help="凭据环境变量名；默认 RESEARCH_SESSION_API_KEY")
    ap.add_argument("--port", type=int, default=None, help="显式本地 Responses 端口；无默认")
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--max-output-tokens", type=int, default=None)
    args = ap.parse_args()
    model = args.model or "offline-unconfigured"

    try:
        seg1, criteria, selfreport, criteria_path = replay_inputs(args.inputs)
    except Refused as exc:
        emit({"ok": False, "stage": "replay_inputs", "error": str(exc)}, 2)

    input_paths = [it["path"] for it in seg1] + [criteria_path]
    try:
        assert_clean(input_paths)
    except Refused as exc:
        emit({"ok": False, "stage": "pollution_check", "error": str(exc)}, 2)

    if args.list_inputs:
        emit({
            "ok": True, "stage": "list_inputs", "replay": args.replay,
            "n_inputs": len(input_paths),
            "inputs": [{"role": it["role"], "path": it["path"], "sha256": it["sha256"],
                        "bytes": len(it["content"].encode("utf-8"))} for it in seg1]
                      + [{"role": "判据原文来源（段2，显式完整文件）",
                          "path": criteria_path, "sha256": sha256_file(criteria_path), "bytes": None}],
            "selfreport_source": {"note": "段3 自述，标注为非事实；由显式清单可选提供"},
            "forbidden_patterns_checked": list(FORBIDDEN_PATTERNS),
            "forbidden_hits": [],
            "criteria_chars": len(criteria),
        }, 0)

    prompt = build_prompt(seg1, criteria, selfreport, REPLAY_EXPECTATION)

    if args.dry_run:
        tmpdir = tempfile.gettempdir()
        p = os.path.join(tmpdir, "%s__audit_raw_prompt__%s.txt" % (now_stamp(), args.replay))
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        emit({"ok": True, "stage": "dry_run", "prompt_path": p,
              "prompt_sha256": sha256_file(p), "prompt_chars": len(prompt),
              "model": model, "sent": False,
              "note": "只组装未发送；请人工过目后再去掉 --dry-run"}, 0)

    if not args.out:
        emit({"ok": False, "stage": "args",
              "error": "送审需要 --out <dir>（只写新文件，不覆盖证据）"}, 2)

    # 先定位输出路径并**在发送前**做拒绝覆盖检查——否则会白烧一次 API 调用。
    outdir = args.out
    if not os.path.isabs(outdir):
        emit({"ok": False, "stage": "args", "error": "--out 必须是显式绝对目录"}, 2)
    os.makedirs(outdir, exist_ok=True)
    stamp = now_stamp()
    target = os.path.join(outdir, "%s__raw审计__%s.json" % (stamp, args.replay))
    if os.path.exists(target):
        emit({"ok": False, "stage": "refuse_overwrite",
              "error": "目标已存在，拒绝覆盖已有证据：%s" % target}, 2)

    dispatch = _load_dispatch()
    timeout = args.timeout or dispatch.DEFAULT_TIMEOUT
    mot = args.max_output_tokens or dispatch.DEFAULT_MAX_OUTPUT_TOKENS
    try:
        route = dispatch.responses_route(args, args.model)
        model = route["wire_model"]
        token = dispatch.provider_api_key(route)
    except dispatch.ProviderNotConfigured as exc:
        emit({"ok": False, "stage": "provider", "error": "provider_not_configured",
              "detail": str(exc), "request_started": False}, 2)

    log("POST explicit Responses endpoint model=%s" % model)
    try:
        status, raw = dispatch.responses_post(route, token, timeout, prompt, mot, True)
    except Exception as exc:
        emit({"ok": False, "stage": "dispatch",
              "error": "通道异常（不自动改道）：%s" % type(exc).__name__}, 2)
    if status != 200:
        emit({"ok": False, "stage": "dispatch", "http_status": status,
              "error": "上游未返回 200；复核未完成，绝不可当作通过",
              "body_head": raw[:400]}, 2)

    text = dispatch.extract_text(raw)
    audit, parse_err = parse_audit_json(text, model)
    verdict = audit["verdict"] if audit else "NEEDS_HUMAN"

    record = {
        "schema": SCHEMA,
        "replay": args.replay,
        "generated": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "channel": "raw-reading",
        "model": model,
        "context": "fresh",
        "inherit_executor_summary": False,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "inputs_read": [{"path": it["path"], "sha256": it["sha256"]} for it in seg1]
                        + [{"path": criteria_path, "sha256": sha256_file(criteria_path),
                            "role": "判据原文（段2）"}],
        "forbidden_patterns_checked": list(FORBIDDEN_PATTERNS),
        "verdict": verdict,
        "parse_error": parse_err,
        "audit": audit,
        "raw_text_path": None,
        "discipline": ("审计结果属**审查意见，不是证据，不改变证据层级**；"
                       "不构成自动登记或对外定稿的授权；不产生 FORMALLY_ACCEPTED。"),
    }
    if audit is None:
        tp = target[:-5] + "__raw_text.txt"
        with open(tp, "w", encoding="utf-8") as fh:
            fh.write(text)
        record["raw_text_path"] = tp

    with open(target, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    replay_ok = verdict != "PASS" if args.expect_not_pass else verdict in ("PASS", "FAIL")
    emit({
        "ok": replay_ok, "stage": "replay_audit", "replay": args.replay,
        "verdict": verdict, "parse_error": parse_err,
        "out": target, "sha256": sha256_file(target),
        "replay_acceptance": ("通过：verdict != PASS" if replay_ok else
                              "**失败**：对已知错算例给了 PASS ⇒ 审计器未通过回放验证，"
                              "不得上线（本设计唯一可先验判定的验收点）"),
        "issues_count": len((audit or {}).get("issues", [])),
        "note": "审计意见不是证据，不改变证据层级；verdict 永不直接产生 FORMALLY_ACCEPTED",
    }, 0 if replay_ok else 2)


if __name__ == "__main__":
    try:
        main()
    except Refused as exc:
        emit({"ok": False, "stage": "refused", "error": str(exc)}, 2)
