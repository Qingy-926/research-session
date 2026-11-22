# research-session

research-session 是一个面向科研全流程的双端插件（Trae / Codex）：把项目接续、论文精读、研究判断、预注册、复现审计、独立复核与写作定稿串成可核验的工作流。当前 **6.3.0 候选**支持三档偏好、计划预算、任务内连续整改和会话内无人值守守卫。自主/无人值守不另问新周期，也没有插件级固定轮数或请求上限；真实后台控制器仍待宿主验收，仅在离线夹具中开放。

## What this project is

research-session packages a research workflow as skills plus small standard-library CLIs. Three scientific review points, evidence binding, a cross-project ledger and send locks apply to every mode. Controlled reviews retain a two-round ceiling; autonomous and unattended session work can continue incremental reviews within one approved task, with verified changes and a shared request/time allocation.

What it deliberately does **not** do:

- It does not ghostwrite papers or fabricate evidence. Drafting without sources is capped and must be marked as unverified.
- It does not invent citations, metrics, reviewer comments, or "novelty" verdicts. Unknown sources stay unknown; unmeasured numbers stay unmeasured.
- It does not grant experiment, budget, data, or publishing permission. Passing a gate is not new authorization.
- It does not run unattended research in the background or across sessions. The background controller accepts only a synthetic fixture project; the in-session guard can work on a real project only inside the current approved session.

## Status

- Version **6.3.0** is a release candidate in this source tree. It has not been published on GitHub, has no public marketplace listing, and has no online CI status yet.
- The background unattended controller is implemented as an offline fixture only; it is not a production release mechanism. The in-session guard runs only in the current approved session.
- Verified environments: Windows + Python 3.13.5 and Ubuntu 24.04 (WSL2) + Python 3.12.3, both with pytest 8.3.4, offline tests only. Clean-host installation, real host loading, and real provider calls are **unverified** (see [Limitations](#limitations-and-unverified-items)).

## Quick start

Full installation, upgrade, uninstall, and troubleshooting steps live in [docs/install.md](docs/install.md). Summary:

### Trae

1. Copy the whole plugin directory into Trae's plugin directory, keeping `.trae-plugin/` and `skills/` together.
2. Restart Trae or trigger the host's plugin reload.
3. Select Research Session in a session and confirm the `research-session` entry skill and its sub-skills are listed.

### Codex

1. Place the same directory in Codex's marketplace plugin directory, named `research-session`.
2. Let Codex read `.codex-plugin/plugin.json` (version `6.3.0`, same metadata as the Trae manifest).
3. Confirm the entry skill and sub-skills are visible in a session.

Both hosts share one source tree; the two manifests must stay identical in version and metadata.

### Codex++ Research Session

配套拓展在输入框上方显示 **Research Session**。调用研究技能时默认自主推进；各聊天保留自己的设置。开启 Codex 原生 Plan，再点无人值守，填写分钟数及选填金额。用户批准具体计划后，任务内整改、验证及分配预算内复核一次覆盖，不另问新周期；AI整理`--task-approval`绑定，沿同一ID累计。三个科研门仍须通过；新协议、范围和预算变化另行裁决。

## Run it offline (no provider)

No gateway, model, or credential is required for local checks. From the plugin root:

```bash
python skills/research-session/scripts/audit_dispatch.py --probe
```

Expected: one JSON line on stdout and exit code `2`:

```json
{"decision": "未完成", "disposition": "incomplete_stop", "error": "provider_not_configured",
 "gate_passed": false, "ok": false, "request_started": false, "review_completed": false,
 "review_counted": false, "stage": "probe"}
```

`request_started` is `false`: the plugin refuses the online path instead of guessing a private endpoint. To see the integrity checks themselves, run `python -m pytest tests/golden -q` (expect `5 passed`; pytest is the only test-time tool).

## Capability matrix

Legend: **Built-in offline（内置离线）** = implemented in this tree and covered by offline tests; **Host or external dependency（宿主或外部依赖）** = the workflow exists but retrieval, rendering, training, or a real model must come from the host or another tool; **Offline fixtures only（仅离线夹具）** = only synthetic control semantics are proven; **Not supported（未支持）** = no verifiable implementation in this release.

| Stage | What the plugin provides | Implementation level | Entry point |
|---|---|---|---|
| Project continuity and recovery | Resume from README, authorization, ledger and direct evidence; read-only binding inspection | Built-in offline（内置离线） | [`research-session`](skills/research-session/SKILL.md), [`review_preflight.py`](skills/research-session/scripts/review_preflight.py) |
| Paper deep reading and source audit | Layered reading with source locations, unread gaps, and L0 source audit | Host or external dependency（宿主或外部依赖） | [paper-deep-reading](skills/paper-deep-reading/SKILL.md), [paper-source-audit](skills/paper-source-audit/SKILL.md) |
| Research judgment and innovation | Falsifiable contribution framing, stress testing, novelty/baseline review | Built-in offline（内置离线）; evidence supplied by the user or retrieval | [innovation-design](skills/innovation-design/SKILL.md), [innovation-stress-test](skills/innovation-stress-test/SKILL.md), [novelty-and-baseline-review](skills/novelty-and-baseline-review/SKILL.md), [research-mentor](skills/research-mentor/SKILL.md), [task-perspectives](skills/task-perspectives/SKILL.md) |
| Experiment design and preregistration | Claim-driven design, pre-execution criteria, review point 1; formal gate needs an explicit provider | Built-in offline（内置离线）design; live review external | [claim-driven-experiment-design](skills/claim-driven-experiment-design/SKILL.md), [preregistration](skills/research-session/references/preregistration.md) |
| Reproduction preflight and code audit | Read-only preflight, code causality audit, no PASS on unknown inputs | Built-in offline（内置离线） | [reproduction-preflight](skills/reproduction-preflight/SKILL.md), [research-code-audit](skills/research-code-audit/SKILL.md) |
| Experiment matrix, metrics, log governance | Frozen matrix checks, metric recomputation, log and archive lists | Built-in offline（内置离线）; data collection and training stay external | [experiment-matrix-audit](skills/experiment-matrix-audit/SKILL.md), [metric-verdict-audit](skills/metric-verdict-audit/SKILL.md), [research-log-governance](skills/research-log-governance/SKILL.md) |
| Independent review and controlled recovery | Three review points, evidence binding, send lock, revocable recovery, no automatic resend | Built-in offline（内置离线）contracts and tests; live dispatch needs an explicit provider | [independent-review](skills/research-session/references/independent-review.md), [`audit_dispatch.py`](skills/research-session/scripts/audit_dispatch.py) |
| Writing, finalization, reviewer response | Evidence-cited drafting, finalization gate, reviewer-question mapping | Host or external dependency（宿主或外部依赖）for rendering and live review; review judgment is built in | [research-introduction](skills/research-introduction/SKILL.md), [research-reviewer](skills/research-reviewer/SKILL.md), [manuscript-writing](skills/research-session/references/manuscript-writing.md) |
| Background unattended controller | Startup card, bounded chain, pause/resume | Offline fixtures only（仅离线夹具） | [autonomous-start](skills/research-session/references/autonomous-start.md), [`autonomy_run.py`](skills/research-session/scripts/autonomy_run.py) |
| Full response-letter workflow | End-to-end rebuttal letter generation | Not supported（未支持） | — |

Not supported in this release: a built-in multi-source retriever, retrieving/training/monitoring stacks, a server-side lineage database, a full benchmark/eval-viewer system, and production unattended research. These are not implied by the rows above.

## Configuration

Offline checks need no configuration. Online review requires an explicit Responses endpoint and model; there are no implicit defaults:

- Start from [docs/provider-config.example.json](docs/provider-config.example.json) (only `url`, `model`, `provider`, `api_key_env`).
- Pass it with `--provider-config` or set `RESEARCH_SESSION_PROVIDER_CONFIG`.
- Credentials are read only from the environment variable named by `api_key_env`; they are never written to config files, logs, receipts, or run manifests.

See [docs/install.md](docs/install.md) for flags, environment variables, and troubleshooting.

## Contracts and observability

The versioned v1 contracts (provider/adapter, run manifest, recovery semantics) are documented in [contracts/README.md](contracts/README.md). Run manifests are **off by default**: set `RESEARCH_SESSION_RUN_MANIFEST_DIR` to have the review dispatch, preflight, and autonomous-control scripts write one redacted `<run_id>.json` per run; nothing is written when the variable is unset. There is no telemetry service.

## First-review contract for isolated candidates

Version 5.2.0 adds an opt-in `first_review` contract for isolated candidates: the sender lists
key claims before dispatch, the reviewer must finish the fixed checks and those claims, trace each
conclusion back to original fields/versions/denominators, and sweep every known position sharing a
root cause. Each blocker must state location, actual versus expected, impact, minimal fix and
acceptance. Blockers are routed `local` (exact wording correction in a bound Markdown/text report)
or `recheck` (a specific judgment is still required). `audit_dispatch.py --verify-local-edits`
reconstructs the reviewed bytes offline, rejects duplicates, omissions, extra edits and hash drift,
and reports `locally_verified` with `gate_passed=false`. A mixed remediation round only re-reviews
remaining judgments and affected dependencies while accounting for every old blocker. The three
scientific gates, authorization, xhigh effort, receipts and the controlled two-round ceiling per `review_id`
are unchanged; local verification is not a gate pass, not a receipt rewrite, and not new
authorization. This is a process change; it is not evidence of stronger model reasoning or general
research-quality gains.

## Evidence-driven retrieval and judgment

Version 5.1.0 adds bounded, gap-driven retrieval, source-location and access-level tracking,
claim-support and counterevidence checks, and a whole-answer scope check. Retrieval still
uses the host's available tools; this plugin does not supply a search backend or certify
semantic entailment. A local excerpt builder reuses the citation guard's sources list
without silently truncating evidence. See [workflow, CLI and evaluation limits](docs/research-quality.md).
These additions are process changes, not proof of stronger model reasoning or universal
research-quality gains.

## Limitations and unverified items

- Clean-machine installation, real Trae/Codex plugin loading, and real provider calls have not been verified; tests mark them as explicit skips rather than passes.
- The code uses `X | None` and `dict[str, ...]`, so Python 3.10+ is required; 3.12.3 (Ubuntu) and 3.13.5 (Windows) have been exercised, 3.10 has not.
- CI workflow definitions live in `.github/workflows/` but have not been pushed or run, so there is no online CI status. Release packages are built locally and reproducibly with `tools/package_release.py`; no release artifact has been published.
- Deep retrieval, real training, long-running monitoring, and a complete reviewer-response letter workflow are outside this release.

## License, contributions, and security

- License: [Apache-2.0](LICENSE).
- Contributions: [CONTRIBUTING.md](CONTRIBUTING.md); release and rollback rules: [docs/versioning-release.md](docs/versioning-release.md).
- Security reports: [SECURITY.md](SECURITY.md). Do not post credentials or ledger contents in public issues.
- Third-party references and asset notes: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Version history: [CHANGELOG.md](CHANGELOG.md).
