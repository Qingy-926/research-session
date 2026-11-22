## §6 独立模型异步复核（三个固定点）

> **决策索引（先读这里，细节按需下查）**
> - 送审前：包只能放 `01-资料/reviews`；先 `--dry-run`；脚本只用显式 Responses provider。
> - 读结果：看 `review_completed` 与 `gate_passed`，不只看是否返回；exit 0/3/2。
> - 受控模式：主审 + 一次整改，两轮后交用户；新周期需用户明确决定。
> - 自主/无人值守：沿同一 `review_id` 与任务批准累计，不派生新周期；无固定轮数。
> - 科学硬门：数据泄漏、协议矛盾、缺原始证据始终阻断。
> - 格式失败/截断：先离线诊断，不重发未知响应；受控格式纠正见下。
> - 台账异常/未知发送：先人工核对，不自动重发。
> - 候选首审：只在隔离候选，本地验收不等于正式放行。
> - 一切结论是审查意见，不是实证或授权。

轮次与预算见 `SKILL.md` §2；三个科研门、用户授权和原始证据不变。原 `--unattended-state` 控制器只用于离线合成夹具；真实会话的任务内复核走普通 dispatcher 的 `--task-approval`。

等待期间请求方保持静默：不重复催问、不重复送审、不空转输出；可推进不依赖该门的已授权工作，但不得把未复核结果写成已通过。旧双会话固定分工退役，主模型可按收益使用授权内子任务；下列三个复核点仍须完成，不由一般委派替代。

| 复核点 | 时机 | 必查项 |
|---|---|---|
| 1 批准前 | 预注册草稿完成后、执行前 | 判据出数前写死、门阈值依据、输入哈希门完整 |
| 2 出数后 | 正式判据出数、登记前 | 数字与 JSON 一致、未事后改判、辅助读数齐 |
| 3 汇报前 | 对外汇报文稿定稿前 | 数字四要素齐全、措辞边界遵守、不声称清单、fragile 宣称已按三选一处置（paper-audit.md 双门核查） |

流程（模板、JSON 合同与增量包见 [codex-review-pack.md](codex-review-pack.md)）：

1. **固定审查对象与关闭条件**：协议是否自洽、实现是否符合协议、运行是否就绪分栏说明，不混成“整个项目无缺陷”。实现主张须审前固定适用输入域、可信边界、正/负/边界验收用例与测试入口（模板 `acceptance_cases.json`）；科学/来源主张固定所需原件、字段与核对方式，不伪造程序测试。主会话先完成可机械检查的验证，制作**自包含最小证据包**到 `01-资料/reviews/YYYYMMDD-HHMM__codex复核包__<主题>.md`；只含本点所需原文、数字字段与证据片段，不堆历史意见。另建输入 JSON 清单：固定跨版本不变的 `review_id`，列全受控原件及依赖 `files[{path,sha256}]`。复核包只能从项目权威 `01-资料/reviews` 送审；脚本按项目身份、`review_id`、复核点维护用户级台账，移走回执、备份目录或整项目复制不能新开首审。发送前在台账锁内重核历史并原子登记发送意图；同一项目/ID/点只允许一个进程进入发送。历史项目用 `--ledger-backfill <项目根>` 只读回填，不改旧回执或判词。
2. **先本地预检，再首次送审**：同一命令先加 `--dry-run`，检查绑定、包内保真度与当前合同模板，确认 active ID 与整改表一致。调用前按[入口与只读核对](plugin-invocation.md#independent-review-entry-and-read-only-continuity-check)设置 `RS_SKILL_ROOT`，只用本会话实际加载技能附带的脚本；同名共享副本与插件副本不能混用。新会话先从 README 定位原件/输入清单/历史回执，用 `review_preflight.py` 核脚本路径、规则摘要与精确兼容关系；该检查不发送、不碰台账、不继承旧通过。`--show-contract --point <1|2|3>` 输出生产/消费共用字段与模板；`--validate-report <原报告或回执> --point ...` 仅离线解析已收到意见，可追加 `--previous <基线>`，不访问代理、不产生放行。原文/源码片段只用 `review_evidence.py --source` 从文件生成，带路径/SHA/行范围并逐字节对账；测试摘要绑定机器可读 `verification.json`（命令、起止时间、exit、输入及日志 SHA）。`--lint-pack` 可独立离线检查，正常送审也强制执行同一检查；细则见 [review-evidence.md](review-evidence.md)。

   ```bash
   python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
     --pack "<包绝对路径>" --point <1|2|3> --inputs "<输入清单绝对路径>"
   ```

   脚本只使用显式配置的 Responses 通道：`--provider-config <JSON>` 或 `--responses-url <.../responses>` 与 `--model <模型>`，也可用 `RESEARCH_SESSION_*` 环境变量；两者缺一即 `provider_not_configured`，不取锁、不发请求。JSON 白名单 `url/model/provider/api_key_env`；凭据只从 `api_key_env` 命名的环境变量读取，不写入配置/绑定/回执/日志。仅限核实端点，不自动切换通道；每次发送最多一次 HTTP 请求。请求名与响应模型字段不证明最终供应商或实际推理强度。意见**追加到包尾**并生成同名 `.review.json` 回执，不改正文或科研原件。三点均须显式给模型，固定 `reasoning.effort=xhigh`；包尾记录请求值，不代表已核验上游实际强度；上游拒绝该设置时复核未完成，不自动降档。**读 `review_completed` 与 `gate_passed`，不能只看送审是否返回。** `decision` 只用通过/待整改/不通过/未完成：exit `0` = 当前绑定版本通过，`3` = 待整改/不通过或轮次上限，`2` = 通道、材料、绑定或裁决合同未完成。未完成保留具体错误/缺证，不得视为通过，也不以重新送审替代诊断。格式不合格先用保存原文离线定位/无损归一；不得把 `report=null` 当复核基线再送整包。格式失败、截断与首审 `repair.patch` 的受控恢复见[受控格式纠正](#首审报告的受控格式纠正)，不计实质轮次、不新增重发预算、不继承放行。输出默认上限 32768，可用 `--max-output-tokens` 调整；截断属未完成，不是科学否决，失败尝试另留诊断；默认不重发，显式 `--retry-truncated-tokens <更高cap>` 才可重试一次，仍用原模型/`xhigh`，不降档。
3. **修复优先，不只转述**：实现阻断同次返回 `repair`：源码足够时给可应用 patch、目标 SHA 与失败用例；否则明确缺哪段上下文、哪项用户取舍或为何不能安全修改。纯证据/科学矛盾不强造补丁。当前脚本只收发文本，不赋予模型文件写权限。主会话核对目标版本、实际 diff、授权与范围后，直接把可接受补丁应用到权威实现/测试并验证；有证据地否定则说明依据。整改记录含问题 ID、接受/质疑、实际改动、命令/结果/产物；改了什么与是否独立通过分开。审查不通过只暂停依赖硬门的步骤，不阻止已授权普通修复；已暂停或只准优化技能时不得借此恢复项目修改。科学协议、阈值、seed、数据、目标、预算、权限、共享配置与冻结证据不在补丁权限内。复核方写补丁不等于已落盘，也不能自称独立验收通过；须由主会话实际回归。
4. **分级处理**：`blocking` 必须定位当前阶段的真实缺陷与验收条件；`advice` 不阻断；`execution_gate` 留到所属执行硬门，不能把当前硬门降为建议，也不能因未获 GPU/训练授权否决只审设计的材料。实现阻断须给在声明输入域/真实调用链上可达的反例与可检验断言；仅有“可能还有漏洞”无具体证据时列建议。论文缺证、泄漏或协议矛盾可以原件核对证明，不因写不成 pytest 降级；静态推演不能冒称已运行。计数/比例断言附可复算定位。销项和时间线用 `verification_status` 明确本地/外部核实、未核实、未采集；状态不是通过开关。非必要历史过程缺失时收窄主张；科学硬门不因此降级。
5. **复查阻断项**：主会话修改并本地验证后制作**增量包**：上一轮回执、逐项整改/未解决说明、实际 diff、验证结果及必要原始片段；沿用 `review_id`，更新输入 SHA，调用加 `--previous "<上一轮 .review.json>"`。第二轮只查旧问题、变化与受影响依赖；新增阻断需具体新证据，不能无依据扩张。规则摘要不同时，仅已核实的兼容版本可接续；预检与新回执记录版本迁移，不改旧回执或继承放行。原验收条件已由绑定证据满足就关闭；发现新反例须写清违反哪项原主张，追加独立用例或修正验收，不以换措辞无限延长。历史已关闭 ID 另作注记；未知 ID 不能替代当前 ID。有证据质疑计数/事实时，在第二轮 `disputes` 给 ID、绑定产物及复算命令/JSON pointer；复核方同轮逐项回应。校验器不执行包中命令；纠正一个计数不自动消除同项其他缺证，不为争议自动加轮。
6. **按任务模式控制后续审查**：自主/无人值守沿原 ID 与累计轮次，在批准范围、实质进展与预算内持续增量复核，不要求新周期；开发者受控模式一次主审、一次整改，仍有阻断/争议则写回 README 由用户裁决，后续才用「用户决定的新周期」。任何模式都不换名清零、不自动通过、不反复全文互审。台账出现回执移出权威目录、未闭合发送意图、来源不明历史锁或无法解析记录时，先人工核对，不自动重发；损坏回执/尝试/锁记 `unreadable` 并项目级阻断。扫描访问失败使 `complete`/`review_complete` 为 false 并阻断新送审；使用 Windows 长路径前缀；junction 记入 `link_exclusions`（内部链接由真实目标覆盖，外部目标含 `reviews` 仍阻断，断链不阻断）。损坏文件在原路径修复后完整回填；文件消失不能证明已恢复。确认上游未送出后用 `--resolve-ledger <entry_id> --ledger-note <依据>` 解除意图；旧版已送出的再补 `--ledger-receipt <回执路径>`（须已登记且对象/点/周期/包摘要一致，记录 SHA256；同摘要不自动证明对应）。解除只消除“发送状态未知”，不等于新一轮复核，也不新增授权。自主/无人值守有效非通过回执返回 `task_revise`，不论第几轮；受控模式用 `revise_once/rejected_stop/needs_user_decision`。无法补齐、无进展或约定验证不支持主张时如实交付，允许停止/放弃/缩小；改变任务目标的仍须用户决定。
7. **复用绑定，不复用口头结论**：同对象、复核点、原件/依赖、包正文与规则不变时直接复用回执，不再请求模型；任一绑定变化不能继承旧通过。旧包/意见/回执留作沿革，README 仍是唯一当前交接入口。
8. 结论始终是审查意见，不是实证，不新增执行、登记或定稿授权；已获授权时不重复求批；项目硬门与 `autonomy-bounds.md` 边界不变。

### 已核实的测试夹具误登记（仅台账维护）

仅处理已独立核查的具体非权威回执：无正式 schema/pack/intent_id/模型/包摘要绑定。目录名、缺字段、同名源码、哈希或非空说明都不能证明来源；权威 `reviews` 及其子目录中的回执永不允许按夹具排除。

维护者先从台账原条目生成未裁决草稿；独立审核者核对测试源码、输入、完整生成字节与运行记录，在审核原件中逐条给结论、依据、未核实项和限制，并由宿主证据核职责分离。不能自填两个名字冒称独立审核。主会话绑定审核原件 `review_record{path,sha256}` 与全部 `evidence[{path,sha256}]`；路径绝对，审核原件/证据/夹具及其父路径不得为重解析点或符号链接，末端不得有硬链接。

原生消费夹具另加 `native_provenance{path,sha256}`（schema `research-session/native-fixture-provenance/v1`）：`producer` 绑定留存生成源码，`verification` 绑定 `research-session/verification/v1` 成功记录及日志；`rows` 每条含精确 `entry_id`、原台账 `target`、`artifacts[{original,preserved}]`（含回执与其包），前后 SHA 一致。项目内存在匹配 intent/guard/对象/点/包摘要的发送记录时拒绝；任一证据失效恢复阻断。请求、费用、未知发送、轮次与旧条目不删除或重置。

```bash
python "<已安装技能>/scripts/audit_dispatch.py" --fixture-template "<原 entry_id>"
python "<已安装技能>/scripts/audit_dispatch.py" --resolve-fixture "<原 entry_id>" \
  --fixture-attestation "<审核记录绝对路径>" --fixture-sha256 "<已核 SHA256>" --dry-run
python "<已安装技能>/scripts/audit_dispatch.py" --resolve-fixture "<原 entry_id>" \
  --fixture-attestation "<相同记录>" --fixture-sha256 "<相同 SHA256>" --confirm-independent-review
python "<已安装技能>/scripts/audit_dispatch.py" --revoke-fixture "<原 entry_id>" --ledger-note "<撤销依据>"
```

模板 `decision=null`，不是判断或授权。先预检，再由操作者确认已核实独立原件；确认参数只留痕，不认证身份。纠错/撤销与送审互斥，不发送、不改旧回执/判词/次数/授权；台账只追加。扫描前至请求发出期间停止项目相关外部写入；发送进程崩溃不自动清除意图或锁；未知发送按下一节核查。

### 已终止发送的受控恢复（仅本地维护）

进程退出、超时、无回执、模型名无效或操作者说明，单独均不能证明可以重发。先停止该对象的外部写入及旧客户端使用，回填所有已知同身份项目副本，再由实际加载入口生成绑定草稿：

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" --recovery-template "<锁 entry_id>"
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" --recover-send-lock "<相同 entry_id>" \
  --recovery-evidence "<核查记录绝对路径>" --recovery-sha256 "<已核 SHA256>" --dry-run
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" --recover-send-lock "<相同 entry_id>" \
  --recovery-evidence "<相同记录>" --recovery-sha256 "<相同 SHA256>" --confirm-recovery-review
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" --revoke-send-recovery "<recovery_id>" --ledger-note "<撤销依据>"
```

草稿 `decision/outcome` 为空；模板与 dry-run 不改台账、不清锁。应用前须全部满足：

- 所有已登记根可完整读取，无无法解释的复核记录；原进程已退出或 PID 已复用。
- 精确绑定项目、对象、复核点、周期、包摘要、锁实例、intent 与全部尝试；有后续 intent、其他对象锁或绑定变化时重核。
- 确认失败且无有效回执：`decision=recover_failed_send`，`outcome=not_accepted` 或 `terminated_without_valid_review`；结构化 4xx（不含 408/409/499）且有上游 code 可作证据；超时/5xx/截断须取得逐请求 gateway/upstream 终态原件，不能据本地进程死亡推断远端终止。
- 不确定请求逐个填 `upstream_confirmations`（绑定 intent/guard、response_id、终态、核查时间与 `record{path,sha256}`）；无法取得终态则保持阻断。
- 认领前崩溃未发送：仅现代 guard、完整历史无对应 intent/attempt/receipt 时，填 `source_kind=local_pre_request,terminal_state=not_sent`，绑定 guard、运行轨迹与 `implementation[{role,path,sha256}]`（dispatcher/ledger，SHA 与 guard 创建时一致）。缺证据不能据“没有 intent”推断未发送；已有 intent 或尝试不能走此入口。
- 已有有效回执：`decision=reconcile_receipted_send,outcome=receipt_preserved`，重验回执合同、包前后摘要、intent/guard/周期，保留原回执/判词/次数；不能用旧同对象回执关闭新锁，不能把回执损坏归为失败后重发。

独立审核者核对原件、逐请求终态与限制，在单独审核原件留结论；操作者绑定 `review_record{path,sha256}`，如实填 operator/reviewer/reviewed_at/basis/limitations；两个名字不认证身份。证据原件及父路径不得为重解析点，末端不得有硬链接。

应用先追加 `send_lock_prepared`、归档精确锁与包快照，再解除并追加 `send_lock_recovered`；历史 intent/attempt/receipt/判词不改。恢复中断后沿用相同证据重入并重核历史；不因物理锁消失认定完成。撤销只追加记录，不重建物理锁；证据失效则重新阻断。恢复不新增次数、预算、新周期或执行授权，也不自动发起下一次审查。

### 首审报告的受控格式纠正

已保存且绑定包尾的首审报告若有覆盖状态/问题关联矛盾，不能由请求方改判或重送整包。仅在明确的格式维护与请求预算授权内，使用[格式纠正命令](codex-review-pack.md#受控格式纠正旁证)。只适用原路径的正式 round0/report=null/review_counted=false 合同失败；精确兼容规则、原模型/provider/credential route 与 `xhigh` 不变。单 HTTP；原发送身份在共享台账目录原子认领；原件副本、并发或移走本地 marker 均不能增加请求。未知/失败/不合格纠正保持停止，无自动重试。

纠正只允许 coverage 的 status/finding_ids 变化及保留完整上下文的 locator 数组归一；原判词、问题、证据、修复、定量值及类型、理由和缺证逐项不变。收到有效终态也先保存原返回再解析。原回执、原包与历史轮次不改；旁证 `gate_passed/review_completed/review_counted` 均 false。后续 `--corrected-previous` 重新核原件、请求、共享认领、原始返回与旁证并绑定全部摘要；仅恢复内存中 effective round1 的非通过基线，普通整改最多到 round2，不继承放行或新增权限。可信边界仍是维护者与台账/证据写权限。

### 任务内连续整改

适用于已由宿主核实用户来源的自主推进/无人值守任务批准。AI 从现有任务书和对话整理一次绑定，不要求用户重复批准或手填 JSON。沿用 `review_id`、point、cycle 与完整旧判词，round 逐次累计；可接续精确兼容的 6.0.1 终态，不派生 `__cN`。原计划若明确禁止后续调用或预算已过期，须遵守；新版不能追溯扩大旧授权。

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<增量包>" --point 1 --inputs "<当前原件清单>" \
  --previous "<上一有效回执>" --task-approval "<任务批准绑定.json>" --timeout 1200
```

首次送审也带 `--task-approval`（省略 `--previous`）；先 `--dry-run` 预检。批准记录、批准来源、全部进展验证与日志须纳入当前 `files`。任务记录 schema `research-session/review-task/v1`：

```json
{
  "schema": "research-session/review-task/v1",
  "task_id": "approved-task",
  "mode": "autonomous",
  "project_id": "category/project-123",
  "review_ids": ["stable-object-id"],
  "points": [1, 2],
  "approval_source": {"path": "<absolute user decision record>", "sha256": "<full SHA256>"},
  "started_at": "<absolute timestamp with timezone>",
  "deadline": "<absolute timestamp with timezone>",
  "closeout_seconds": 1800,
  "max_requests": 12,
  "reserved_requests": 2,
  "revoked": false
}
```

`mode` 也可为 `unattended`。`max_requests` 是本任务总请求额度；台账锁内累计 `started_at` 后全部 dispatcher 发送（意图、失败尝试、最终回执，含旧版显式截断重试），跨对象/点共用；仍有未知发送时停止。格式纠正、委派等其他通道须事先计入 `reserved_requests`。单次 timeout 加收尾须容纳在剩余时间内。

用户再次明确批准时，用新文件保存续批并增加 `continuation: {"previous":{"path":"<上一批准绝对路径>","sha256":"<完整 SHA256>"},"effective_at":"<带时区的生效时间>"}`。只有批准来源、截止时间和续批链接可变，所有链接批准与各自的独立用户来源均须纳入当前 `files`。新版截止时间须晚于旧截止时间。历史回执以各自批准的生效时间至截止时间或被接续时间（取较早者）为有效窗口，新发送使用当前窗口。批准间隔不追溯生效，请求累计仍从最初的 `started_at` 算起。

增量清单还须含 `remediation_progress:[{finding_id,path,verification}]`：上一回执当前 blocking ID、实际改变的受控原件或新增证据，以及本轮 `verifications` 中的成功机器记录；验证须在上一回执后实际执行，绑定改动原件、日志与前后 SHA；换包名、改批准或只生成日志不算进展。无新进展停止重复送审。发送锁、未知发送、原合同与科学硬门照旧；此入口不发起循环、不自动重试、不授予执行或后台能力，每次只发一次 HTTP。计量/时限失效、批准撤回、范围/协议变化或用户暂停即停。

### 用户决定的新周期（仅开发者受控模式）

仅适用于开发者受控模式；自主/无人值守用上一节。两轮按同一 `review_id` 计数，不因换包名、换对象 ID 或重开主审而重置。触发点是两轮结束（`needs_user_decision`）或首轮明确否决（`rejected_stop`）；首轮待整改先走整改复核，首轮未完成/截断先诊断补证。只有用户明确决定再开周期才执行；普通“继续修改”不自动解释为该决定。

脚本核对：上一周期须已两轮结束或首轮否决；新 `review_id` 必须是根 ID + `__cN`，非新周期 ID 不得以 `__cN` 结尾；新包与上一周期回执同目录；两者至少共享一个非回执受控原件；决定记录与上一周期最终回执须列入 `--inputs` 的 `files`；决定记录须含上一周期 `review_id`、回执 SHA256、新 `review_id`（各占一行）。脚本不认证用户身份。

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<新周期包>" --point <1|2|3> --inputs "<含决定记录与上周期回执的清单>" \
  --previous "<上周期最终回执>" --new-cycle "<决定记录>"
```

1. **记录用户决定**：在 `01-资料/reviews/` 建 `YYYYMMDD-HHMM__用户决定新周期__<主题>.md`，写明用户原话（逐字引用，不得代写/概括）与时间、对象与点、上一周期 `review_id`/回执路径/SHA256/轮次/结论/未解决项、为什么需要新周期、新 `review_id` 与本次不审范围；同步写入项目 README。
2. **区分新周期与新对象**：研究结论、协议或对照对象实质改变 → 新对象，新建 `review_id`；对象与点不变、只是再给一个周期 → 派生 `review_id`。
3. **绑定旧证据**：`--inputs` 除常规原件外，必须列入决定记录与上一周期最终回执；新包背景引用用户决定原话并列出未解决项摘要。
4. **送审与上限**：用派生 ID 新建包，带 `--previous` 与 `--new-cycle`；轮次从第 1 轮开始，仍受同一对象两轮上限约束；旧回执/包/判词只读保留。
5. **不继承不放行**：新周期不继承旧“通过”或待整改；旧阻断逐项说明（已关闭给证据，未解决保留，范围变化写原因）；决定记录与新包列全旧 `blocking` ID 及状态；结论仍是审查意见，不新增授权。
6. **一次决定一个周期**：每个新周期独立决定记录；不得预先批量派生 `__c3`/`__c4`，也不得在无新决定时连续派生；用户不再决定时坚持两轮上限并交付分歧。

### 复核全面性与不得无据扩张

**隔离候选首审（显式 `first_review`，不适用于已安装版本）**：

1. 主会话列出材料实际作出的关键主张，逐条编号，给结论位置与对应原件；固定必查项不被该清单替代。
2. 复核方先核完固定项与关键主张，再从结论反查原件的字段、版本、口径与分母；发现错误后横查同根因涉及的正文、表图和摘要，同根因合并一项但列全位置。缺证只冻结对应判断，其余继续查完。
3. 每条核对记录标明依据/缺证并关联问题。阻断项写清位置、实际与预期、影响、最小改法、验收；代码原文够就给最小补丁与用例，文字给替换句；无法确定时点明缺哪份原件、字段或决定。
4. 首审有必要缺证 →「未完成」并列出已证实问题，先定向补证，不自动重送；已有充分证据否决主张 →「不通过」；无阻断才「已通过」。待整改逐项标明可本地验收还是需独立判断，并写明后者具体判断什么。

本地验收候选只限复核点 3 已绑定的 Markdown/文本展示更正；所有阻断均满足、原证据与结论不变时，主会话按首审精确替换逐项整改并离线验证；不得变更数据、口径、协议、科学结论或授权。候选输出 `locally_verified`，不写回回执、不等同正式科研门放行。混合项先做本地更正，第二轮仍逐项交代旧阻断，只审需判断项与受影响依赖。输入与命令见[候选合同](codex-review-pack.md#候选首审合同)。

- 首轮一次性给出当前阶段所有可证实阻断，`location/change/acceptance` 写清“改哪里、改成什么、如何验收”；不得只给方向。
- 风格、措辞、可读性、可选优化或“可能还有漏洞”只能记 `advice`；`blocking` 只留会影响当前阶段有效性、且能用原件、调用链或最小反例证明的缺陷。
- 整改复核只审旧阻断、实际 diff 与受影响依赖；无关新问题只能记 `advice` 或留待新周期；只有新证据表明旧结论失效才可新增阻断并说明违反哪条原主张；数据泄漏、协议矛盾、缺原始证据等科学硬门不在此限。
- 同一问题不得换措辞重复阻断，也不得每轮抛一个新阻断无限延长；首轮列全、后续收敛是复核方责任。
