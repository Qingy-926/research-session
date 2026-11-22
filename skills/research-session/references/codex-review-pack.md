# Codex 复核包（自包含）· 模板与各复核点必查项

> **决策索引（先读这里）**
> - 用本文件只做两件事：按复核点查必查项；按模板装包。
> - 合同语义、发送锁、台账、恢复：以 `independent-review.md` 为准。
> - 包内数字必须"路径 + 字段"；不确定写"未核实"。
> - 首轮一次列全阻断；风格/措辞/可选优化只能记 advice。
> - 整改轮只查旧阻断、diff、依赖；无关新问题留给新周期。
> - 通过不得有阻断或必查项存疑；关键材料不够即未完成。

> 目标：Codex **只读这一个文件**就能完成复核，不需要访问项目其他文件。
> 包内所有数字必须来自原始产物并注明"路径 + 字段"；不确定的写"未核实"，不猜。

脚本命令先按 [调用入口](plugin-invocation.md#independent-review-entry-and-read-only-continuity-check) 设置 `RS_SKILL_ROOT` 为本会话实际加载的技能根；不凭同名跨副本调用。

自主推进和无人值守使用[任务内连续整改](independent-review.md#任务内连续整改)的 `--task-approval`，保持同一 ID 与累计轮次，不要求新周期。下文新周期模板仅适用于开发者受控模式；报告合同与三个必查点共用。

## 一、各复核点必查项

### 复核点 1 · 预注册批准前

- [ ] 判据（门阈值 + 分支动作）全部出数前写死，无"视结果而定"的余地
- [ ] 每个门阈值给了依据（历史均值 / σ / 成本约束），不是拍脑袋
- [ ] 输入哈希门覆盖全部会影响结果的输入（数据、协议 bundle、评分脚本）
- [ ] marker 语义 fail-closed（只在 rc=0 后写）
- [ ] 单变量声明：与对照的差异只有一个
- [ ] 诚实边界写死了不得声称的内容
- [ ] 已知先验（设计者已见过的数据）已声明（如补充预注册场景）

### 复核点 2 · 正式判据出数后（登记前）

- [ ] 汇总数字与原始 JSON 字段逐位一致（列出 JSON 路径 + 字段名 + 值）
- [ ] 判定命中预注册写死的分支，未事后改判
- [ ] 预注册要求一并登记的辅助读数齐全（如同构区间、极差、配对差）
- [ ] 已知缺陷（0 字节文件、缺失产物等）如实注记
- [ ] 证据层级如实（本地有什么 / 缺什么）

### 复核点 3 · 对外汇报前

- [ ] 每个数字有四要素：数据集、协议、指标、seed
- [ ] 出处可回溯（总账编号 + 原始产物路径）
- [ ] 措辞边界逐条过（如：部署线不得称 EER、不得称"接近复现"等项目特定禁语）
- [ ] "不声称"清单：未复现 / 部分改善等结论没有夸大
- [ ] 与既有裁定一致，或明确说明为何修订并另行登记

## 二、复核包模板

````markdown
# Codex 复核包 · <主题>（复核点 <1/2/3>）
- 生成：<YYYY-MM-DD HH:MM>；生成者：<会话 / AI 名>
- 项目：<项目名>；请求：<请 Codex 做什么裁决 / 检查>
- 对象：<协议 / 实现符合性 / 结果 / 文稿>；当前要证明的主张、适用输入域和不审范围：<明确列出>
- 复核要求：首轮一次列全当前阶段可证实的阻断项，并写清具体修改位置、改法与验收条件；不用风格、措辞或可选优化制造阻断。整改轮只查旧阻断、实际 diff 和受影响依赖；与修改无关的问题只有在新证据使旧结论失效时才新增阻断，数据泄漏、协议矛盾、缺原始证据等科学硬门仍须阻断。

## 1. 背景与现状（自包含，3–6 行）
（当前权威数字 + 总账编号；本包要复核的对象是什么；为什么现在复核。用户决定的新周期还须写：用户决定原话与时间、派生 review_id、上一周期结论、未解决 blocking ID 摘要）

## 2. 待复核材料
（预注册判据表 / 数字表 / 汇报稿段落，直接粘贴进包）
数字表格式：| 值 | 原始产物路径 | 字段名 |
实现附件只附相关 diff、源码/测试绑定和验收矩阵；与本次裁决直接相关的函数/断言片段可展开，不堆叠全部历史。

## 3. 我方自查结论（逐条对照必查项）
（每条：通过 / 不适用 / 存疑 + 原因）

## 4. 请 Codex 裁决的问题（编号列出）
1. ...

## 5. 复核结论（Codex 填写）
（脚本追加结构化意见；通过 / 待整改 / 不通过 / 未完成）
````

输入清单与包分开保存，使用真实原件/依赖的完整 SHA256；不把会追加意见的包自身、本周期回执或当前状态 README 纳入循环绑定；上一周期最终回执作为新周期入口证据时，按新周期规则列入 `files`。清单只作版本绑定，不授权执行，也不能用哈希代替语义证据：

```json
{
  "review_id": "本项目-本研究对象的稳定标识",
  "files": [
    {"path": "原件的绝对路径或相对本清单的路径", "sha256": "实物的64位SHA256"}
  ]
}
```

`files` 应覆盖所有会改变本次裁决的原件、数据/协议/评分等依赖。审计模型没有文件工具，不假设它能按路径打开附件；缺材料时补所需片段，不机械嵌入整仓库或历史长日志。

## 三、送审与裁决合同

完整送审语义、锁、台账与恢复见[独立复核](independent-review.md)。本节只列包侧要点。

首次发送前在同一命令末尾加 `--dry-run`：核对真实输入 SHA、包内源码字节/验证证据、基线有效性、当前合同/模板及必销项 ID，生成待发送 prompt，不联网。它不预测模型格式，也不是科学内容检查；输出缺字段时先离线修接口，不继续全文送审。

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<包绝对路径>" --point <1|2|3> --inputs "<输入清单绝对路径>"
```

- **用 Git Bash 调用**：stdout 为一行 ASCII 转义 JSON，过程信息走 stderr（避免 PowerShell 5.1 按 GBK 误读 UTF-8）。
- **状态与退出码**：`decision` 为通过/待整改/不通过/未完成；`review_completed` 表示形成有效裁决，`gate_passed` 表示当前绑定版本通过。exit `0` = 通过，`3` = 待整改/不通过或轮次上限，`2` = 未完成；`--probe`/`--dry-run` 的 `0` 不产生通过裁决，dry-run 的 `gate_passed=false`。
- 脚本只追加结论段到包尾并复验正文前缀 SHA256；同包旁生成不可覆盖的 `<包名>.review.json` 作为证据回执，不是第二套状态板。
- 送审保护、台账分类、`--ledger-backfill`、扫描失败阻断、未知发送解除，见[独立复核](independent-review.md) §6 第 1、6 条。包只能从项目权威 `01-资料/reviews` 送审；同一项目/ID/点只允许一个进程发送。
- 三点均无默认审计模型；必须显式 `--model` 或配置 provider。通道仅为显式 Responses provider（URL 以 `/responses` 结尾，凭据只从 `api_key_env` 读取）；未配置返回 `provider_not_configured`，不取锁、不发请求；不可用时停下报告，不做直连兜底。连接/HTTP/流失败/缺终态与审查否决分开；每次发送最多一次 HTTP 请求；截断默认不重发，显式 `--retry-truncated-tokens` 才重试一次。
- 结论属审查意见，不是实证；通过不新增执行/登记/定稿授权。

模型只输出一个 JSON 对象，脚本验证结构及裁决一致性：

```json
{
  "decision": "通过",
  "summary": "当前绑定材料无阻断项。",
  "checklist": [{"item": 1, "status": "通过", "reason": ""}],
  "findings": [],
  "closed_findings": []
}
```

示例只演示一个必查项，实际必须覆盖所选复核点的全部编号。`status` 为通过/不适用/存疑；后两者须理由。`findings` 每项含 `id/kind/location/evidence/impact/change/acceptance`；`evidence` 可为非空字符串或含非空 `actual_evidence` 的对象。其余基础字段为非空字符串，`domain/verification_status/quantitative_claim/repair/test_case/patch` 为兼容扩展。`repair.patch` 为非空字符串；`repair.test_case` 接受非空字符串或非空 JSON 对象。必需字段与完整模板以 `--show-contract` 为准。`kind`：

- `blocking`：影响当前阶段有效性的具体缺陷，须定位原件/字段、证据、最小改法和可执行验收条件。
- `advice`：一般优化，不影响当前放行；不为建议数量硬找问题。
- `execution_gate`：属于未来执行阶段的门；不能藉此推迟当前硬门。

复核方交付：首轮一次列全当前阶段所有可证实阻断，`location/change/acceptance` 写清改哪里、改成什么、如何验收；不得用风格、措辞、可选优化或“可能还有漏洞”制造阻断（只能记 `advice`）。整改轮只审旧阻断、实际 diff 和受影响依赖；无关新问题只能记 `advice` 或留待新周期，除非新证据使旧结论失效，或触及数据泄漏、协议矛盾、缺原始证据等科学硬门。详见[复核全面性](independent-review.md#复核全面性与不得无据扩张)。通过不得有阻断或必查项存疑；待整改/不通过必须有具体阻断；关键材料不够或合同矛盾是未完成。

### 本地合同校验与故障恢复

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" --point 1 --show-contract
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --point 1 --validate-report "<原始报告JSON或旧回执>" --previous "<上一轮原报告或回执>"
```

首审省略 `--previous`。退出 0 仅表示 `schema_validated=true`，`gate_passed/review_completed` 始终 false；`report` 是可读原意见，不是新一轮复核。旧回执回放核对原包绑定；原件不可读或包漂移时拒绝。原文只套 JSON 围栏或 evidence 用对象时做本地无损归一；缺事实证据、验收条件、当前 ID 未回应仍拒绝。`report=null` 不得作为实质基线发送。格式失败、截断与首审 `repair.patch`/`command` 对象的恢复，见[独立复核](independent-review.md#首审报告的受控格式纠正)。

### 受控格式纠正旁证

只在明确授权的格式维护与额外请求预算内执行；格式失败本身不新增额度。先固定原回执 SHA，再运行：

```bash
python "$RS_SKILL_ROOT/scripts/review_format.py" \
  --receipt "<原包.md.review.json>" --expected-receipt-sha256 "<首次核对值>" \
  --output "<同目录新文件.format-correction.json>" \
  --responses-url "<原responses URL>" --model "<原model>" --dry-run
```

正式调用仅移除 `--dry-run`；仍固定 `xhigh`、cap ≤32768 和一次 HTTP。不发整包，只传已保存报告与合同。原判词必须为待整改/不通过/未完成；只修 coverage 状态/关联与无损 locator，不能删改问题、理由、原证、修复、缺证或数值。原件、规则未知、输入漂移、未闭合发送、缺终态、非 JSON 或纠正不合格均停止；marker 保留，不换文件或新周期重发。旁证仍不是正式通过；新增量包用 `--corrected-previous`，原 round0 不改，内存基线 effective round1。详见[独立复核](independent-review.md#首审报告的受控格式纠正)。

**人工兜底**（脚本 exit 2 且用户要求时）：

> 请读取文件 `<绝对路径>\01-资料\reviews\<文件名>.md`，按本复核点和上述 JSON 合同审查；不要修改科研原件。

人工意见仍须绑定原件并由主会话验证；不得手工把 failed 回执的 `gate_passed` 改成 true。

## 候选首审合同

在**隔离候选**输入清单中添加 `first_review`：

```json
{
  "claims": ["report.md 表2的 A 组成功率与 counts.json /cohorts/A 一致"],
  "editable_reports": ["<已在 files 绑定的绝对路径>/report.md"]
}
```

`claims` 是送审前从材料中提取的关键主张，用数组顺序编号。复核点 1/2 或无授权修改展示文件时，`editable_reports` 用空数组。合同以 `--show-contract --point … --inputs …` 为准：

- `checklist` 与 `claim_checks` 逐项给出非空 `reason` 与 `finding_ids`；缺证条目给精确 `missing_evidence`。
- 阻断必须能追到某条覆盖记录。`location` 写全受影响位置，`evidence` 对照实际与预期；`change` 给具体替换句、补丁或补证目标，`acceptance` 给验收条件。
- `closure.mode=recheck` 时给必须独立判断的 `reason`。`closure.mode=local` 仅适用于 `domain=wording` 的精确展示更正，`edits` 每条为 `path/before/after`，各原文在首审文件中独立、唯一且不重叠；替换后的片段也须唯一可定位。不能唯一定位时转需二次复核。例：`before="A 组成功率 96%"`，`after="A 组成功率 94%"`；报告须指出原件已确定读数 `47/50=94%`。
- 所有阻断均可本地验收时才显示 `local_verification_required`；任一须再判断则显示 `recheck_required`，输出 `recheck_ids` 与 `local_verification_ids`。缺证不当作待整改，不开启自动重审。

首次送审时独立留存命令输出的 `receipt_sha256`，不可事后从待核回执反算。主会话对照首审证据应用精确更正后，单独运行 `audit_dispatch.py --verify-local-edits <首审回执> --expected-receipt-sha256 <首次留存值>`：只读对比全文及其他受控文件，重复、遗漏、额外改动或哈希漂移均不放行。输出 `local_release_ready=true` 仅表示**隔离候选的本地验收**，`gate_passed=false`，原判词仍为待整改。调用方保留输出中的回执和文件摘要；此入口不读写台账、不发请求、不增加轮次。

## 四、整改、增量与停止

主会话收到意见后先逐项处理，不以转述建议结束。整改表必须有：

| 问题 ID | 处置及证据 | 原件实际改动 | 验证命令/结果/产物 | 已解决或待决 | verification_status |
|---|---|---|---|---|---|

授权内普通修正直接改原件并验证；有证据地质疑意见时明确理由，不能默默丢弃阻断项。科研目标、协议、预算等实质变化请求用户裁决，不因审计意见自行扩权。实现缺陷采用**同次修复交付**：复核方以 `domain=implementation` 标记，并返回 `repair` 补丁或具体不能安全修改的原因；主会话核对目标 SHA、路径与权限后直接应用可接受补丁、运行失败用例与受影响回归。审计脚本没有文件工具，不能假称复核方已改文件；落盘后的独立复核按任务模式办理，不由补丁作者自证。

增量包依次为：基线回执路径及摘要 → 整改表 → 实际 diff → 本地验证结果 → 变化及依赖所需证据片段；不再粘贴整份旧包或旧审查全文。输入清单沿用 `review_id`、更新真实 SHA：

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<增量包绝对路径>" --point <1|2|3> --inputs "<更新后的输入清单>" \
  --previous "<上一轮包名>.review.json"
```

第二轮 `closed_findings` 必须覆盖上一轮全部 `blocking` ID，每项有 `id/status/evidence`，`status` 只用已解决/未解决；未解决仍列为阻断。新增阻断须指出新证据或与修改的关联，不能借复核扩展目标。

开发者受控模式仅主审一轮、整改复核一轮；第二轮仍未关闭则 README 留清分歧，返回 `needs_user_decision`。自主/无人值守带 `--task-approval` 沿原 ID 累计整改复核，不另问新周期；有效非通过返回 `task_revise`，每次先核真实进展与预算。任何模式都不以未通过伪装放行。可继续的普通修复与被阻断的科研动作分开；用户选择停止、缩小主张或变更方案时分别记录。同一对象/点的包与回执放在项目权威 `01-资料/reviews`；不得移走证据、换包名或换 `review_id` 重置次数。台账按项目身份拦截备份目录、搬迁回执和同号项目副本；历史记录只用 `--ledger-backfill` 只读登记，不迁移、不改写旧回执。

### 用户决定的新周期（显式入口）

仅开发者受控模式；操作步骤、字段与脚本核对见[独立复核](independent-review.md#用户决定的新周期仅开发者受控模式)。要点：两轮按同一 `review_id` 计数；触发点为两轮结束或首轮明确否决；只有用户明确决定才可开新周期；新 ID 为根 ID + `__cN`；决定记录与上一周期最终回执须列入 `--inputs`；上一周期与新周期至少共享一个非回执受控原件；新周期从第 1 轮重新计数，不继承旧结论，不得无新决定连续派生 `__c3`。

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<新周期包>" --point <1|2|3> --inputs "<含决定记录与上周期回执的清单>" \
  --previous "<上周期最终回执>" --new-cycle "<决定记录>"
```

对象、依赖、包正文、复核点和规则完全不变时，脚本直接返回原回执并标 `reused=true`，不请求模型；任何绑定漂移都不能继承旧通过。复核点 2 的正式结果登记须通过；未通过时可记录失败/待复核事实，但不能登记为已通过。

## 五、有限验收与拆包

协议主稿只承担研究目标、对照、阈值/依据、seed、指标、停止门、预算和诚实边界。comparator、registry、marker 和 fixture 归实现附件，用“主张 → 相关源码 SHA → 用例 → 结果 → 影响面”矩阵连接。实现附件不是免审区：关系当前硬门的真实缺陷仍阻断其依赖项；不能以任一未来实现细节否决已自洽的协议。分栏不增加固定外审次数，也不把“几个测试通过”冒充所有实现可信。

对可执行实现主张，首次审查前冻结项目本地的 `acceptance_cases.json` 并绑定输入清单；至少给正例、拒绝例、边界和调用可达性。这是已有实现验收的规格，不要求每个纯文字润色建文件，也不授权本轮改动正在运行的科研项目。最小结构示例：

```json
{
  "scope": "comparator conforms to the declared scalar checkpoint input schema",
  "claim": "Declared unsupported role inputs are rejected, even when both arms are identical.",
  "cases": [{
    "id": "reject_empty_role",
    "input": {"a": {"checkpoint": {"path": [], "sha256": []}}, "b": {"checkpoint": {"path": [], "sha256": []}}},
    "call": {"target": "parent_loading.diff_single_variable", "kwargs": {"line": "F", "arm_a": "control", "arm_b": "treatment"}},
    "expected": "CONTROL_INVALID",
    "assertion": "The entry raises ControlInvalid; no control-valid report is returned.",
    "test": "tests/test_comparator.py::test_reject_empty_role"
  }]
}
```

实际文件须有目标函数所需的**全部**参数、调用前置/可信登记及可运行测试，不能把上述局部示例直接冒充项目验收。异常类型和期望判词从冻结实现/协议获取，不为凑用例改科学含义。有现成 Hypothesis 可用于边界生成；没有时先用参数化正/负/边界矩阵，不为此擅自装依赖、发起真实评估或延长运行预算。

实现 blocking 须给最小反例、可达调用、实际与期望差异，以及明确验收断言；无法复现时先检查输入域/调用前置，不把假设当已发生故障。静态上已能定位的必现错误不因尚未运行就丢弃；无法给具体反例或原件矛盾的纯加固设想记 advice。**不是“写不成失败测试就自动降级”**：数据泄漏、统计无效、缺原始证据、协议矛盾仍可凭原件核查阻断。

复核方应在相同回复内给最小失败用例；上下文足够时附仅限实现/测试的可应用 patch，不碰阈值、seed、预算或科学判词。主会话复现、应用或有证据地否定该 patch，回归原通过用例和反例，再按原验收销项。若要求证明“所有可能绕过都不存在”，须改写为本对象的可判定主张，或明确是无法满足的目标问题，而不是不断追加补丁。

新的可达反例可纠正覆盖缺口，但须关联原主张并登记为新用例；原条件已满足的旧项先关闭，不用同 ID 吞并无关新要求。绑定已通过的无变化材料在增量审查中可沿用其限域结论，注明未重新核实，不再凭记忆猜旧数字；源内容缺失影响本轮判断时只索取对应片段。
