# -*- coding: utf-8 -*-
"""audit_dispatch.py -- 把 research-session 复核包送审到独立模型，去掉人工搬运。

通道：显式配置的 Responses provider（CLI、环境变量或 JSON 文件）。
      URL 与模型无隐式默认；凭据只从指定环境变量读入进程内存，不写入回执或日志。

纪律：
  - 包正文一经生成不改：只**追加**结论段，写回后复验前缀字节逐位一致；
  - fail-closed：通道失败 / 包不可读 / 必查项取不到 / 无明确总体裁决 -> exit 2
    （「复核未完成」），绝不当作通过；
  - **不做直连兜底**：不得因本地不可用自动改走外部直连 API。
    代理不可用时停下并如实报告，不擅自改道。

本脚本只产出**审查意见**，不是证据、不改变证据层级；拿到「放行」也**不**构成
自动推进登记或对外定稿的授权（见 autonomy-bounds.md「必须停下」表）。

用法（Git Bash）：
  python audit_dispatch.py --pack <包绝对路径> --point 1 --inputs <清单JSON> [--previous <回执>]
  python audit_dispatch.py ... --previous <上周期最终回执> --new-cycle <用户决定记录>
  python audit_dispatch.py --probe

退出码：0 = 当前绑定版本通过；3 = 待整改/不通过/达到轮次上限；2 = 未完成。
实际送审须 --inputs；整改复核须 --previous <上一轮 .review.json>。
新周期须 --new-cycle 决定记录 + --previous 上周期最终回执，且二者列入 --inputs 的 files。
stdout 只输出一行 ASCII 转义 JSON；过程信息走 stderr。
"""

import argparse
import datetime
import hashlib
import http.client
import importlib
import json
import os
import re
import sys
import socket
import threading
import time
import tempfile
from pathlib import Path
from typing import Any, NoReturn, cast
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
review_evidence = importlib.import_module("review_evidence")
review_ledger = importlib.import_module("review_ledger")
rule_sources = importlib.import_module("rule_sources")
run_manifest = importlib.import_module("run_manifest")

DEFAULT_PORT = None
DEFAULT_TIMEOUT = 1200
DEFAULT_MAX_OUTPUT_TOKENS = 32768
REVIEW_REASONING_EFFORT = "xhigh"
# 显式声明工具身份，不依赖 Python-urllib 的默认 UA。
DISPATCH_USER_AGENT = "research-session-dispatch/1.0"
# 已核实的旧合同版本；仅允许对该版本的特定格式失败进行显式迁移。
LEGACY_FORMAT_RULES_SHA256 = "8ae413ca0983351c65206f03c1c7b46e3b4db5b873132ca54ac14886016f62ac"

REFERENCE_MD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "references", "codex-review-pack.md",
)

# 离线 dry-run/夹具占位；实际发送必须由显式 provider 配置给出模型。
POINT_MODEL = {1: "offline-unconfigured", 2: "offline-unconfigured", 3: "offline-unconfigured"}
POINT_TIER = {
    1: "协议审计 / 关键裁决",
    2: "出数后复核",
    3: "对外措辞 / 关键裁决",
}

VERDICTS = ("不放行", "修改后放行", "放行")
DECISIONS = ("通过", "待整改", "不通过", "未完成")
FINDING_FIELDS = ("id", "kind", "location", "evidence", "impact", "change", "acceptance")
FINDING_KINDS = ("blocking", "advice", "execution_gate")
CLOSURE_STATES = ("已解决", "未解决")


def review_contract(checklist_count, previous=None, disputes=None, *, first_review=None) -> dict[str, Any]:
    """生产提示与离线预检共用；模板仅是格式样例，不是审查结论。"""
    if previous is not None and not isinstance(previous.get("report"), dict):
        raise ValueError("上一轮 report=null，不能作为实质复核基线；先 --validate-report 离线回放原意见")
    active = sorted(f["id"] for f in (previous or {}).get("report", {}).get("findings", [])
                    if f.get("kind") == "blocking")
    contract = {
        "version": "research-session/response-contract/v1.2",
        "required": ["decision", "summary", "checklist", "findings", "closed_findings"] + (["dispute_resolutions"] if disputes else []),
        "decision_enum": list(DECISIONS),
        "checklist": {"count": checklist_count, "item": "1..count, exact ordered integers",
                      "status": ["通过", "不适用", "存疑"], "reason": "nonempty unless status=通过"},
        "findings": {"required": list(FINDING_FIELDS), "kind_enum": list(FINDING_KINDS),
                     "evidence": "nonempty string OR object with nonempty actual_evidence; all other fields nonempty strings",
                     "optional": ["domain", "test_case", "patch", "repair", "verification_status", "quantitative_claim"],
                     "domain_enum": ["implementation", "scientific", "evidence", "wording"]},
        "repair": {"required_for": "blocking implementation findings", "required": ["status", "reason"],
                   "status_enum": ["patch_proposed", "needs_context", "needs_decision", "not_safely_patchable"],
                   "patch_proposed_requires": ["patch", "test_case", "targets[{path,sha256}]"],
                   "patch": "nonempty string containing an applicable patch; do not return a JSON object",
                   "test_case": "nonempty string OR nonempty JSON object, losslessly serialized",
                   "rule": "minimal implementation/test patch in the same response; patch authoring is not independent approval"},
        "closed_findings": {"required": ["id", "status", "evidence"], "status_enum": list(CLOSURE_STATES),
                            "evidence": "nonempty string OR object with nonempty actual_evidence, losslessly serialized",
                            "verification_status": list(review_evidence.STATUSES),
                            "rule": "cover every active_blocker_id exactly once; omitted verification_status is unverified, not verified"},
        "verification_status_enum": list(review_evidence.STATUSES),
        "quantitative_claim": {"required": ["artifact", "observed", "expected"],
                               "reproduction": "command argv OR json_pointer; a locator only, never executed by harness"},
        "disputes": disputes or [],
        "dispute_resolutions": {"ids": [d["id"] for d in disputes or []],
                                "required": ["id", "status", "evidence", "verification_status", "reproduction"],
                                "status_enum": ["accepted", "rejected", "unresolved"]},
        "active_blocker_ids": active,
        "response_template": {
            "decision": "通过", "summary": "格式示例，实际裁决须由证据支持。",
            "checklist": [{"item": i, "status": "通过", "reason": ""} for i in range(1, checklist_count + 1)],
            "findings": [],
            "closed_findings": [{"id": item, "status": "已解决", "evidence": "替换为本轮真实验证证据",
                                 "verification_status": "unverified"} for item in active],
            "dispute_resolutions": [{"id": d["id"], "status": "accepted", "evidence": "替换为对异议的实际核对证据",
                                     "verification_status": "unverified", "reproduction": d["reproduction"]} for d in disputes or []],
        },
    }
    if first_review is not None:
        contract["version"] = "research-session/response-contract/v1.3-candidate"
        contract["required"].append("claim_checks")
        contract["checklist"]["reason"] = "nonempty evidence locator or reason for every status"
        contract["checklist"]["finding_ids"] = "explicit array; each cited finding must exist"
        contract["checklist"]["missing_evidence"] = "optional nonempty string or list of nonempty strings; only for unresolved required evidence"
        contract["claim_checks"] = dict(contract["checklist"], count=len(first_review["claims"]))
        contract["findings"]["blocking_closure"] = {
            "local": {"mode": "local", "edits": [{"path": "exact allowlisted path", "before": "unique original text",
                                                 "after": "exact replacement text"}]},
            "recheck": {"mode": "recheck", "reason": "specific judgment still required"},
        }
        for row in contract["response_template"]["checklist"]:
            row.update(reason="替换为实际核对依据", finding_ids=[])
        contract["response_template"]["claim_checks"] = [
            {"item": i, "status": "通过", "reason": "替换为实际原件位置与对账结果", "finding_ids": []}
            for i in range(1, len(first_review["claims"]) + 1)
        ]
    return contract


def log(msg):
    # 控制台一律 ASCII：Windows PowerShell 5.1 按系统代码页读 UTF-8 会乱码。
    sys.stderr.write("[audit_dispatch] %s\n" % msg.encode("ascii", "backslashreplace").decode("ascii"))
    sys.stderr.flush()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


LAST_EMITTED: dict[str, Any] = {}


def emit(obj, code) -> NoReturn:
    obj.setdefault("review_completed", False)
    obj.setdefault("gate_passed", False)
    obj.setdefault("decision", "未完成")
    obj.setdefault("review_counted", False)
    obj.setdefault("disposition", "local_validation_only" if obj.get("local_only") or obj.get("dry_run") else "incomplete_stop")
    LAST_EMITTED.clear()
    LAST_EMITTED.update(obj)
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def read_current_provider():
    """兼容本地预检的探测点；公开版不读取任何隐式宿主 provider 配置。"""
    return "explicit-responses"


def load_checklist(point, reference_path):
    """从 codex-review-pack.md §一 按复核点抽取必查项（单一真源，不内嵌副本）。"""
    try:
        with open(reference_path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception as exc:
        raise RuntimeError("必查项真源不可读: %s (%s)" % (reference_path, exc))

    head = re.compile(r"^###\s*复核点\s*(\d+)\s*[·:：]?\s*(.*)$")
    title, items, inside = None, [], False
    for line in lines:
        m = head.match(line)
        if m:
            if inside:
                break
            if int(m.group(1)) == point:
                inside, title = True, m.group(2).strip() or ("复核点 %d" % point)
            continue
        if inside:
            if line.startswith("## "):
                break
            m2 = re.match(r"^\s*-\s*\[\s*\]\s*(.+?)\s*$", line)
            if m2:
                items.append(m2.group(1))
    if not items:
        raise RuntimeError("在 %s 中找不到复核点 %d 的必查项" % (reference_path, point))
    return title, items


def build_prompt(point, title, items, pack_text, previous=None, metadata=None):
    checklist = "\n".join("- %s" % it for it in items)
    metadata = metadata or {}
    first_review = metadata.get("first_review") if previous is None else None
    contract = json.dumps(review_contract(len(items), previous, metadata.get("disputes"), first_review=first_review), ensure_ascii=False)
    incremental = ""
    if first_review is not None:
        incremental = (
            "\n首审在同一请求中完成三步：\n"
            "1. 把固定必查项和输入清单 first_review.claims（按顺序从 1 编号）分别逐项查完。"
            "每项给原件位置和核对结果；不适用给范围依据，缺证给需要的原件/字段。"
            "发现一个阻断或缺证后继续查其他可审项。发现送审方未列出的关键主张时仍回溯并归到最相关的固定必查项。\n"
            "2. 从关键结论反查原始字段、适用版本、口径及分母；发现错误后横查同一根因的所有已知影响位置。"
            "同根因合并为一项并列全位置。覆盖行给非空 reason 和 finding_ids；存疑须关联阻断或填 missing_evidence。"
            "必要原件缺失填 missing_evidence、decision=未完成，同时保留所有已证实错误。已证实错与待核疑点分开。\n"
            "3. 每个阻断给原句/文件字段、实际与应有值、影响、最小改法和可检验验收。"
            "文字直接给替换句，代码上下文足够给补丁，不能确定就点名缺什么。每项给 closure："
            "只有展示层逐字更正、原证据与结论不变，且能对 first_review.editable_reports 内的文件"
            "给全部精确且唯一的 before/after 时才用 local；其余用 recheck 并指出仍需什么独立判断。"
            "待整改不等于已放行，任何 local 项不得替代科学硬门。提交前检查编号、引用和同根因遗漏。\n"
        )
    if previous:
        incremental = (
            "\n本次是整改复核：只检查旧阻断项、实际差异及受影响的依赖；"
            "旧问题沿用原 id。与修改无关的新问题只有在新证据使旧结论失效时才新增阻断；"
            "数据泄漏、协议矛盾或缺原始证据等科学硬门不受此限，原硬门仍须阻断。"
            "新增阻断须说明具体证据及受影响的原主张，"
            "不重审无变化的历史。closed_findings 必须逐项回应旧 blocking 项，"
            "状态只用 已解决/未解决，每项给具体验证证据；未解决项仍列入 findings。\n"
            "上一轮裁决与问题（不是整份旧包）：\n%s\n"
            % json.dumps(previous.get("report"), ensure_ascii=False)
        )
        if previous.get("binding", {}).get("first_review"):
            incremental += (
                "混合整改先逐项对照首审验收条件：local 项只核提交的精确变更及验收证据；"
                "recheck 项核查首审指出的待判断问题；所有旧编号仍须返回销项状态。\n"
            )
    if metadata.get("new_cycle"):
        incremental += (
            "\n本次是用户决定的新周期首次送审：新周期不继承上一周期结论；"
            "须核对包内用户决定原话与上一周期未解决 blocking 的对应关系。\n"
        )
    return (
        "你是独立审计模型。任务：对下面这份「复核包」做复核点 {point}（{title}）的裁决。\n"
        "\n"
        "纪律（必须遵守）：\n"
        "- 你只能看到这个包；不得假设包外还有别的证据可查。\n"
        "- 包内数字若未注明「路径 + 字段」，视为未核实。\n"
        "- 不得补全包中未给出的信息；缺失就写「未在包中给出」。\n"
        "- 对关键断言标注来源类别：论文原文 / 图表公式证据 / 代码仿真 / 上下文推断 / 建议。\n"
        "  推断不得冒充已证实。\n"
        "- 宁可指出不确定，也不要为了给出结论而编造依据。\n"
        "\n"
        "本复核点必查项：\n{checklist}\n"
        "\n"
        "只返回一个 JSON 对象，不加 Markdown 或长篇复述。字段合同：\n"
        "decision: 通过/待整改/不通过/未完成；summary: 一句结论；\n"
        "checklist: 按必查项顺序列出 {{item:从1开始的编号,status:通过/不适用/存疑,reason:理由}}；"
        "reason 按下方当前合同填写。\n"
        "findings: 问题数组，每项含 id,kind,location,evidence,impact,change,acceptance；"
        "kind 只用 blocking/advice/execution_gate。location 定位具体文件和字段/行；"
        "evidence 含来源类别和实际证据，acceptance 是可检验的销项条件。\n"
        "首轮一次列全当前阶段可证实的阻断，逐项写清修改位置、具体改法及验收条件；"
        "无法安全给出补丁时说明缺少的具体上下文或取舍。\n"
        "blocking 只用于影响当前阶段有效性的真实缺陷；一般优化列 advice；"
        "未来执行门列 execution_gate，不因当前未获运行授权而否决文档，"
        "也不得将当前硬门改名推迟。不要为了挑错而编造问题，无问题则 findings=[]。\n"
        "通过须无 blocking 且所有必查项通过或有依据地不适用；"
        "待整改/不通过须列具体 blocking；关键材料不足则未完成。"
        "不用“修改后放行”。\n"
        "实现层 blocking 须给本对象可达的具体反例、触发条件和验收断言；"
        "只是假设“可能还有绕过”而无证据则列 advice。"
        "数据/论文缺证、泄漏或科学协议矛盾可由原件检查证明，不强求伪造程序测试。"
        "协议审查与实现附件分开，未来运行门不当作设计缺陷。"
        "整改达到原验收条件就关闭该项；新阻断须指出具体反例/原件冲突及受影响主张，不能要求证明不存在一切漏洞。"
        "必要时在 test_case 给完整最小输入/调用/预期断言，在 patch 给仅限实现和测试的最小补丁建议；"
        "不得改阈值、seed、预算、科学判词或执行权限。\n"
        "修复优先：每项finding用domain区分implementation/scientific/evidence/wording。"
        "blocking implementation必须同次给repair，不得只写请修改后重审。"
        "有足够原文时给status=patch_proposed、reason、可应用patch、完整test_case及targets[{{path,sha256}}]；"
        "补丁只碰当前授权实现和测试，不改科学设定、配置或原始证据。缺上下文用needs_context并指出精确片段，"
        "涉及取舍用needs_decision，否则not_safely_patchable并说明具体原因，不伪造补丁。"
        "主会话核对SHA和范围后实际落盘/验证；写了补丁不等于已修改或独立验收通过。\n"
        "所有销项及时间线用 verification_status: verified_local/verified_external/unverified/absent；"
        "旧记录缺状态只表示unverified，不能推认为已验证。未采集的非必要过程记录标absent并限制相关主张，"
        "不无限索取或据此阻断；泄漏、协议矛盾、缺原始证据等科学硬门不因缺记录豁免，不能追认历史盲注册。\n"
        "你提出计数、比例等可复算主张时，须在对应finding的quantitative_claim给artifact、observed、expected和"
        "command(argv)或json_pointer，写清分母与口径；无复算依据不能作为blocking。包内命令只作证据定位，不执行。"
        "disputes须在同一轮dispute_resolutions逐项接受/反驳/保留未决并给复算依据；单一计数纠正不自动关闭该项其他缺陷。\n"
        "closed_findings: 首审为空数组，整改复核按下文要求填写；"
        "如包内有具体问题，可另列 answers 简答，不重复完整必查项。\n"
        "{incremental}\n"
        "以下合同与当前 active_blocker_ids 是本次生产/消费共用的精确格式，"
        "不要从历史叙述臆测当前销项集合；response_template 不是预设放行：\n{contract}\n"
        "本地核验的包证据及显式状态（不是科学通过，也不替代所需原文）：\n{metadata}\n"
        "\n"
        "以下是复核包全文：\n"
        "---\n"
        "{pack}\n"
        "---\n"
    ).format(point=point, title=title, checklist=checklist, pack=pack_text,
             incremental=incremental, contract=contract, metadata=json.dumps(metadata, ensure_ascii=False))


class ProviderNotConfigured(ValueError):
    """Online transport is disabled until an explicit Responses provider exists."""


def _provider_config(args):
    source = getattr(args, "provider_config", None) or os.environ.get("RESEARCH_SESSION_PROVIDER_CONFIG")
    if not source:
        return {}
    try:
        if source.lstrip().startswith("{"):
            config = json.loads(source)
        else:
            with open(source, "r", encoding="utf-8") as fh:
                config = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ProviderNotConfigured("provider config is unreadable or invalid JSON") from exc
    if not isinstance(config, dict) or set(config) - {"url", "model", "provider", "api_key_env"}:
        raise ProviderNotConfigured("provider config supports only url, model, provider and api_key_env")
    return config


def responses_route(args, model):
    """Build a secret-free route; both endpoint and model are mandatory for sending."""
    config = _provider_config(args)
    cli_url = getattr(args, "responses_url", None)
    port = getattr(args, "port", None)
    if cli_url and port is not None:
        raise ProviderNotConfigured("use either --responses-url or --port, not both")
    url = cli_url or os.environ.get("RESEARCH_SESSION_RESPONSES_URL") or config.get("url")
    if port is not None:
        url = "http://127.0.0.1:%d/v1/responses" % port
    wire_model = model or config.get("model") or os.environ.get("RESEARCH_SESSION_MODEL")
    if not url or not wire_model:
        raise ProviderNotConfigured("provider_not_configured: set Responses URL and model explicitly")
    if not isinstance(url, str) or not isinstance(wire_model, str) or not wire_model.strip():
        raise ProviderNotConfigured("provider URL and model must be nonempty strings")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or
            parsed.password or parsed.query or parsed.fragment or not parsed.path.endswith("/responses")):
        raise ProviderNotConfigured("Responses URL must be an http(s) .../responses endpoint without credentials/query")
    key_env = getattr(args, "api_key_env", None)
    if key_env is None:
        key_env = config["api_key_env"] if "api_key_env" in config else os.environ.get(
            "RESEARCH_SESSION_API_KEY_ENV", "RESEARCH_SESSION_API_KEY")
    if key_env is not None and (not isinstance(key_env, str) or not key_env):
        raise ProviderNotConfigured("api_key_env must be null or a nonempty environment variable name")
    provider = config.get("provider", "responses")
    if not isinstance(provider, str) or not provider:
        raise ProviderNotConfigured("provider name must be a nonempty string")
    return {"transport": "responses", "url": url, "wire_model": wire_model,
            "provider": provider, "api_key_env": key_env}


def provider_api_key(route):
    name = route.get("api_key_env")
    return os.environ.get(name) if name else None


def optional_responses_route(args, model):
    try:
        return responses_route(args, model)
    except ProviderNotConfigured:
        # 只认端点/provider 配置；单给模型名不构成在线通道，dry-run 仍可离线组装绑定。
        configured = any((getattr(args, "provider_config", None), getattr(args, "responses_url", None),
                          getattr(args, "port", None), os.environ.get("RESEARCH_SESSION_PROVIDER_CONFIG"),
                          os.environ.get("RESEARCH_SESSION_RESPONSES_URL")))
        if configured:
            raise
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_with_deadline(req, timeout, stream, no_redirect=False):
    """One local request with a wall-clock limit; timeout never authorizes a resend."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout
    timers = []
    sockets = []

    def arm_deadline(sock):
        sockets.append(sock)
        remaining = max(0, deadline - time.monotonic())
        def interrupt():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(remaining, interrupt)
        timer.daemon = True
        timers.append(timer)
        timer.start()

    def limit_read_timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("review request deadline exceeded; upstream status unknown")
        for sock in sockets:
            if sock.fileno() >= 0:
                sock.settimeout(remaining)

    class DeadlineConnection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            arm_deadline(self.sock)

    class DeadlineHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            arm_deadline(self.sock)

    class DeadlineHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(DeadlineConnection, request)

    class DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(DeadlineHTTPSConnection, request, context=self._context)

    handlers = [urllib.request.ProxyHandler({}), DeadlineHandler(), DeadlineHTTPSHandler()]
    if no_redirect:
        handlers.append(NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    try:
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        with resp:
            status = resp.status if hasattr(resp, "status") else resp.code
            content_type = (resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else "")
            sse = stream and "text/event-stream" in content_type.lower()
            if not hasattr(resp, "read1"):
                limit_read_timeout()
                return status, resp.read().decode("utf-8", "replace")
            chunks = []
            pending = b""
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("review request deadline exceeded; upstream status unknown")
                limit_read_timeout()
                chunk = resp.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if sse:
                    pending = (pending + chunk).replace(b"\r\n", b"\n")
                    while b"\n\n" in pending:
                        frame, pending = pending.split(b"\n\n", 1)
                        event_type = None
                        for line in frame.split(b"\n"):
                            if line.startswith(b"event:"):
                                event_type = line[6:].strip().decode("utf-8", "replace")
                            elif line.startswith(b"data:"):
                                try:
                                    event = json.loads(line[5:].strip())
                                except (ValueError, TypeError):
                                    continue
                                if isinstance(event, dict) and event.get("type"):
                                    event_type = event["type"]
                        if event_type in {"response.completed", "response.failed", "response.incomplete", "response.cancelled", "error"}:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("review request deadline exceeded; upstream status unknown")
                            log("received terminal event: %s" % event_type)
                            return status, b"".join(chunks).decode("utf-8", "replace")
            if time.monotonic() >= deadline:
                raise TimeoutError("review request deadline exceeded; upstream status unknown")
            return status, b"".join(chunks).decode("utf-8", "replace")
    except Exception as exc:
        if time.monotonic() >= deadline:
            raise TimeoutError("review request deadline exceeded; upstream status unknown") from exc
        raise
    finally:
        for timer in timers:
            timer.cancel()


def responses_post(route, token, timeout, prompt, max_output_tokens, stream, reasoning_effort=None):
    payload = {"model": route["wire_model"], "input": prompt, "stream": bool(stream),
               "max_output_tokens": max_output_tokens}
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    req = urllib.request.Request(route["url"], data=json.dumps(payload).encode("utf-8"), method="POST")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream" if stream else "application/json")
    req.add_header("User-Agent", DISPATCH_USER_AGENT)
    status, raw = _request_with_deadline(req, timeout, stream, no_redirect=True)
    return status, raw.replace(token, "[redacted]") if token else raw


def http_get_models(url, timeout, token=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("User-Agent", DISPATCH_USER_AGENT)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return resp.status, raw.replace(token, "[redacted]") if token else raw


def http_post_responses(port, timeout, model, prompt, max_output_tokens, stream=True, reasoning_effort=None):
    """显式本地端口的低层测试入口；生产发送走 responses_route/responses_post。"""
    url = "http://127.0.0.1:%d/v1/responses" % port
    payload = {
        "model": model,
        "input": prompt,
        "stream": bool(stream),
        "max_output_tokens": max_output_tokens,
    }
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream" if stream else "application/json")
    req.add_header("User-Agent", DISPATCH_USER_AGENT)
    return _request_with_deadline(req, timeout, stream, no_redirect=True)


# A transport error or 5xx does not prove that the upstream did not process the request.
RETRY_ATTEMPTS = 1
REQUEST_STARTED = False
RECEIPT_PUBLISHED = False
GUARDED_ID = None
GUARD_INSTANCE = None


def post_with_retry(port, timeout, model, prompt, max_output_tokens, stream, reasoning_effort=None):
    return http_post_responses(port, timeout, model, prompt, max_output_tokens, stream, reasoning_effort)


def has_failed_response(raw):
    """拒绝失败、未完成或缺终态的 SSE，即便已经收到完整裁决文字。"""
    failed_types = {"error", "response.failed", "response.incomplete", "response.cancelled"}
    saw_sse, completed, event_name = False, False, None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            event_name = None
        elif line.startswith("event:"):
            event_name = line[6:].strip()
            if event_name in failed_types:
                return True
        elif line.startswith("data:"):
            saw_sse = True
            try:
                event = json.loads(line[5:].strip())
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict):
                payload_type = event.get("type")
                if event_name and payload_type and event_name != payload_type:
                    return True
                kind = payload_type or event_name
                if kind in failed_types:
                    return True
                if kind == "response.completed":
                    response = event.get("response")
                    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error"):
                        return True
                    completed = True
    try:
        response = json.loads(raw)
    except (ValueError, TypeError):
        return saw_sse and not completed
    return isinstance(response, dict) and (
        response.get("status") not in (None, "completed")
        or response.get("type") in failed_types
        or bool(response.get("error"))
    )


def _text_from_sse(raw):
    """只提取最终正文或正文增量，不取 reasoning / 工具参数。"""
    chunks = []
    final_text = ""
    event_name = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            event_name = None
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except Exception:
            continue
        if not isinstance(evt, dict):
            continue
        kind = evt.get("type") or event_name
        if kind == "response.completed" and isinstance(evt.get("response"), dict):
            final_text = _text_from_json_payload(evt["response"])
        if "output_text" not in str(kind or ""):
            continue
        delta = evt.get("delta")
        if isinstance(delta, str):
            chunks.append(delta)
    return final_text or "".join(chunks).strip()


def _text_from_json_payload(payload):
    out = payload.get("output")
    if isinstance(out, list):
        chunks = []
        for item in out:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("output_text", "text")
                    and isinstance(part.get("text"), str)
                ):
                    chunks.append(part["text"])
        if chunks:
            return "\n".join(chunks).strip()

    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        if isinstance(msg.get("content"), str) and msg["content"].strip():
            return msg["content"].strip()
    return ""


def extract_text(raw):
    """兼容 Responses / chat-completions / SSE 三种返回形状。

    注意顺序：SSE 流以 `event: response.created` 开头（**不是** `data:`），
    所以先试 JSON，失败再按 SSE 解析——不能只看开头是不是 `data:`。
    """
    try:
        payload = json.loads(raw.lstrip())
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return _text_from_json_payload(payload)
    if "data:" in raw:
        return _text_from_sse(raw)
    return ""


def parse_verdict(text):
    """旧调用方兼容入口；不再用关键词子串推断通过。主流程另验 JSON 合同。"""
    lines = re.findall(r"^\s*总体裁决\s*[:：]\s*(\S+)\s*$", text, re.MULTILINE)
    if len(lines) == 1 and lines[0] in VERDICTS:
        return lines[0], True
    return "未识别", False


def parse_review(text, checklist_count, previous=None, disputes=None, *, first_review=None):
    contract = review_contract(checklist_count, previous, disputes, first_review=first_review)
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("decision") not in contract["decision_enum"]:
        raise ValueError("裁决必须是结构化的 通过/待整改/不通过/未完成")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise ValueError("缺少 summary")
    checks = data.get("checklist")
    if not isinstance(checks, list) or len(checks) != checklist_count:
        raise ValueError("必查项缺失或数量不符")
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict) or type(check.get("item")) is not int or check["item"] != index:
            raise ValueError("必查项编号不完整")
        if check.get("status") not in ("通过", "不适用", "存疑"):
            raise ValueError("必查项状态无效")
        if check["status"] != "通过" and not str(check.get("reason") or "").strip():
            raise ValueError("不适用/存疑必须给出理由")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("缺少 findings 数组")
    ids, blockers = set(), set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError("问题必须是对象")
        evidence = finding.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("actual_evidence"), str) and evidence["actual_evidence"].strip():
            # 无损归一，不从 change/impact 臆造 evidence，也不改变模型裁决。
            finding["evidence"] = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        for key in contract["findings"]["required"]:
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                raise ValueError("findings[%d].%s: 缺少非空字段或有效 evidence 对象" % (index, key))
        if finding["id"] in ids or finding["kind"] not in contract["findings"]["kind_enum"]:
            raise ValueError("问题编号重复或分类无效")
        ids.add(finding["id"])
        finding.setdefault("verification_status", "unverified")
        review_evidence.check_status(finding)
        if "domain" in finding and finding["domain"] not in contract["findings"]["domain_enum"]:
            raise ValueError("finding.domain无效")
        repair = finding.get("repair")
        if repair is not None or (finding.get("domain") == "implementation" and finding["kind"] == "blocking"):
            if not isinstance(repair, dict) or repair.get("status") not in contract["repair"]["status_enum"] or not isinstance(repair.get("reason"), str) or not repair["reason"].strip():
                raise ValueError("实现阻断须同次给repair补丁或不能安全修改的具体原因")
            if repair["status"] == "patch_proposed":
                if isinstance(repair.get("test_case"), dict) and repair["test_case"]:
                    repair["test_case"] = json.dumps(repair["test_case"], ensure_ascii=False, sort_keys=True)
                if any(not isinstance(repair.get(k), str) or not repair[k].strip() for k in ("patch", "test_case")):
                    raise ValueError("repair.patch须为非空字符串，test_case须为非空字符串或对象")
                if not isinstance(repair.get("targets"), list) or not repair["targets"]:
                    raise ValueError("repair补丁须绑定targets路径及SHA256")
                for target in repair["targets"]:
                    if not isinstance(target, dict) or not isinstance(target.get("path"), str) or not target["path"] or not isinstance(target.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", target["sha256"]):
                        raise ValueError("repair.targets路径或SHA256无效")
        if "quantitative_claim" in finding:
            review_evidence.check_reproduction(finding["quantitative_claim"])
        if finding["kind"] == "blocking":
            blockers.add(finding["id"])
    decision = data["decision"]
    if decision == "通过" and (blockers or any(c["status"] == "存疑" for c in checks)):
        raise ValueError("通过与未关闭阻断/必查项存疑矛盾")
    if decision in ("待整改", "不通过") and not blockers:
        raise ValueError("整改/否决须列具体阻断项")
    closed = data.get("closed_findings")
    if not isinstance(closed, list):
        raise ValueError("缺少 closed_findings 数组")
    for item in closed:
        if not isinstance(item, dict):
            raise ValueError("销项必须是对象")
        evidence = item.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("actual_evidence"), str) and evidence["actual_evidence"].strip():
            item["evidence"] = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        item.setdefault("verification_status", "unverified")
        review_evidence.check_status(item)
    if previous:
        old = set(contract["active_blocker_ids"])
        seen, active_closed, historical = set(), [], []
        for item in closed:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or item["id"] in seen:
                raise ValueError("旧问题销项编号无效或重复；当前必销项: %s" % sorted(old))
            if item.get("status") not in CLOSURE_STATES or not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
                raise ValueError("旧问题销项缺少状态/验证证据")
            seen.add(item["id"])
            if item["id"] not in old:
                if item["status"] != "已解决" or item["id"] in blockers:
                    raise ValueError("非当前销项只能保留为已解决的历史注记；当前必销项: %s" % sorted(old))
                historical.append(dict(item, verified_in_current_round=False))
                continue
            active_closed.append(item)
            if item["status"] == "未解决" and item["id"] not in blockers:
                raise ValueError("未解决的旧问题仍须列为阻断")
            if item["status"] == "已解决" and item["id"] in blockers:
                raise ValueError("旧问题销项与阻断清单矛盾")
        if not old.issubset(seen):
            raise ValueError("未逐项回应旧阻断项，缺少: %s；不允许模糊映射 ID" % sorted(old - seen))
        data["closed_findings"] = active_closed
        if historical:
            data["historical_closed_findings"] = historical
    if disputes:
        responses = data.get("dispute_resolutions")
        if not isinstance(responses, list):
            raise ValueError("缺少 dispute_resolutions")
        seen = set()
        expected = {d["id"] for d in disputes}
        for row in responses:
            if not isinstance(row, dict) or row.get("id") not in expected or row["id"] in seen:
                raise ValueError("异议回应ID缺失/重复/无效")
            seen.add(row["id"])
            if row.get("status") not in ("accepted", "rejected", "unresolved") or not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
                raise ValueError("异议回应缺少有效状态/证据")
            review_evidence.check_status(row)
            review_evidence.check_reproduction(row.get("reproduction"))
            if row["status"] == "unresolved" and decision == "通过":
                raise ValueError("异议未决不能自动通过")
        if seen != expected:
            raise ValueError("未逐项回应全部disputes")
    if first_review is not None:
        validate_first_review(data, first_review, ids, blockers)
    return data


def validate_first_review(report, scope, ids, blockers):
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    claims = report.get("claim_checks")
    if not isinstance(claims, list) or len(claims) != len(scope["claims"]):
        raise ValueError("首审关键主张核对项不全")
    linked = set()
    for rows in (report["checklist"], claims):
        for index, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or type(row.get("item")) is not int or row["item"] != index
                    or row.get("status") not in ("通过", "不适用", "存疑") or not nonempty(row.get("reason"))):
                raise ValueError("首审每条覆盖记录须有正确编号、状态、原件定位/理由")
            refs = row.get("finding_ids")
            if (not isinstance(refs, list) or any(not isinstance(key, str) or key not in ids for key in refs)
                    or len(set(refs)) != len(refs)):
                raise ValueError("覆盖记录 finding_ids 缺失、重复或未定义")
            gap = "missing_evidence" in row
            has_blocker = bool(blockers.intersection(refs))
            if gap:
                missing = row["missing_evidence"]
                if (report["decision"] != "未完成"
                        or not (nonempty(missing) or isinstance(missing, list) and missing and all(map(nonempty, missing)))):
                    raise ValueError("必要缺证须说明具体原件/字段且保持未完成")
            if (row["status"] == "存疑") != (gap or has_blocker):
                raise ValueError("存疑与缺证/阻断引用不一致")
            linked.update(refs)
    if not blockers.issubset(linked) or report["closed_findings"]:
        raise ValueError("首审阻断必须可回溯到覆盖项，不能提前销项")
    if report["decision"] == "未完成" and not any(
            "missing_evidence" in row for row in report["checklist"] + claims):
        raise ValueError("有效首审报告的未完成须定位必要缺证；传输及格式错误另行记录")
    for finding in report["findings"]:
        if finding["id"] not in blockers:
            continue
        closure = finding.get("closure")
        if not isinstance(closure, dict):
            raise ValueError("首审阻断必须明确 closure")
        if closure.get("mode") == "recheck" and set(closure) == {"mode", "reason"} and nonempty(closure["reason"]):
            continue
        edits = closure.get("edits")
        if (closure.get("mode") != "local" or set(closure) != {"mode", "edits"}
                or finding.get("domain") != "wording" or "repair" in finding
                or not isinstance(edits, list) or not edits):
            raise ValueError("本地销项只支持明确的展示层逐字更正")
        for edit in edits:
            if (not isinstance(edit, dict) or set(edit) != {"path", "before", "after"}
                    or edit["path"] not in scope["editable_reports"]
                    or not all(nonempty(edit[k]) for k in edit)
                    or edit["before"] == edit["after"]):
                raise ValueError("本地更正必须有允许的报告路径和精确、非空的替换文本")


def validate_first_review_scope(scope, files):
    if not isinstance(scope, dict) or set(scope) != {"claims", "editable_reports"}:
        raise ValueError("first_review 须包含 claims 和 editable_reports")
    claims, reports = scope["claims"], scope["editable_reports"]
    bound = {row["path"] for row in files}
    if (not isinstance(claims, list) or not claims
            or any(not isinstance(claim, str) or not claim.strip() for claim in claims)
            or len(set(claim.strip() for claim in claims)) != len(claims)
            or not isinstance(reports, list)
            or any(not isinstance(path, str) or path not in bound
                   or Path(path).suffix.lower() not in (".md", ".txt") for path in reports)
            or len(set(reports)) != len(reports)):
        raise ValueError("first_review 要求非空、无重复的关键主张以及已绑定的绝对报告路径（.md/.txt）")


def load_inputs(path):
    if not path:
        raise ValueError("实际送审须 --inputs <含 review_id 和 files 的 JSON 清单>")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("review_id"), str) or not data["review_id"].strip():
        raise ValueError("输入清单缺少稳定 review_id")
    if not isinstance(data.get("files"), list) or not data["files"]:
        raise ValueError("输入清单 files 不能为空")
    files, seen = [], set()
    for row in data["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not row["path"].strip():
            raise ValueError("输入路径无效")
        target = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(path)), row["path"]))
        identity = os.path.normcase(target)
        if identity in seen:
            raise ValueError("输入清单含重复路径")
        seen.add(identity)
        expected = row.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise ValueError("输入缺少完整 SHA256: %s" % target)
        with open(target, "rb") as fh:
            actual = sha256_hex(fh.read())
        if actual != expected.lower():
            raise ValueError("输入 SHA256 漂移: %s" % target)
        files.append({"path": target, "sha256": actual})
    result = {"review_id": data["review_id"], "files": sorted(files, key=lambda row: row["path"])}
    if "first_review" in data:
        validate_first_review_scope(data["first_review"], files)
        result["first_review"] = data["first_review"]
    for key in ("timeline", "closures", "disputes", "verifications", "remediation_progress"):
        if key in data:
            result[key] = data[key]
    if "verifications" in result:
        if not isinstance(result["verifications"], list) or not all(isinstance(p, str) and p for p in result["verifications"]):
            raise ValueError("verifications必须是路径数组")
        result["verifications"] = [os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(path)), p)) for p in result["verifications"]]
    review_evidence.validate_metadata(result, files)
    return result


def rules_binding(reference: str):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    evidence_path = review_evidence.__file__
    if not isinstance(evidence_path, str):
        raise ValueError("review_evidence模块没有可绑定的源码路径")
    digest = rule_sources.digest(root, reference)
    manifest_path = os.path.join(root, "references", "review-rules-compatibility.json")
    return {"rules_sha256": digest,
            "compatibility_manifest_sha256": sha256_hex(open_bytes(manifest_path))}


def make_binding(args: argparse.Namespace, model: str, body_sha: str, *, for_send: bool = True):
    options = cast(dict[str, object], vars(args))
    inputs_path, pack_path, reference, point = (options[name] for name in ("inputs", "pack", "reference", "point"))
    if (not isinstance(inputs_path, str) or not isinstance(pack_path, str)
            or not isinstance(reference, str) or type(point) is not int):
        raise ValueError("复核绑定需要文本路径与整数复核点")
    inputs = load_inputs(inputs_path)
    if point != 3 and inputs.get("first_review", {}).get("editable_reports"):
        raise ValueError("只有复核点3可以声明本地更正报告")
    if os.path.normcase(os.path.realpath(pack_path)) in {os.path.normcase(f["path"]) for f in inputs["files"]}:
        raise ValueError("输入清单不得包含将被追加意见的复核包自身")
    result = dict(inputs, point=point, model=model, reasoning_effort_requested=REVIEW_REASONING_EFFORT,
                  pack_sha256=body_sha, **rules_binding(reference))
    route = None if (getattr(args, "unattended_state", None) or getattr(args, "local_only", False)) else optional_responses_route(args, model)
    if route is not None:
        result["route"] = route
    if getattr(args, "task_approval", None):
        import review_task
        scope = review_ledger.validate_scope(pack_path, result)
        result["task_approval"] = review_task.validate(
            args.task_approval, result, scope, args.timeout, for_send=for_send)
    return result


def compatible_rules_transition(previous_hash, current_binding):
    """Only reviewed exact version pairs can continue; unknown changes fail closed."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "references", "review-rules-compatibility.json")
    raw = open_bytes(path)
    if sha256_hex(raw) != current_binding["compatibility_manifest_sha256"]:
        raise ValueError("规则兼容清单在预检中发生变化")
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("current_rules_sha256") != current_binding["rules_sha256"]
            or previous_hash not in manifest.get("compatible_previous", {})):
        raise ValueError("整改复核与基线的 rules_sha256 不一致，且无已核实的兼容版本")
    reason = manifest["compatible_previous"][previous_hash]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("规则兼容记录缺少说明")
    return {"previous_rules_sha256": previous_hash, "current_rules_sha256": current_binding["rules_sha256"],
            "compatibility_manifest_sha256": sha256_hex(raw), "reason": reason}


def load_receipt(path, checklist_count, *, raw=None):
    data = json.loads(open_bytes(path) if raw is None else raw)
    if not isinstance(data, dict) or data.get("schema") != "research-session/review/v1" or type(data.get("round")) is not int or data["round"] < 0:
        raise ValueError("复核回执格式/轮次无效")
    unattended = data.get("unattended")
    task = data.get("binding", {}).get("task_approval")
    if data["round"] > 2 and not unattended and (
            not isinstance(task, dict) or task.get("mode") not in ("autonomous", "unattended")
            or not all(isinstance(task.get(k), str) and task[k] for k in ("path", "sha256", "task_id"))):
        raise ValueError("后续整改回执缺少任务批准绑定")
    if ("unattended" in data and
        (not isinstance(unattended, dict) or set(unattended) != {"run_id", "action_id", "round"} or
         any(not isinstance(unattended[key], str) or not unattended[key] for key in ("run_id", "action_id")) or
         type(unattended["round"]) is not int or unattended["round"] not in (1, 2, 3) or
         (data.get("report") is not None and data["round"] != unattended["round"]))):
        raise ValueError("无人值守复核回执缺少运行与轮次绑定")
    if "cycle" in data and (type(data["cycle"]) is not int or data["cycle"] < 1):
        raise ValueError("复核回执 cycle 无效")
    pack = data["pack"]
    if not isinstance(pack, dict) or type(pack.get("before_bytes")) is not int or pack["before_bytes"] <= 0:
        raise ValueError("复核包前缀绑定无效")
    with open(pack["path"], "rb") as fh:
        content = fh.read()
    if sha256_hex(content) != pack["after_sha256"] or sha256_hex(content[:pack["before_bytes"]]) != data["binding"]["pack_sha256"]:
        raise ValueError("旧复核包或回执绑定已漂移")
    if data.get("report") is not None:
        if data["round"] == 0:
            raise ValueError("有效审查回执的 round 不能为0")
        binding = data["binding"]
        first_review = binding.get("first_review") if data["round"] == 1 else None
        if first_review is not None:
            validate_first_review_scope(first_review, binding["files"])
        parse_review(json.dumps(data["report"], ensure_ascii=False), checklist_count,
                     disputes=binding.get("disputes"), first_review=first_review)
    return data


def cycle_of(receipt):
    """Cycle number; old receipts default to 1, and a __cN suffix is honored."""
    value = receipt.get("cycle", 1)
    if type(value) is not int or value < 1:
        raise ValueError("复核回执 cycle 无效")
    review_id = receipt.get("binding", {}).get("review_id", "")
    match = re.match(r"^.+__c(\d+)$", review_id.strip(), re.IGNORECASE) if isinstance(review_id, str) else None
    if match:
        value = max(value, int(match.group(1)))
    return value


def derived_cycle_root(review_id):
    if not isinstance(review_id, str):
        return None
    match = re.match(r"^(.+)__c\d+$", review_id.strip(), re.IGNORECASE)
    return match.group(1) if match else None


def prepare_new_cycle(args, previous, binding):
    """Validate a user-decided new cycle; return evidence metadata, not an approval."""
    if previous.get("unattended"):
        raise ValueError("离线夹具回执不得开启正式复核周期")
    decision = (previous.get("report") or {}).get("decision")
    terminal_two_rounds = previous["round"] == 2 and previous.get("report") is not None
    rejected_first = previous["round"] == 1 and decision == "不通过"
    if not (terminal_two_rounds or rejected_first):
        raise ValueError("新周期只能在上周期两轮结束或首轮明确否决后开启")
    previous_cycle = cycle_of(previous)
    previous_id = previous["binding"]["review_id"]
    root = derived_cycle_root(previous_id) or previous_id
    required = "%s__c%d" % (root, previous_cycle + 1)
    if binding["review_id"] != required:
        raise ValueError("新周期 review_id 必须为 %s" % required)
    if os.path.normcase(os.path.realpath(os.path.dirname(args.pack))) != os.path.normcase(
            os.path.realpath(os.path.dirname(args.previous))):
        raise ValueError("新周期包须与上一周期回执放在同一 reviews 目录")
    bound = {os.path.normcase(f["path"]) for f in binding["files"]}
    for label, path in (("决定记录", args.new_cycle), ("上一周期回执", args.previous)):
        if os.path.normcase(os.path.realpath(path)) not in bound:
            raise ValueError("新周期%s必须列入 --inputs 的 files" % label)
    record_raw = open_bytes(args.new_cycle)
    try:
        text = record_raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("新周期决定记录须为 UTF-8")
    if not text.strip():
        raise ValueError("新周期决定记录为空")
    previous_sha = sha256_hex(open_bytes(args.previous))
    checks = (
        ("上一周期 review_id",
         r"(?m)^[ \t]*(?:[-*][ \t]*)?上一周期[ \t]*review_id[ \t]*[：:][ \t]*%s[ \t]*\r?$"
         % re.escape(previous_id)),
        ("上一周期回执 SHA256",
         r"(?m)^[ \t]*(?:[-*][ \t]*)?上一周期(?:最终)?回执[ \t]*SHA256[ \t]*[：:][ \t]*%s[ \t]*\r?$"
         % re.escape(previous_sha)),
        ("新周期 review_id",
         r"(?m)^[ \t]*(?:[-*][ \t]*)?新周期[ \t]*review_id[ \t]*[：:][ \t]*%s[ \t]*\r?$"
         % re.escape(binding["review_id"])),
    )
    missing = [label for label, pattern in checks if not re.search(pattern, text)]
    if missing:
        raise ValueError("新周期决定记录缺少或未引用: %s" % "、".join(missing))
    previous_files = {os.path.normcase(f["path"]) for f in previous["binding"].get("files", [])
                      if not str(f["path"]).endswith(".review.json")}
    current_files = {os.path.normcase(f["path"]) for f in binding["files"]
                     if not str(f["path"]).endswith(".review.json")}
    if not (previous_files & current_files):
        raise ValueError("新周期与上一周期没有共同的受控原件，不能作为同一对象的延续")
    return {
        "source_receipt": os.path.abspath(args.previous),
        "source_receipt_sha256": previous_sha,
        "decision_record": os.path.abspath(args.new_cycle),
        "decision_record_sha256": sha256_hex(record_raw),
        "previous_cycle": previous_cycle,
        "cycle": previous_cycle + 1,
    }


def read_saved_report(path, checklist_count):
    """离线回放原报告/回执；不改旧包、绑定或裁决，不发布通过回执。"""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict) or data.get("schema") != "research-session/review/v1":
        return text
    saved = load_receipt(path, checklist_count)
    if isinstance(saved.get("raw_report"), str):
        return saved["raw_report"]
    if saved.get("report") is not None:
        return json.dumps(saved["report"], ensure_ascii=False)
    with open(saved["pack"]["path"], "rb") as fh:
        appended = fh.read()[saved["pack"]["before_bytes"]:].decode("utf-8")
    start = appended.find("\n{")
    if start < 0:
        raise ValueError("旧包未找到结构化原始意见，不得从叙述补造裁决")
    return appended[start + 1:].strip()


def normalized_format_report(report):
    """Only unwrap an existing locator list, retaining every original locator."""
    result = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    if not isinstance(result, dict) or result.get("decision") not in ("待整改", "不通过", "未完成"):
        raise ValueError("format correction cannot create or recover a pass")
    for finding in result.get("findings", []):
        claim = finding.get("quantitative_claim") if isinstance(finding, dict) else None
        if isinstance(claim, dict) and isinstance(claim.get("json_pointer"), list):
            pointers = claim["json_pointer"]
            if (not pointers or not all(isinstance(p, str) and (not p or p.startswith("/")) for p in pointers)
                    or "json_pointer_context" in claim):
                raise ValueError("locator list has no lossless representation")
            claim["json_pointer_context"] = pointers
            claim["json_pointer"] = pointers[0]
    return result


def validate_format_correction(original, candidate, checklist_count, first_review):
    """A reviewer may correct coverage links/statuses, never scientific content."""
    expected = normalized_format_report(original)
    if not isinstance(candidate, dict):
        raise ValueError("corrected report must be an object")
    projection = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    for field in ("checklist", "claim_checks"):
        if field not in expected:
            if field in projection:
                raise ValueError("format correction cannot introduce new claims")
            continue
        old_rows, new_rows = expected[field], projection.get(field)
        if not isinstance(old_rows, list) or not isinstance(new_rows, list) or len(old_rows) != len(new_rows):
            raise ValueError("format correction cannot add or drop coverage rows")
        for old, new in zip(old_rows, new_rows):
            if not isinstance(old, dict) or not isinstance(new, dict):
                raise ValueError("invalid coverage row")
            for key in ("status", "finding_ids"):
                if key in old and key in new:
                    new[key] = old[key]
    if json.dumps(projection, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False):
        raise ValueError("format correction changed immutable decision, findings or evidence")
    return parse_review(json.dumps(candidate, ensure_ascii=False, allow_nan=False), checklist_count,
                        first_review=first_review)


def recover_corrected_baseline(path, receipt, checklist_count, correction_path):
    import review_format
    from types import SimpleNamespace
    return review_format.recover(path, receipt, checklist_count, correction_path, SimpleNamespace(**globals()))


def recover_format_baseline(path: str, receipt: dict[str, Any], checklist_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """仅在内存恢复旧版首审的已知格式缺陷；不改历史回执、不产生通过。"""
    patch_error = "裁决合同未通过: repair.patch须为非空字符串，test_case须为非空字符串或对象"
    if receipt.get("error") == patch_error:
        if (receipt != load_receipt(path, checklist_count)
                or receipt.get("report") is not None or type(receipt.get("round")) is not int
                or receipt["round"] != 0 or receipt.get("review_counted") is not False
                or receipt.get("previous") is not None
                or receipt.get("gate_passed") is True or receipt.get("review_completed") is True
                or receipt.get("binding", {}).get("rules_sha256") !=
                "bc3bc3a47b5de01aa2e459b85076cffbf15cdb3de8e6023cc9d14aa80852e901"):
            raise ValueError("patch-object recovery requires the exact known first-round failure")
        raw = receipt.get("raw_report")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("missing original patch-object report")
        appended = open_bytes(receipt["pack"]["path"])[receipt["pack"]["before_bytes"]:]
        if raw.rstrip().encode("utf-8") not in appended:
            raise ValueError("original patch-object report differs from bound package suffix")
        normalized = json.loads(raw)
        if not isinstance(normalized, dict) or normalized.get("decision") == "通过":
            raise ValueError("patch-object recovery cannot create or recover a pass")
        changed = 0
        for finding in normalized.get("findings", []):
            repair = finding.get("repair") if isinstance(finding, dict) else None
            if isinstance(repair, dict) and isinstance(repair.get("patch"), dict) and repair["patch"]:
                repair["patch"] = json.dumps(repair["patch"], ensure_ascii=False, sort_keys=True, allow_nan=False)
                changed += 1
        if not changed:
            raise ValueError("no recoverable nonempty patch object")
        report = parse_review(json.dumps(normalized, ensure_ascii=False), checklist_count)
        recovered = dict(receipt, report=report, round=1, review_counted=True)
        info = {"source_receipt": os.path.abspath(path), "source_sha256": sha256_hex(open_bytes(path)),
                "original_round": 0, "effective_round": 1, "decision": report["decision"],
                "recovery_kind": "patch_object_wrapper", "lossless_wrappers": changed,
                "original_raw_report_preserved": True}
        return recovered, info
    command_error = "裁决合同未通过: reproduction requires an argv command or JSON pointer (not executed by lint)"
    if receipt.get("error") == command_error:
        if (receipt.get("report") is not None or receipt.get("round") != 0
                or receipt.get("review_counted") is not False or receipt.get("previous") is not None
                or receipt.get("binding", {}).get("rules_sha256") !=
                "449a487edeb6fb6f68d2ad046f9e2c0774b1659b7b9fe6bf63d0f7b9b577ed28"):
            raise ValueError("command-wrapper recovery requires the exact known first-round failure")
        raw = receipt.get("raw_report")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("missing original command-wrapper report")
        appended = open_bytes(receipt["pack"]["path"])[receipt["pack"]["before_bytes"]:]
        if raw.rstrip().encode("utf-8") not in appended:
            raise ValueError("original command-wrapper report differs from bound package suffix")
        original = json.loads(raw)
        normalized = json.loads(raw)
        if not isinstance(normalized, dict):
            raise ValueError("command-wrapper report must be an object")
        changed = 0
        for finding in normalized.get("findings", []):
            claim = finding.get("quantitative_claim", {})
            command = claim.get("command")
            if isinstance(command, dict):
                argv = command.get("argv")
                if (not isinstance(argv, list) or not argv
                        or not all(isinstance(arg, str) and arg.strip() for arg in argv)
                        or "command_context" in claim):
                    raise ValueError("command wrapper has no lossless nonempty argv")
                claim["command_context"] = command
                claim["command"] = argv
                changed += 1
        if not changed:
            raise ValueError("no recoverable command wrapper")
        report = parse_review(json.dumps(normalized, ensure_ascii=False), checklist_count)
        if report["decision"] == "通过" or report["decision"] != original.get("decision"):
            raise ValueError("format recovery cannot create or change a pass decision")
        recovered = dict(receipt, report=report, round=1, review_counted=True)
        info = {"source_receipt": os.path.abspath(path), "source_sha256": sha256_hex(open_bytes(path)),
                "original_round": 0, "effective_round": 1, "decision": report["decision"],
                "recovery_kind": "command_argv_wrapper", "lossless_wrappers": changed,
                "original_raw_report_preserved": True}
        return recovered, info
    if (receipt.get("report") is not None or receipt.get("round") != 0
            or receipt.get("review_counted") is not False or receipt.get("previous") is not None
            or receipt.get("error") != "裁决合同未通过: repair补丁缺少patch/test_case"
            or receipt.get("binding", {}).get("rules_sha256") != LEGACY_FORMAT_RULES_SHA256):
        raise ValueError("仅支持旧版首审repair.test_case格式失败的原始回执")
    raw = receipt.get("raw_report")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("缺少原始意见")
    with open(receipt["pack"]["path"], "rb") as fh:
        appended = fh.read()[receipt["pack"]["before_bytes"]:]
    if raw.rstrip().encode("utf-8") not in appended:
        raise ValueError("原始意见与绑定的包尾不一致")
    unnormalized = json.loads(raw)
    if not isinstance(unnormalized, dict) or not any(
        isinstance(item, dict) and isinstance(item.get("repair"), dict)
        and isinstance(item["repair"].get("test_case"), dict) and item["repair"]["test_case"]
        for item in unnormalized.get("findings", [])
    ):
        raise ValueError("原意见没有可无损恢复的test_case对象")
    report = parse_review(raw, checklist_count)
    if report["decision"] == "通过":
        raise ValueError("格式恢复不能恢复或继承通过裁决")
    recovered = dict(receipt, report=report, round=1, review_counted=True)
    info = {"source_receipt": os.path.abspath(path), "source_sha256": sha256_hex(open_bytes(path)),
            "original_round": 0, "effective_round": 1, "decision": report["decision"]}
    return recovered, info


def open_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def related_receipts(pack_path, binding, checklist_count, *, fixture=False):
    """同目录同对象的证据链；改包名不能重置主审次数。"""
    found = []
    with os.scandir(os.path.dirname(os.path.abspath(pack_path))) as entries:
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".review.json"):
                continue
            try:
                with open(entry.path, encoding="utf-8") as fh:
                    candidate = json.load(fh)
            except (ValueError, OSError):
                # 当前包/显式基线另有严格校验，不让别的对象的残缺回执阻塞整目录。
                continue
            scope = candidate.get("binding", {}) if isinstance(candidate, dict) else {}
            if (isinstance(candidate, dict) and ("unattended" in candidate) == fixture and
                    scope.get("review_id") == binding["review_id"] and scope.get("point") == binding["point"]):
                found.append((entry.path, load_receipt(entry.path, checklist_count)))
    return found


def first_review_routes(report):
    routes = {"local": [], "recheck": []}
    for item in report.get("findings", []):
        if item["kind"] == "blocking":
            routes[item["closure"]["mode"]].append(item["id"])
    return routes


def verify_local_edits(receipt):
    """Reconstruct exact reviewed bytes from the current files, without executing review text."""
    report, binding = receipt["report"], receipt["binding"]
    if (receipt["round"] != 1 or "unattended" in receipt or binding["point"] != 3
            or not binding.get("first_review") or report["decision"] != "待整改"):
        raise ValueError("仅新合同汇报前首审的可验收整改适用")
    routes = first_review_routes(report)
    if not routes["local"] or routes["recheck"]:
        raise ValueError("不是全部阻断均可本地验收")
    edits = {}
    for item in report["findings"]:
        if item["kind"] == "blocking":
            for edit in item["closure"]["edits"]:
                edits.setdefault(edit["path"], []).append(edit)
    verified = []
    for entry in binding["files"]:
        path = entry["path"]
        if os.path.realpath(path) != path:
            raise ValueError("受控路径已重定向: " + path)
        current = Path(path).read_bytes()
        original = current
        for edit in reversed(edits.get(path, [])):
            before, after = (edit[key].encode("utf-8") for key in ("before", "after"))
            if original.count(after) != 1:
                raise ValueError("替换结果缺失或不唯一: " + path)
            original = original.replace(after, before, 1)
            if original.count(before) != 1:
                raise ValueError("首审原文不唯一: " + path)
        if sha256_hex(original) != entry["sha256"]:
            raise ValueError("存在未验收整改或绑定文件漂移: " + path)
        if path in edits:
            spans = []
            for edit in edits[path]:
                before, after = (edit[key].encode("utf-8") for key in ("before", "after"))
                start = original.find(before)
                if start < 0 or original.find(before, start + 1) >= 0:
                    raise ValueError("每项更正必须独立定位到首审原文: " + path)
                spans.append((start, start + len(before), after))
            rebuilt, end = [], 0
            for start, stop, after in sorted(spans):
                if start < end:
                    raise ValueError("首审更正相互覆盖: " + path)
                rebuilt.extend((original[end:start], after))
                end = stop
            if b"".join([*rebuilt, original[end:]]) != current:
                raise ValueError("当前正文不是首审批准的独立替换结果: " + path)
            verified.append({"path": path, "sha256": sha256_hex(current)})
    return verified


def review_result(receipt, path, reused=False):
    decision = (receipt.get("report") or {}).get("decision", "未完成")
    passed = decision == "通过"
    completed = decision != "未完成"
    fixture = "unattended" in receipt
    routes = None
    if passed:
        disposition, action = (("fixture_passed", "use_fixture_result_in_offline_run_only") if fixture else
                               ("approved", "use_current_binding_with_existing_authorization"))
    elif receipt.get("unattended") and receipt.get("report") is not None:
        if receipt["round"] >= 3:
            disposition, action = "unattended_limit", "apply_bounded_repairs_validate_then_continue_independent_work_only"
        else:
            disposition, action = "unattended_revise", "verify_bounded_repairs_before_next_preapproved_round"
    elif receipt.get("binding", {}).get("task_approval") and receipt.get("report") is not None:
        disposition, action = "task_revise", "repair_verify_progress_then_continue_within_task_budget"
    elif receipt["round"] >= 2 and receipt.get("report") is not None:
        disposition, action = "needs_user_decision", "stop_dependent_steps_record_disagreement_no_third_round"
    elif not completed:
        disposition, action = "incomplete_stop", "diagnose_missing_evidence_or_contract_no_automatic_resubmit"
    elif decision == "不通过":
        disposition, action = "rejected_stop", "record_rejection_and_decide_scope_no_automatic_resubmit"
    elif receipt.get("binding", {}).get("first_review") and receipt["round"] == 1:
        routes = first_review_routes(receipt["report"])
        if routes["recheck"]:
            disposition, action = "recheck_required", "verify_repairs_then_review_remaining_judgments_and_account_for_every_old_blocker"
        else:
            disposition, action = "local_verification_required", "apply_exact_edits_then_verify_locally_no_resubmit"
    else:
        disposition, action = "revise_once", "apply_bounded_reviewer_patch_verify_then_at_most_one_incremental_review"
    repairs = [f["id"] for f in (receipt.get("report") or {}).get("findings", [])
               if isinstance(f.get("repair"), dict) and f["repair"].get("status") == "patch_proposed"]
    return {"ok": passed, "review_completed": completed, "gate_passed": passed and not fixture,
            **({"offline_fixture": True, "fixture_gate_passed": passed} if fixture else {}),
            "decision": decision, "stage": "done", "round": receipt["round"],
            "cycle": cycle_of(receipt),
            "disposition": disposition, "next_action": action,
            **({"local_verification_ids": routes["local"], "recheck_ids": routes["recheck"]} if routes else {}),
            "proposed_repair_ids": repairs,
            "review_counted": receipt.get("review_counted", receipt.get("report") is not None),
            "receipt": os.path.abspath(path), "reused": reused, "binding": receipt["binding"],
            **({"receipt_sha256": sha256_hex(open_bytes(path))} if routes and os.path.isfile(path) else {}),
            "error": receipt.get("error")}, (0 if passed else 3 if completed else 2)


def save_failed_attempt(pack, binding, details, cap):
    """Persist diagnostics without a substantive round or an immutable review receipt."""
    value = dict(details, schema="research-session/review-attempt/v1", binding=binding,
                 max_output_tokens=cap, review_counted=False, review_completed=False, gate_passed=False,
                 created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    fd, path = tempfile.mkstemp(prefix=os.path.basename(pack) + ".attempt-", suffix=".json",
                                dir=os.path.dirname(os.path.abspath(pack)))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=True, indent=2)
        fh.write("\n")
    return path


def upstream_diagnostic(raw):
    """只提取错误字段，不回显请求正文、凭据或整个响应。"""
    payloads = []
    try:
        payloads.append(json.loads(raw))
    except (ValueError, TypeError):
        for line in raw.splitlines():
            if line.strip().startswith("data:"):
                try:
                    payloads.append(json.loads(line.strip()[5:].strip()))
                except (ValueError, TypeError):
                    pass
    result = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        response = payload.get("response") or payload
        if not isinstance(response, dict):
            continue
        if response.get("id"):
            result["response_id"] = response["id"]
        if response.get("status"):
            result["response_status"] = response["status"]
        details = response.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason"):
            result["reason"] = details["reason"]
        error = response.get("error") or payload.get("error")
        if not error and payload.get("type") == "error":
            error = payload
        if isinstance(error, dict):
            for key in ("code", "type", "message"):
                if error.get(key):
                    value = str(error[key])[:1000]
                    value = re.sub(r"(?i)Bearer\s+\S+|\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
                    result[key] = value
        elif isinstance(error, str):
            result["message"] = re.sub(r"(?i)Bearer\s+\S+|\bsk-[A-Za-z0-9_-]+", "[REDACTED]", error[:1000])
    if "data:" in raw and not result.get("reason") and not result.get("message"):
        result["reason"] = "stream_failed_or_ended_without_completion"
    return result


def review_usage(raw):
    for line in [raw, *(line.strip()[5:].strip() for line in reversed(raw.splitlines())
                        if line.strip().startswith("data:"))]:
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        response = payload.get("response") or payload
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            continue
        result = {key: usage[key] for key in ("input_tokens", "output_tokens", "billed_cny_fen")
                  if type(usage.get(key)) is int and usage[key] >= 0}
        cached = usage.get("input_tokens_details")
        if isinstance(cached, dict) and type(cached.get("cached_tokens")) is int and cached["cached_tokens"] >= 0:
            result["cached_input_tokens"] = cached["cached_tokens"]
        return result
    return {}


def _main(offline_transport=None):
    global REQUEST_STARTED, RECEIPT_PUBLISHED
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--pack", help="复核包绝对路径")
    ap.add_argument("--point", type=int, choices=(1, 2, 3), help="复核点 1/2/3")
    ap.add_argument("--model", help="显式审计模型；实际发送无默认")
    ap.add_argument("--transport", choices=("responses",), default="responses", help="唯一在线传输契约；不自动切换")
    ap.add_argument("--provider-config", help="Responses provider JSON；凭据只允许环境变量")
    ap.add_argument("--responses-url", help="显式 Responses 端点，例如 https://provider.example/v1/responses")
    ap.add_argument("--api-key-env", help="凭据环境变量名；默认 RESEARCH_SESSION_API_KEY")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="显式本地测试端口；无默认")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="单次发送的整体截止时间（秒，默认1200）；超时不自动重发")
    ap.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    ap.add_argument("--retry-truncated-tokens", type=int,
                    help="显式授权max_output_tokens截断后仅重试一次的更高cap；缺省不重试、不增预算")
    ap.add_argument("--unattended-state", help="已认领的 v2 运行台账")
    ap.add_argument("--unattended-action", help="台账内固定复核动作")
    ap.add_argument("--reference", default=REFERENCE_MD, help="必查项真源 md")
    ap.add_argument("--inputs", help="受控文件 JSON 清单：review_id 与 files[{path,sha256}]")
    ap.add_argument("--previous", help="上一轮 .review.json；整改复核必填")
    ap.add_argument("--new-cycle", help="用户决定的新周期决定记录；须同时给 --previous 与派生 review_id")
    ap.add_argument("--task-approval", help="自主/无人值守任务批准记录；同一ID累计整改，不开新周期")
    ap.add_argument("--recover-previous-format", action="store_true",
                    help="仅内存恢复已登记的旧首审对象包装格式失败；不修改原回执、不授予发送权限")
    ap.add_argument("--corrected-previous", help="独立格式纠正侧件；显式绑定原始失败回执，不改旧判词或轮次")
    ap.add_argument("--dry-run", action="store_true", help="组装 prompt 但不发送")
    ap.add_argument("--lint-pack", action="store_true", help="只离线核对包内源码字节与验证记录；不送审、不放行")
    ap.add_argument("--validate-report", help="仅离线校验原始裁决 JSON 或历史 .review.json；不送审、不放行")
    ap.add_argument("--verify-local-edits", metavar="RECEIPT", help="候选：离线核对首审预定修改；不触碰台账或正式门")
    ap.add_argument("--expected-receipt-sha256", help="首次送审结果中已独立留存的回执摘要")
    ap.add_argument("--show-contract", action="store_true", help="显示当前合同/必需字段/完整模板；不送审")
    ap.add_argument("--no-stream", action="store_true", help="关闭流式（默认流式）")
    ap.add_argument("--probe", action="store_true", help="只探活 GET /v1/models")
    ap.add_argument("--ledger-dir", help="复核台账目录；默认 ~/.agents/state/research-review")
    ap.add_argument("--ledger-backfill", metavar="ROOT", help="只读扫描项目并回填复核台账；不送审")
    ap.add_argument("--resolve-ledger", metavar="ENTRY_ID", help="解除已核查的发送意图；须配 --ledger-note")
    ap.add_argument("--ledger-note", help="解除发送意图的依据")
    ap.add_argument("--ledger-receipt", help="人工核对历史已发送意图对应的回执")
    ap.add_argument("--recovery-template", metavar="LOCK_ENTRY_ID", help="输出孤锁恢复核查草稿；不改状态")
    ap.add_argument("--recover-send-lock", metavar="LOCK_ENTRY_ID", help="按独立核查证据恢复失败发送锁")
    ap.add_argument("--recovery-evidence", help="逐发送独立核查记录")
    ap.add_argument("--recovery-sha256", help="已核对的恢复核查记录 SHA256")
    ap.add_argument("--confirm-recovery-review", action="store_true", help="已核实独立核查原件；不认证身份或授权重发")
    ap.add_argument("--revoke-send-recovery", metavar="RECOVERY_ID", help="追加撤销恢复，不重建物理锁")
    ap.add_argument("--fixture-template", metavar="ENTRY_ID", help="输出逐条审核草稿；不写台账、不送审")
    ap.add_argument("--resolve-fixture", metavar="ENTRY_ID", help="追加已独立审核的夹具误登记纠错")
    ap.add_argument("--fixture-attestation", help="独立逐条审核记录的绝对路径")
    ap.add_argument("--fixture-sha256", help="预检时核对的审核记录 SHA256")
    ap.add_argument("--confirm-independent-review", action="store_true", help="确认已核实独立审核原件和职责分离；不认证身份")
    ap.add_argument("--revoke-fixture", metavar="ENTRY_ID", help="追加撤销夹具纠错；须配 --ledger-note")
    args = ap.parse_args()
    if args.task_approval and (args.new_cycle or args.unattended_state or args.unattended_action
            or args.retry_truncated_tokens is not None or args.recovery_template
            or args.recover_send_lock or args.revoke_send_recovery or args.resolve_fixture
            or args.revoke_fixture or args.fixture_template or args.ledger_backfill or args.resolve_ledger):
        emit({"ok": False, "stage": "args", "error": "task continuation cannot mix maintenance, fixture, new-cycle or retry operations"}, 2)
    if args.verify_local_edits is not None or args.expected_receipt_sha256 is not None:
        try:
            if (len(sys.argv) != 5 or not args.verify_local_edits
                    or not re.fullmatch(r"[a-fA-F0-9]{64}", args.expected_receipt_sha256 or "")):
                raise ValueError("只接受 --verify-local-edits 及首次送审时留存的 --expected-receipt-sha256")
            path = os.path.abspath(args.verify_local_edits)
            raw = open_bytes(path)
            if sha256_hex(raw) != args.expected_receipt_sha256.lower():
                raise ValueError("回执与首次送审时留存的摘要不符")
            _, items = load_checklist(3, REFERENCE_MD)
            receipt = load_receipt(path, len(items), raw=raw)
            binding = rules_binding(REFERENCE_MD)
            previous_rules = receipt["binding"]["rules_sha256"]
            transition = None
            if previous_rules != binding["rules_sha256"]:
                transition = compatible_rules_transition(previous_rules, binding)
            verified = verify_local_edits(receipt)
            if raw != open_bytes(path):
                raise ValueError("核对过程中复核回执变化")
            emit({"ok": False, "local_only": True, "local_release_ready": True,
                  "disposition": "locally_verified", "decision": receipt["report"]["decision"],
                  "review_completed": True, "receipt": path, "receipt_sha256": sha256_hex(raw),
                  "verified_files": verified, "rules_transition": transition,
                  "stage": "verify_local_edits"}, 0)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            emit({"ok": False, "local_only": True, "local_release_ready": False,
                  "stage": "verify_local_edits", "error": str(exc)}, 2)
    unattended = None
    if args.unattended_state or args.unattended_action:
        if offline_transport is None:
            emit({"ok": False, "stage": "unattended", "error": "此版本仅支持离线运行夹具；不开放真实无人送审"}, 2)
        if (not args.unattended_state or not args.unattended_action or args.retry_truncated_tokens is not None or
            args.recover_previous_format or args.new_cycle or args.model or args.ledger_dir or args.probe or
            args.ledger_backfill or args.resolve_ledger or args.recovery_template or args.recover_send_lock
            or args.revoke_send_recovery or args.reference != REFERENCE_MD or
            args.port != DEFAULT_PORT or args.no_stream or args.transport != "responses" or
            args.provider_config or args.responses_url or args.api_key_env or
            args.resolve_fixture or args.revoke_fixture or args.fixture_template):
            emit({"ok": False, "stage": "unattended", "error": "无人值守只接受固定路由；不得重试、换模或新建周期"}, 2)

    recovery_modes = [args.recovery_template, args.recover_send_lock, args.revoke_send_recovery]
    if any(recovery_modes):
        conflicts = (args.pack or args.inputs or args.point or args.previous or args.new_cycle
                     or args.probe or args.lint_pack or args.validate_report or args.show_contract
                     or args.resolve_ledger or args.ledger_backfill or args.fixture_template
                     or args.resolve_fixture or args.revoke_fixture or args.unattended_state
                     or args.unattended_action or args.retry_truncated_tokens or args.model
                     or args.recover_previous_format or args.corrected_previous or args.fixture_attestation
                     or args.fixture_sha256 or args.confirm_independent_review
                     or args.transport != "responses" or args.port != DEFAULT_PORT or args.no_stream)
        if (sum(bool(mode) for mode in recovery_modes) != 1 or conflicts
                or (not args.recover_send_lock and (args.dry_run or args.recovery_evidence
                                                   or args.recovery_sha256 or args.confirm_recovery_review))):
            emit({"ok": False, "stage": "args", "error": "恢复维护不能混用送审或其他操作"}, 2)
        try:
            if args.recovery_template:
                template = review_ledger.send_lock_recovery_template(args.ledger_dir, args.recovery_template)
                emit({"ok": True, "stage": "recovery_template", "local_only": True,
                      "gate_passed": False, "template": template}, 0)
            if args.revoke_send_recovery:
                result = review_ledger.revoke_send_recovery(
                    args.ledger_dir, args.revoke_send_recovery, args.ledger_note)
                emit({"ok": True, "stage": "recovery_revocation", **result}, 0)
            if not args.recovery_evidence or not args.recovery_sha256:
                raise ValueError("须提供 --recovery-evidence 和 --recovery-sha256")
            if not args.dry_run and not args.confirm_recovery_review:
                raise ValueError("应用前须核实独立核查原件并给出 --confirm-recovery-review")
            result = review_ledger.recover_send_lock(
                args.ledger_dir, args.recover_send_lock, args.recovery_evidence,
                args.recovery_sha256, dry_run=args.dry_run)
            emit({"ok": True, "stage": "send_lock_recovery", **result}, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "send_lock_recovery", "local_only": True,
                  "gate_passed": False, "error": str(exc)}, 2)
    if args.recovery_evidence or args.recovery_sha256 or args.confirm_recovery_review:
        emit({"ok": False, "stage": "args", "error": "恢复证据仅用于 --recover-send-lock"}, 2)

    fixture_modes = [args.fixture_template, args.resolve_fixture, args.revoke_fixture]
    if any(fixture_modes):
        conflicts = (args.pack or args.previous or args.inputs or args.point or args.probe or args.lint_pack
                     or args.validate_report or args.show_contract or args.ledger_backfill or args.resolve_ledger
                     or args.new_cycle or args.recover_previous_format or args.corrected_previous)
        if (sum(bool(x) for x in fixture_modes) != 1 or conflicts
                or (not args.resolve_fixture and (args.dry_run or args.fixture_attestation
                                                  or args.fixture_sha256 or args.confirm_independent_review))):
            emit({"ok": False, "stage": "args", "error": "夹具维护命令不能混用送审或其他操作"}, 2)
        try:
            if args.fixture_template:
                template = review_ledger.fixture_attestation_template(args.ledger_dir, args.fixture_template)
                emit({"ok": True, "stage": "fixture_template", "local_only": True,
                      "template": template, "gate_passed": False}, 0)
            if args.revoke_fixture:
                result = review_ledger.revoke_false_receipt_resolution(
                    args.ledger_dir, args.revoke_fixture, args.ledger_note)
                emit({"ok": True, "stage": "fixture_revocation", "local_only": True,
                      "gate_passed": False, "resolution": result}, 0)
            if not args.fixture_attestation or not args.fixture_sha256:
                raise ValueError("须指定 --fixture-attestation 和 --fixture-sha256")
            if not args.dry_run and not args.confirm_independent_review:
                raise ValueError("应用前须核实独立审核原件，并明确给出 --confirm-independent-review")
            result = review_ledger.resolve_reviewed_fixture(
                args.ledger_dir, args.resolve_fixture, args.fixture_attestation, args.fixture_sha256,
                dry_run=args.dry_run)
            emit({"ok": True, "stage": "fixture_resolution", "local_only": True,
                  "applied": not args.dry_run, "gate_passed": False, "resolution": result}, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "fixture_resolution", "local_only": True, "error": str(exc)}, 2)
    if args.fixture_attestation or args.fixture_sha256 or args.confirm_independent_review:
        emit({"ok": False, "stage": "args", "error": "审核记录参数仅用于 --resolve-fixture"}, 2)

    if args.corrected_previous and (not args.previous or args.recover_previous_format or args.new_cycle
            or args.validate_report or args.show_contract or args.lint_pack or args.probe
            or args.unattended_state or args.ledger_backfill or args.resolve_ledger
            or args.recover_send_lock or args.resolve_fixture):
        emit({"ok": False, "stage": "args", "error": "格式纠正侧件仅用于原回执的有界增量复核"}, 2)
    if args.recover_previous_format and (not args.previous or args.validate_report or args.show_contract or args.lint_pack):
        emit({"ok": False, "stage": "args", "error": "格式恢复仅用于带--previous的增量包/预检"}, 2)
    if args.new_cycle and (not args.previous or not args.inputs
                           or args.validate_report or args.show_contract or args.lint_pack
                           or args.probe or args.recover_previous_format):
        emit({"ok": False, "stage": "args",
              "error": "--new-cycle 仅用于带 --previous、--inputs 的首次送审/预检；不得与离线校验或格式恢复同用"}, 2)
    if args.timeout <= 0:
        emit({"ok": False, "stage": "args", "error": "timeout须为正"}, 2)
    if args.max_output_tokens <= 0 or (args.retry_truncated_tokens is not None
                                      and args.retry_truncated_tokens <= args.max_output_tokens):
        emit({"ok": False, "stage": "args", "error": "输出cap须为正，截断重试cap须大于首次cap"}, 2)

    if args.ledger_backfill:
        try:
            summary = review_ledger.backfill(args.ledger_backfill, args.ledger_dir)
            emit({"ok": True, "stage": "ledger_backfill", "local_only": True, **summary}, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "ledger_backfill", "error": str(exc)}, 2)

    if args.resolve_ledger:
        if not args.ledger_note or not args.ledger_note.strip():
            emit({"ok": False, "stage": "args", "error": "--resolve-ledger 须同时给 --ledger-note 说明核查依据"}, 2)
        try:
            result = review_ledger.resolve_intent(
                args.ledger_dir, args.resolve_ledger, args.ledger_note.strip(), receipt_path=args.ledger_receipt)
            emit({"ok": True, "stage": "ledger_resolution", "local_only": True, **result}, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "ledger_resolution", "error": str(exc)}, 2)

    if args.lint_pack:
        try:
            if not args.pack:
                raise ValueError("--lint-pack须提供--pack和--inputs")
            with open(args.pack, "rb") as fh:
                lint = review_evidence.lint_pack(fh.read(), load_inputs(args.inputs))
            emit({"ok": False, "stage": "pack_lint", "local_only": True, "pack_lint": lint}, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "pack_lint", "local_only": True, "error": str(exc)}, 2)

    if args.validate_report or args.show_contract:
        try:
            if not args.point:
                raise ValueError("离线校验/合同输出须指定 --point")
            _, items = load_checklist(args.point, args.reference)
            previous = None
            if args.previous:
                previous = {"report": parse_review(read_saved_report(args.previous, len(items)), len(items))}
            first_review = load_inputs(args.inputs).get("first_review") if args.inputs and not previous else None
            contract = review_contract(len(items), previous, first_review=first_review)
            value = {"ok": False, "stage": "local_validation", "schema_validated": True,
                     "local_only": True, "contract": contract}
            if args.validate_report:
                value["report"] = parse_review(read_saved_report(args.validate_report, len(items)), len(items), previous,
                                               first_review=first_review)
            emit(value, 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "local_validation", "schema_validated": False,
                  "local_only": True, "error": str(exc)}, 2)

    if args.probe:
        try:
            route = responses_route(args, args.model)
            models_url = route["url"][:-len("responses")] + "models"
            status, raw = http_get_models(models_url, min(args.timeout, 30), provider_api_key(route))
        except ProviderNotConfigured:
            emit({"ok": False, "stage": "probe", "error": "provider_not_configured",
                  "request_started": False}, 2)
        except Exception as exc:
            emit(
                {"ok": False, "stage": "probe", "error": "%s: %s" % (type(exc).__name__, exc)},
                2,
            )
        ids = []
        try:
            payload = json.loads(raw)
            for row in payload.get("data") or []:
                if isinstance(row, dict) and row.get("id"):
                    ids.append(row["id"])
        except Exception:
            pass
        emit({"ok": status == 200, "stage": "probe", "status": status, "models": ids},
             0 if status == 200 else 2)

    if not args.pack or not args.point:
        emit({"ok": False, "stage": "args", "error": "--pack 与 --point 均为必填"}, 2)

    route = None
    if args.unattended_state or args.unattended_action:
        # 离线无人值守只走显式 offline_transport；不要求在线 provider，也不建路由。
        model = POINT_MODEL[args.point]
    elif args.dry_run:
        try:
            route = optional_responses_route(args, args.model)
        except Exception as exc:
            emit({"ok": False, "stage": "provider", "error": str(exc), "request_started": False}, 2)
        model = route["wire_model"] if route else (
            args.model or os.environ.get("RESEARCH_SESSION_MODEL") or POINT_MODEL[args.point])
    else:
        try:
            route = responses_route(args, args.model)
        except ProviderNotConfigured:
            emit({"ok": False, "stage": "provider", "error": "provider_not_configured",
                  "request_started": False}, 2)
        model = route["wire_model"]

    # ---- 1. 读包 + 正文 sha256 ----
    try:
        with open(args.pack, "rb") as fh:
            original = fh.read()
        pack_text = original.decode("utf-8")
    except Exception as exc:
        emit({"ok": False, "stage": "read_pack", "error": "%s: %s" % (type(exc).__name__, exc)}, 2)
    body_sha = sha256_hex(original)

    # ---- 2. 必查项（单一真源）----
    try:
        title, items = load_checklist(args.point, args.reference)
    except Exception as exc:
        emit({"ok": False, "stage": "checklist", "error": str(exc)}, 2)

    receipt_path = os.path.abspath(args.pack) + ".review.json"
    previous = None
    new_cycle = None
    baseline = None
    recovered_previous = None
    rules_transition = None
    scope = None
    ledger_summary = None
    try:
        if args.inputs:
            scope = review_ledger.validate_scope(args.pack, load_inputs(args.inputs))
            if not args.unattended_state:
                if review_ledger.is_offline_fixture_path(args.pack):
                    raise ValueError("v4 合成项目不得使用普通预检或发送入口")
                review_ledger.verify_ledger_scope(args.ledger_dir, scope["project_root"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "review_scope", "error": str(exc)}, 2)
    try:
        if not original.strip():
            raise ValueError("复核包为空")
        if os.path.exists(receipt_path):
            cached = load_receipt(receipt_path, len(items))
            if cached.get("unattended"):
                raise ValueError("离线夹具回执不复用为普通或本轮复核结果")
            current = make_binding(args, model, cached["binding"]["pack_sha256"])
            if GUARDED_ID is not None and current["review_id"] != GUARDED_ID:
                raise ValueError("获取发送保护后 review_id 发生变化")
            if current != cached["binding"]:
                raise ValueError("审查对象/依赖/规则已变化；须出增量包，不得复用旧裁决")
            review_evidence.lint_pack(original[:cached["pack"]["before_bytes"]], current)
            result, code = review_result(cached, receipt_path, reused=True)
            emit(result, code)
        binding = make_binding(args, model, body_sha)
        if args.unattended_state:
            from autonomy_run import review_ticket, RunError
            try:
                unattended = review_ticket(args.unattended_state, args.unattended_action, args, binding)
                args.ledger_dir = unattended["ledger_dir"]
            except (RunError, ValueError, OSError, KeyError, TypeError) as exc:
                emit({"ok": False, "stage": "unattended", "error": str(exc)}, 2)
        if GUARDED_ID is not None and binding["review_id"] != GUARDED_ID:
            raise ValueError("获取发送保护后 review_id 发生变化")
        if scope is None:
            raise ValueError("实际送审须 --inputs")
        ledger_summary = review_ledger.preflight(
            args.ledger_dir, scope, binding["review_id"], binding["point"])
        blocked = review_ledger.blocking_reason(ledger_summary)
        if blocked is not None:
            emit({"ok": False, "stage": blocked[0], "error": blocked[1], **blocked[2]}, 2)
        related = related_receipts(args.pack, binding, len(items), fixture=bool(unattended))
        if (not args.previous or args.new_cycle) and re.search(
                r"(?m)^## 5\. 复核结论（[^\r\n]*自动填写）", pack_text):
            raise ValueError("已有审查意见但缺少有效回执；不得把旧包全文重新送审")
        if args.previous:
            if os.path.normcase(os.path.realpath(os.path.dirname(args.pack))) != os.path.normcase(
                    os.path.realpath(os.path.dirname(args.previous))):
                raise ValueError("复核包与上一轮回执须在同一目录")
            previous = load_receipt(args.previous, len(items))
            if args.corrected_previous:
                corrected_path = os.path.abspath(args.corrected_previous)
                bound_paths = {os.path.normcase(f["path"]) for f in binding["files"]}
                if not {os.path.normcase(os.path.realpath(p)) for p in
                        (args.previous, corrected_path)}.issubset(bound_paths):
                    raise ValueError("原回执和格式纠正侧件必须列入本轮绑定")
                previous, recovered_previous = recover_corrected_baseline(
                    args.previous, previous, len(items), corrected_path)
                if not {os.path.normcase(os.path.realpath(row["path"])) for row in
                        recovered_previous["artifact_files"]}.issubset(bound_paths):
                    raise ValueError("格式纠正请求、原始返回和共享认领必须列入本轮绑定")
            if args.recover_previous_format:
                if os.path.normcase(os.path.realpath(os.path.dirname(args.pack))) != os.path.normcase(
                        os.path.realpath(os.path.dirname(args.previous))):
                    raise ValueError("格式恢复增量包须与原回执同目录，以核对已有轮次")
                previous, recovered_previous = recover_format_baseline(args.previous, previous, len(items))
            baseline = None if args.new_cycle else previous
            review_contract(len(items), baseline)
            if unattended and (previous.get("unattended", {}).get("run_id") != unattended["run_id"] or
                               previous.get("report") is None or previous["round"] != unattended["round"] - 1 or
                               previous["report"]["decision"] == "通过"):
                raise ValueError("无人值守续轮必须使用同一运行上一有效未通过回执")
            if not unattended and not args.new_cycle and previous.get("unattended"):
                raise ValueError("不得从无人值守回执切换到普通整改以重置轮次")
            keys = ("point", "model", "reasoning_effort_requested") if args.new_cycle else (
                "review_id", "point", "model", "reasoning_effort_requested")
            for key in keys:
                if binding[key] != previous["binding"][key]:
                    label = "新周期" if args.new_cycle else "整改复核"
                    raise ValueError("%s与基线的 %s 不一致" % (label, key))
            rules_changed = binding["rules_sha256"] != previous["binding"]["rules_sha256"]
            if rules_changed and (recovered_previous is None or args.corrected_previous):
                rules_transition = compatible_rules_transition(previous["binding"]["rules_sha256"], binding)
            if recovered_previous is not None:
                recovered_previous.update(rules_changed=rules_changed,
                                          previous_rules_sha256=previous["binding"]["rules_sha256"],
                                          current_rules_sha256=binding["rules_sha256"])
            if args.new_cycle:
                for path, record in related:
                    if record["binding"] == binding:
                        review_evidence.lint_pack(original, binding)
                        result, code = review_result(record, path, reused=True)
                        emit(result, code)
                if related:
                    raise ValueError("该新周期 review_id 已有审查记录；不得用 --new-cycle 重开")
                new_cycle = prepare_new_cycle(args, previous, binding)
            if binding == previous["binding"]:
                review_evidence.lint_pack(original, binding)
                result, code = review_result(previous, args.previous, reused=True)
                emit(result, code)
            consumed_recovery = (previous.get("recovered_previous") or {}) if args.task_approval else {}
            if consumed_recovery:
                if sha256_hex(open_bytes(consumed_recovery["source_receipt"])) != consumed_recovery["source_sha256"]:
                    raise ValueError("已消费的格式恢复原回执发生变化")
            if baseline and any(
                    record.get("report") is None and os.path.normcase(os.path.realpath(path)) !=
                    os.path.normcase(os.path.realpath(args.previous))
                    and sha256_hex(open_bytes(path)) != consumed_recovery.get("source_sha256")
                    for path, record in related):
                raise ValueError("已有未完成的审查响应；先离线诊断，不得更换包重复送审")
            if args.task_approval:
                import review_task
                review_task.validate_progress(binding, previous)
            if not unattended and not args.task_approval and baseline and (baseline.get("report") or {}).get("decision") == "不通过":
                emit({"ok": False, "stage": "review_limit", "decision": "不通过",
                      "disposition": "rejected_stop",
                      "error": "首轮明确否决；不得作为普通整改轮送审，新周期须用户明确决定"}, 3)
            round_limit = 3 if unattended else 2
            if not args.task_approval and baseline and (baseline["round"] >= round_limit or any(
                    record.get("report") is not None and record["round"] >= round_limit for _, record in related)):
                emit({"ok": False, "stage": "review_limit",
                      "decision": (previous.get("report") or {}).get("decision", "未完成"),
                      "disposition": "needs_user_decision", "next_action": "stop_dependent_steps_no_third_round",
                      "error": "已完成一次主审和一次整改复核；保留分歧，交用户裁决，不自动重送",
                      "previous": os.path.abspath(args.previous)}, 3)
        else:
            if derived_cycle_root(binding["review_id"]):
                raise ValueError("派生 review_id %s 须使用 --new-cycle 并提供用户决定记录" % binding["review_id"])
            if related:
                for path, record in related:
                    if record["binding"] == binding:
                        review_evidence.lint_pack(original, binding)
                        result, code = review_result(record, path, reused=True)
                        emit(result, code)
                raise ValueError("该 review_id/复核点已有审查记录；修改后须 --previous，不得改名重开主审")
        if args.task_approval:
            import review_task
            review_task.validate(args.task_approval, binding, scope, args.timeout,
                                 review_ledger.read_entries(args.ledger_dir))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "binding", "error": str(exc)}, 2)

    try:
        pack_lint = review_evidence.lint_pack(original, binding)
        if binding.get("disputes") and not baseline:
            raise ValueError("disputes只用于带有效--previous的整改复核")
        active = set(review_contract(len(items), baseline)["active_blocker_ids"])
        if any(d["finding_id"] not in active for d in binding.get("disputes", [])):
            raise ValueError("disputes须定位上一轮当前blocking ID")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "pack_lint", "error": str(exc), "local_only": True}, 2)
    metadata = {key: binding[key] for key in ("timeline", "closures", "disputes", "task_approval", "remediation_progress") if key in binding}
    first_review = binding.get("first_review") if not baseline else None
    if first_review is not None:
        if unattended:
            emit({"ok": False, "stage": "review_scope", "error": "候选首审不能用于无人值守夹具"}, 2)
        metadata["first_review"] = first_review
    metadata["pack_lint"] = pack_lint
    if new_cycle:
        metadata["new_cycle"] = new_cycle
    provider = "offline-fixture" if unattended else binding.get("route", {}).get("provider", "responses")
    prompt = build_prompt(args.point, title, items, pack_text, baseline, metadata)

    if args.dry_run:
        contract = review_contract(len(items), baseline, binding.get("disputes"), first_review=first_review)
        # 使用真正消费端验证生产模板；不依赖模型凭空猜必需字段/销项集合。
        parse_review(json.dumps(contract["response_template"], ensure_ascii=False), len(items), baseline, binding.get("disputes"),
                     first_review=first_review)
        fd, path = tempfile.mkstemp(prefix="audit-prompt-", suffix=".txt")
        with os.fdopen(fd, "wb") as fh:
            fh.write(prompt.encode("utf-8"))
        emit(
            {
                "ok": False,
                "dry_run": True,
                "stage": "dry_run",
                "reasoning_effort_requested": REVIEW_REASONING_EFFORT,
                "model": model,
                "provider": provider,
                "point": args.point,
                "cycle": new_cycle["cycle"] if new_cycle else (cycle_of(previous) if previous else 1),
                "new_cycle": new_cycle,
                "checklist_count": len(items),
                "pack_sha256": body_sha,
                "prompt_chars": len(prompt),
                "prompt_path": path,
                "binding": binding,
                "schema_validated": True,
                "pack_lint": pack_lint,
                "output_budget": {"first_cap": args.max_output_tokens,
                                  "truncation_retry_cap": args.retry_truncated_tokens,
                                  "http_attempt_limit_per_send": RETRY_ATTEMPTS},
                "contract": contract,
                "recovered_previous": recovered_previous or (
                    previous.get("recovered_previous") if args.task_approval and previous else None),
                "rules_transition": rules_transition,
                "ledger": {"path": review_ledger.ledger_path(args.ledger_dir),
                           "project_id": scope["project_id"], "project_root": scope["project_root"],
                           "history_count": len(ledger_summary["group"]),
                           "canonical_receipts": len(ledger_summary["canonical_receipts"]),
                           "coverage_complete": ledger_summary["coverage_complete"],
                           "coverage_review_complete": ledger_summary["coverage_review_complete"]},
            },
            0,
        )

    # ---- 3. 送审 ----
    stream = not args.no_stream
    log("point=%d model=%s provider=%s stream=%s" % (args.point, model, provider, stream))
    attempt_paths = []
    cycle_value = new_cycle["cycle"] if new_cycle else (cycle_of(previous) if previous else 1)
    intent_recorded = False
    claimed_intent_id = None
    ledger_error = None
    caps = [args.max_output_tokens] + ([args.retry_truncated_tokens] if args.retry_truncated_tokens else [])
    for send_index, cap in enumerate(caps):
        try:
            # Recheck before an optional retry; never send a stale or concurrently changed package.
            if make_binding(args, model, body_sha) != binding:
                raise ValueError("发送前输入/规则发生变化")
            with open(args.pack, "rb") as fh:
                if fh.read() != original:
                    raise ValueError("发送前复核包发生变化")
            review_evidence.lint_pack(original, binding)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            emit({"ok": False, "stage": "verify_binding", "error": str(exc),
                  "attempt_receipts": attempt_paths}, 2)
        if not intent_recorded:
            try:
                if unattended:
                    review_ticket(args.unattended_state, args.unattended_action, args, binding, consume=True)
                claim = review_ledger.claim_send(
                    args.ledger_dir, scope, binding, args.pack, cycle_value,
                    allow_existing_receipt=bool(args.previous) and not bool(args.new_cycle),
                    previous_path=args.previous if not args.new_cycle else None,
                    recovered_previous=recovered_previous is not None,
                    **({"task_approval": args.task_approval, "task_timeout": args.timeout,
                        "task_round": baseline["round"] + 1 if baseline else 1} if args.task_approval else {}),
                    **({"unattended_round": unattended["round"]} if unattended else {}))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                emit({"ok": False, "stage": "ledger",
                      "error": "复核台账认领失败，未发送: %s" % exc}, 2)
            if not claim["claimed"]:
                stage, message, details = claim["reason"]
                emit({"ok": False, "stage": stage, "error": message, **details}, 2)
            claimed_intent_id = claim["entry"]["intent_id"]
            intent_recorded = True
        try:
            REQUEST_STARTED = True
            if unattended:
                status, raw = offline_transport(model, REVIEW_REASONING_EFFORT)
            else:
                status, raw = responses_post(binding["route"], provider_api_key(binding["route"]),
                                             args.timeout, prompt, cap, stream, REVIEW_REASONING_EFFORT)
        except Exception as exc:
            status, raw = None, "transport error: %s" % type(exc).__name__
        diagnostic = upstream_diagnostic(raw)
        failure: dict[str, Any] | None = None
        if status is None:
            failure = {"stage": "transport", "failure_kind": "transport", "error": raw[:400]}
        elif status != 200:
            failure = {"stage": "upstream", "failure_kind": "http_error", "status": status, "error": "上游 HTTP 请求失败"}
        elif has_failed_response(raw):
            truncated = diagnostic.get("reason") == "max_output_tokens"
            failure = {"stage": "upstream", "failure_kind": "response_truncated" if truncated else "response_incomplete",
                       "error": "上游响应未形成完整裁决；不是科学否决"}
        if failure is None:
            break
        failure.update({"ok": False, "upstream": diagnostic, "model": model, "provider": provider,
                        "intent_id": claimed_intent_id, "guard_id": GUARD_INSTANCE, "cycle": cycle_value,
                        "disposition": "response_incomplete" if failure["failure_kind"] == "response_truncated" else "incomplete_stop"})
        try:
            if unattended:
                failure["unattended"] = {key: unattended[key] for key in ("run_id", "action_id", "round")}
                failure["offline_fixture"] = True
            attempt_paths.append(save_failed_attempt(args.pack, binding, failure, cap))
        except OSError as exc:
            failure["attempt_save_error"] = str(exc)
            emit(failure, 2)
        try:
            review_ledger.append_entries(args.ledger_dir, [review_ledger.attempt_entry(
                scope, binding, attempt_paths[-1], failure["failure_kind"], cycle_value)])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failure["ledger_error"] = str(exc)
        if failure["failure_kind"] == "response_truncated" and send_index == 0 and len(caps) == 2:
            log("max_output_tokens truncation: one explicitly budgeted retry")
            continue
        failure["attempt_receipts"] = attempt_paths
        if unattended:
            failure["usage"] = review_usage(raw)
        emit(failure, 2)
    else:
        emit({"ok": False, "stage": "upstream", "error": "没有完成的审查响应"}, 2)

    text = extract_text(raw)
    if not text:
        # head + tail 都要给：SSE 失败常表现为"开头正常、结尾是错误事件"。
        emit(
            {
                "ok": False,
                "stage": "empty_text",
                "error": "上游返回中没有可提取的文本",
                "raw_bytes": len(raw),
                "upstream": upstream_diagnostic(raw),
                "model": model,
                "provider": provider,
            },
            2,
        )

    validation_error = None
    try:
        report = parse_review(text, len(items), baseline, binding.get("disputes"), first_review=first_review)
    except (ValueError, KeyError, TypeError) as exc:
        report = None
        validation_error = "裁决合同未通过: %s" % exc

    # 审查期间的原件/规则漂移会让意见失去当前绑定，不能追加为有效裁决。
    try:
        if unattended:
            review_ticket(args.unattended_state, args.unattended_action, args, binding, publish=True)
        with open(args.pack, "rb") as fh:
            if fh.read() != original:
                raise ValueError("送审期间复核包发生并行变化")
        review_evidence.lint_pack(original, binding)
        now = datetime.datetime.now().astimezone()
        if make_binding(args, model, body_sha, for_send=False) != binding:
            raise ValueError("送审期间输入/规则发生变化")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "verify_binding", "error": str(exc)}, 2)

    # ---- 4. 追加（不改正文）----
    ts = now.strftime("%Y-%m-%d %H:%M:%S%z")
    block = (
        "\n\n---\n\n"
        "## 5. 复核结论（%s 于 %s 自动填写）\n\n"
        "- 包内原有「## 5. 复核结论（Codex 填写）」占位段按纪律原样保留，实际结论见本节。\n"
        "- 请求的审计模型：`%s`；复核用途：%s\n"
        "- 请求的推理强度：`%s`（上游实际采用情况未由本脚本核验）\n"
        "- 发送时本地通道/provider：`%s`\n"
        "- 复核点：%d；包正文 sha256（本节追加前）：`%s`\n"
        "- 通道：`%s`；路由模型：`%s`（请求值，不代表已核验上游实际身份）\n"
        "- 本次为**自动送审**：结论属审查意见，不是证据，不改变证据层级。\n\n"
        "%s\n"
        % (
            model,
            ts,
            model,
            POINT_TIER[args.point],
            REVIEW_REASONING_EFFORT,
            provider,
            args.point,
            body_sha,
            binding.get("route", {}).get("url", "offline-fixture"),
            binding.get("route", {}).get("wire_model", model),
            text.rstrip(),
        )
    )
    lock_path = receipt_path + ".lock"
    lock_owned, pending_path = False, None
    try:
        # 排他锁防同包并行写回；完整回执先写临时文件，最后原子发布。
        with open(lock_path, "x", encoding="utf-8"):
            lock_owned = True
            if os.path.exists(receipt_path):
                raise FileExistsError("回执已存在，不覆盖")
            expected_after = original + block.encode("utf-8")
            cycle = cycle_value
            receipt = {
                "schema": "research-session/review/v1",
                "intent_id": claimed_intent_id,
                "guard_id": GUARD_INSTANCE,
                "cycle": cycle,
                "round": (baseline["round"] if baseline else 0) + int(report is not None),
                "review_counted": report is not None,
                "raw_report": text,
                "pack_lint": pack_lint, "attempt_receipts": attempt_paths,
                "output_budget": {"first_cap": args.max_output_tokens, "truncation_retry_cap": args.retry_truncated_tokens,
                                  "effective_cap": cap, "http_attempt_limit_per_send": RETRY_ATTEMPTS},
                "binding": binding, "report": report, "error": validation_error,
                "created_at": now.isoformat() if args.task_approval else ts,
                "previous": None if new_cycle else (os.path.abspath(args.previous) if args.previous else None),
                "new_cycle": new_cycle or (previous.get("new_cycle") if previous else None),
                "recovered_previous": recovered_previous or (
                    previous.get("recovered_previous") if args.task_approval and previous else None),
                "rules_transition": rules_transition,
                "pack": {"path": os.path.abspath(args.pack), "before_bytes": len(original),
                         "after_sha256": sha256_hex(expected_after)},
            }
            if unattended:
                receipt["unattended"] = {key: unattended[key] for key in ("run_id", "action_id", "round")}
                receipt["usage"] = review_usage(raw)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".audit-receipt-",
                                             suffix=".pending", dir=os.path.dirname(receipt_path), delete=False) as temporary:
                pending_path = temporary.name
                json.dump(receipt, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            with open(args.pack, "r+b") as fh:
                if fh.read() != original:
                    raise ValueError("写回前复核包发生并行变化")
                fh.write(block.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            with open(args.pack, "rb") as fh:
                after = fh.read()
            if after != expected_after:
                raise ValueError("写回后复核包与预期不一致")
            # link 的目标已存在即失败，不会像 POSIX rename 那样覆盖旧证据。
            os.link(pending_path, receipt_path)
            RECEIPT_PUBLISHED = True
            try:
                review_ledger.append_entries(args.ledger_dir, [review_ledger.receipt_entry(
                    scope, binding, receipt_path, receipt, cycle_value)])
            except (ValueError, OSError, KeyError, TypeError) as exc:
                ledger_error = str(exc)
            os.unlink(pending_path)
            pending_path = None
    except (OSError, ValueError) as exc:
        emit({"ok": False, "stage": "append", "error": "%s: %s" % (type(exc).__name__, exc),
              "pending_receipt": pending_path,
              "hint": "临时回执如存在已保留供核对；仅当包/输入与绑定相符时恢复，不重发掩盖写回故障"}, 2)
    finally:
        if lock_owned:
            os.unlink(lock_path)

    # ---- 5. 复验：前缀字节逐位一致 ----
    with open(args.pack, "rb") as fh:
        after = fh.read()
    if after[: len(original)] != original or sha256_hex(after[: len(original)]) != body_sha:
        emit(
            {
                "ok": False,
                "stage": "verify_prefix",
                "error": "写回后包正文前缀与原文不一致（正文可能被改动）",
                "pack_sha256_before": body_sha,
            },
            2,
        )

    result, code = review_result(receipt, receipt_path)
    result.update({"reasoning_effort_requested": REVIEW_REASONING_EFFORT, "model": model,
                   "provider": provider, "point": args.point, "pack": args.pack,
                   "pack_sha256": body_sha, "appended_chars": len(block)})
    if ledger_error:
        result["ledger_error"] = ledger_error
    if unattended:
        result["usage"] = receipt["usage"]
    emit(result, code)


def _dispatch_main(*, offline_transport=None):
    global REQUEST_STARTED, RECEIPT_PUBLISHED, GUARDED_ID, GUARD_INSTANCE
    REQUEST_STARTED = RECEIPT_PUBLISHED = False
    GUARDED_ID = None
    GUARD_INSTANCE = None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pack")
    parser.add_argument("--point", type=int)
    parser.add_argument("--inputs")
    parser.add_argument("--verify-local-edits")
    parser.add_argument("--expected-receipt-sha256")
    parser.add_argument("--model")
    parser.add_argument("--provider-config")
    parser.add_argument("--responses-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--port", type=int)
    parser.add_argument("--transport", default="responses")
    parser.add_argument("--ledger-dir")
    parser.add_argument("--ledger-backfill")
    parser.add_argument("--resolve-ledger")
    parser.add_argument("--ledger-note")
    parser.add_argument("--unattended-state")
    parser.add_argument("--unattended-action")
    parser.add_argument("--fixture-template")
    parser.add_argument("--resolve-fixture")
    parser.add_argument("--revoke-fixture")
    parser.add_argument("--recovery-template")
    parser.add_argument("--recover-send-lock")
    parser.add_argument("--revoke-send-recovery")
    for flag in ("--dry-run", "--lint-pack", "--validate-report", "--show-contract", "--probe"):
        parser.add_argument(flag, action="store_true" if flag != "--validate-report" else "store")
    args, _ = parser.parse_known_args()
    if args.verify_local_edits is not None or args.expected_receipt_sha256 is not None:
        return _main(offline_transport)
    if (args.unattended_state or args.unattended_action) and offline_transport is None:
        return _main()
    if (not args.pack or args.point not in (1, 2, 3) or not args.inputs or args.dry_run
            or args.lint_pack or args.validate_report or args.show_contract or args.probe
            or args.ledger_backfill or args.resolve_ledger
            or args.fixture_template or args.resolve_fixture or args.revoke_fixture):
        return _main(offline_transport)
    if args.recovery_template or args.recover_send_lock or args.revoke_send_recovery:
        return _main(offline_transport)
    if not args.unattended_state:
        try:
            responses_route(args, args.model)
        except ProviderNotConfigured:
            emit({"ok": False, "stage": "provider", "error": "provider_not_configured",
                  "request_started": False}, 2)
    try:
        inputs = load_inputs(args.inputs)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "send_guard", "error": str(exc)}, 2)
    try:
        scope = review_ledger.validate_scope(args.pack, inputs)
        if args.unattended_state or args.unattended_action:
            from autonomy_run import fixture_root
            fixture_root(scope["project_root"])
        elif review_ledger.is_offline_fixture_path(args.pack):
            raise ValueError("v4 合成项目不得使用正式发送入口")
        else:
            review_ledger.verify_ledger_scope(args.ledger_dir, scope["project_root"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "stage": "review_scope", "error": str(exc)}, 2)
    review_id = inputs["review_id"]
    key = sha256_hex(json.dumps([review_id, args.point], ensure_ascii=False).encode("utf-8"))
    lock_path = os.path.join(os.path.dirname(os.path.realpath(args.pack)), ".review-send-" + key + ".lock")
    guard_home = args.ledger_dir
    if args.unattended_state and guard_home is None:
        guard_home = os.path.join(scope["project_root"], "runs", "run-001", "fixture-ledger")
    try:
        guard = review_ledger.create_send_guard(
            guard_home, scope, lock_path,
            {"pid": os.getpid(), "pack": os.path.abspath(args.pack),
                       "review_id": review_id, "point": args.point,
             **({"unattended": True} if args.unattended_state else {})})
        GUARD_INSTANCE = guard["guard_id"]
    except FileExistsError:
        emit({"ok": False, "stage": "send_guard",
              "error": "同一对象复核正在进行或上次发送状态未知；核查锁和回执，不得自动重发",
              "guard": lock_path}, 2)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "stage": "send_guard", "error": str(exc), "guard": lock_path}, 2)
    GUARDED_ID = review_id
    try:
        return _main(offline_transport)
    finally:
        if not REQUEST_STARTED or RECEIPT_PUBLISHED:
            review_ledger.release_send_guard(guard_home, lock_path, GUARD_INSTANCE)


def main(*, offline_transport=None):
    started = run_manifest.started_at()
    LAST_EMITTED.clear()
    try:
        return _dispatch_main(offline_transport=offline_transport)
    except SystemExit as stopped:
        code = stopped.code if isinstance(stopped.code, int) else 2
        if LAST_EMITTED.get("stage") != "verify_local_edits":
            run_manifest.record_run(LAST_EMITTED, code, started)
        raise


if __name__ == "__main__":
    main()
