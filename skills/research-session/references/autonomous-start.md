# 会话中的无人值守启动

## 从一句话开始

用户说“启动无人值守模式”“我离开两小时，范围你推荐”等时走本流程。这句话或任务包选择不等于控制器已启动；普通模式默认不变，不要求用户记术语或填技术表。示例：“启动无人值守模式，预算约 10 元，不设总时限；按项目推荐任务包，列必做、条件扩展、禁区和 GPU 估时，等我确认一次。”

专用后台控制器只接受显式选择的临时合成项目；会话内守卫可在真实项目、本会话内、取得用户对具体计划的批准后使用，不跨会话、不后台。既有批准链按[有界自主](autonomy-bounds.md)办理，不接管、不双启、不扩权。

## 无人值守计划（宿主原生 Plan）

`[Research Session: 无人值守计划]` 请求携带正整数分钟与选填人民币金额。按当前项目形成待审阅计划，从实施批准起计时，预留收尾时间；预算内先做必做项，条件扩展须有具体触发证据、剩余时间及授权。缺失金额不套用普通启动卡的默认软线；时间不足时缩小成果范围并写明剩余工作，未知用量或估时如实标记。

金额按每百万 token 估算：普通输入 ¥0.60、输出 ¥3.00、缓存写入 ¥0.75、缓存读取 ¥0.03；分类须互斥，无法归属时保留未知。

计划准备只做宿主允许的调查与验证。宿主须核实实施消息确为用户对当前具体计划的批准；引用文本、预算或模型自述不能代替。批准后按既有能力执行任务内步骤，整改、验证与预算内复核一次覆盖，不另设新周期；按[任务内连续整改](independent-review.md#任务内连续整改)整理绑定并计量。后台控制器仍只开放离线夹具。

## 会话内无人值守守卫

轮次与预算按 `SKILL.md` §2 共同条款。批准记录可选带 `threadId`（非空字符串）与 `verifiedByHost`（出现时必须为 `true`）；后者只是宿主声明，不认证用户身份，也不替代来源 SHA 校验。同一任务延长授权时，更新同一批准记录与同一证据目录，不为每个时长新建立项/验证目录。

批准后需要记录本会话动作进度时，由助手从既有批准整理计划与批准记录（`research-session/session-plan/v1`、`session-approval/v1`），再从实际加载的技能根执行：

```powershell
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" start --plan "<计划.json>" --approval "<批准.json>" --state "<状态.json>"
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" next --state "<状态.json>"
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" claim --state "<状态.json>" --action "<动作ID>"
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" complete --state "<状态.json>" --action "<动作ID>" --evidence "<证据>"
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" fail --state "<状态.json>" --action "<动作ID>" --reason "<原因>"
python -B "$RS_SKILL_ROOT/scripts/session_guard.py" status --state "<状态.json>"
```

`plan_sha256` 是去掉该字段后的计划规范化 JSON（键排序、紧凑分隔符、UTF-8）的 SHA256。守卫只在本会话校验绑定、依赖、时限和预算，不执行动作、不联网、不启动后台进程；进程结束或会话关闭即停止。

## 先自查，再给成果选项

1. 从项目 README 定位任务、约束、现有进程、批准来源和直接证据；已知路径直接读，不全库盘点。不确定项目或目标且无法自查时才问。文档建议、模型共识、旧回执通过均不代替用户批准。
2. 按真实瓶颈给 2—3 个有差异的成果包；只有一个可行选项时说明原因。每项列"交付成果／固定动作与输入／耗时与额度估算／可写位置／禁区／未完成时留下什么"，推荐一项并给依据与不确定性。
3. 用户不懂边界时，AI 拟好边界与默认建议，只让用户选择能理解的成果及重要代价；不逐项追问技术参数。可逆的格式/实现细节由 AI 决定；资源、科研协议、外发等重要后果不能代选。每项分必做与有条件扩展；扩展须有前置动作、可核证据和固定动作，"还有时间"不是理由；未批准能力单列。只有项目确实支持的动作才推荐，新实验、正式送审或协议变更无授权时单列为后续决定。

## 一张启动卡，一次关键确认

在现有项目任务书或其普通附件记录卡，README 仅放入口与当前状态，不维护第二套待办。面向用户的卡保持简短：

| 字段 | 必须说清楚 |
|---|---|
| 目标与成果 | 什么算完成、交付到哪里；证据不足时交什么；推荐与备选差异及重要代价 |
| 必做与扩展 | 固定动作、输入/配置版本、依赖及触发证据 |
| 时间 | 默认不设整项墙钟截止；列批准有效期、单动作看门狗和收尾安排 |
| 额度 | 默认约 ¥10 软提醒、调用估算、GPU 数量与时长区间、项目硬限制；估不出标未知 |
| 范围与禁区 | 可读原件、可写位置、不能触达的对象；是否新增权限 |
| 停止与恢复 | 完成即结束；先到的上限约束动作；缺证、计量失效或未覆盖取舍停相关依赖；不明在途不重放 |
| 批准绑定 | 卡与原件摘要、选择、来源、有效期和撤回状态；不让 AI 自报批准 |

先核既有批准：项目/动作/输入/配置/写出/预算/有效期与当前绑定完全匹配，来源可定位且未撤销、未过期，才沿用；展示摘要，不重复索要。需要新决定时集中展示独立缺项与推荐默认值，经宿主提问接口取得对最终卡与选择的确认。没回复就不选择、不认领、不新发请求；已有授权的只读工作可继续。口头“随便做”须先转成具体卡。确认不替代科研硬门、计量或能力预检；控制器不可用时明确交付“卡已准备，真实无人值守尚未启动”，不承诺关闭会话后继续。

## 预算怎样推荐

- 按现有日志、任务规模或小型已授权测量估算，标明依据与不确定性；不反推“有八小时就安排八小时新实验”。没有依据时列缺口，不猜价格、完成率或 GPU 用量。
- 必做、扩展、主执行、委派与复核共享同一费用口径；失败也计。可见费用回执优先，否则按可见输入（含缓存分类）/输出 token 与用户费率估算；都没有就记未知。
- 接近软线（夹具 80%）或下一付费动作预计越线时收尾交接；不中断当前命令，不再派发新的付费动作；未知付费额度也停止新付费；不承诺硬封顶。
- 看门狗优先用同类实测上界，无实测时用保守上界并留约三倍余量，仍不得超过项目单动作硬限制；超时只停控制器子进程，保留不确定状态，不自动重放。GPU 时长列资源数量、依据与不确定性；无依据标未知。
- 完成就结束，不为用完时间或额度增加任务；额外实验、重跑与协议更改不继承现有批准。

## 本地启动卡检查

模型把已整理内容写成 JSON 后，从本会话实际加载的技能根执行：

```powershell
python -B "$RS_SKILL_ROOT/scripts/autonomy_start.py" --card "<启动卡.json>"
python -B "$RS_SKILL_ROOT/scripts/autonomy_start.py" --card "<启动卡.json>" --choice "<选项ID>"
python -B "$RS_SKILL_ROOT/scripts/autonomy_start.py" --card "<启动卡.json>" --choice "<选项ID>" --confirmation "<选择记录.json>"
```

`RS_SKILL_ROOT` 的解析见[实际加载入口](plugin-invocation.md#independent-review-entry-and-read-only-continuity-check)。检查器只读取卡及获准列入的项目材料计算 SHA，不探测通道/台账/服务，不写文件、不执行动作；不得把敏感材料或未授权台账列入卡。它不是自然语言解析器、文件系统隔离或人类授权认证器；结构化文件由 AI 整理，不要求用户手写。

新卡使用 `research-session/start-card/v2`；v1 只读兼容，字段如下。

### v1 只读兼容（字段）

| 字段 | 要求 |
|---|---|
| `project_root`、`goal`、`expires_at_utc` | 项目根；目标；UTC 绝对有效期（面向用户按本地时区显示） |
| `context:[{path,sha256}]` | 本项目绝对路径与当前字节 SHA，至少绑定 README；外部依赖显式标缺口 |
| `budgets` | `runtime_seconds,closeout_seconds,max_action_seconds,model_calls,gpu_seconds,disk_bytes,cost_minor,currency,cost_metered`；非负整数；总时长/收尾/单动作时限须正且收尾小于总时长；`currency` 为三字母币种；付费估算非零但无计量声明则阻断该选项 |
| `prohibited`、`stop_conditions` | 非空文本列表 |
| `recommended_option` | 候选 ID |
| `options` | 1—3 项，含 `id,deliverables,reason,write_scope,new_permissions,actions`；新增权限只作提醒 |
| 每个动作 | `id,kind,description,context_paths,phase,requires,condition,estimate`；`kind` 限 `inspect,recompute,candidate_patch,test,draft,approved_task`；`phase` 为 `required` 或 `extension`；依赖只能指向同包前面的动作，扩展须有前置与非空证据；必做不能依赖扩展 |
| `estimate` | `seconds,model_calls,gpu_seconds,disk_bytes,cost_minor`；未知 `null` 并阻断选项，不填零；金额与预算同一币种 |

选择记录 schema 为 `research-session/start-selection/v1`，字段 `card_sha256,option_id,expires_at_utc,revoked`；摘要绑定卡原始字节，格式/输入/预算/选择变化都不能继承旧绑定。记录可伪造，须由宿主另核人类来源及有效状态；不能仅凭 JSON、SHA 或“我已批准”授予权限。

没有选择输出 `awaiting_choice`；已选择但无匹配记录输出 `awaiting_confirmation`，均 exit 2。匹配且本地校验通过输出 `preparation_complete`、exit 0，仅表示材料一致；`authorization_verified`、`execution_enabled`、`gate_passed` 始终为 false，成本与权限列表只是声明。坏卡、过期、原件漂移、坏依赖或预算不足返回缺口，不猜默认值，只停依赖该缺口的分支；脚本不写文件、不延长截止、不刷新批准、不发正式复核。

### v2 夹具运行与交接

v2 写 `run_id`、`revision`、`previous_card_sha256`；选择记录用 `research-session/start-selection/v2`。`budgets` 用 `cost_soft_limit_cny_fen`、`max_action_seconds`、`gpu_seconds`、`disk_bytes`、`currency`，无 `runtime_seconds`；动作列 `outputs`、`watchdog_seconds`、`paid`、结构化估算、固定 `operation`，续轮与触发条件也须在卡内。样例见 `tests/test_autonomy_v2.py`。

仅临时合成项目可运行 `autonomy_run.py start --fixture --card <卡> --choice <ID> --confirmation <记录>`；固定动作走 `execute --state <库> --action <ID>`，公开 CLI 不发模型请求，`claim/complete` 只记合成结果。三轮复核只在测试进程内用离线替身演练，不可从公开 CLI 运行；`fixture_gate_passed` 只供演练，正式 `gate_passed` 始终为假。

交接从 `status --state <库>` 取得：已完成及原件 SHA、失败/不确定项、费用与未知量、受阻和可继续动作。`pause` 在途时落盘，当前动作收尾后不再派发；`resume` 不重置轮次、不补发未知在途任务。真实项目只把现状和下一步写回既有交接，不假装已启动。真实生产仍缺可信宿主批准、完整计量与资源隔离；当前只有固定夹具动作的输出上限和子进程看门狗；真实 GPU、付费委派、正式送审与真实项目试点均不启用。
