# 包保真、证据状态与修复交付

本文件只定义技能的离线质量检查和修复责任，不授权恢复暂停项目、改科学设定或发起外审。
检查证明声明的字节/版本/日志相符，不证明输入域覆盖完整、命令可信或科研结论通过。

## 原文从文件生成

凡称为全文、原文、实际源码或测试的代码块，必须使用 `scripts/review_evidence.py` 生成，禁止手写后冒充。
生成块含 `research-source` 元数据：原件绝对路径、完整SHA、全文/行范围和字节数；全文逐字节一致，
片段等于指定源行的原始字节。保留CRLF及末尾换行，不经转义模板再次format；文件须列在输入清单 `files`。

```bash
python "$RS_SKILL_ROOT/scripts/review_evidence.py" \
  --source "<源码绝对路径>" --lines 10 35 --output "<新的片段md路径>"
```

省略 `--lines` 为全文；`--output` 二进制写UTF-8、不覆盖已有文件，避免PowerShell重定向改编码/行尾。
组包器应直接调用 `render_source()` 并写原始UTF-8，不手写源码。叙述中的行内代码及自然语言主张无法靠字节检查证明，仍须人工核对。
非原文的调用示例、命令、JSON格式样例须在代码围栏紧前一行写 `<!-- research-example -->`；这是明确的非实证示例，
不能当源码、测试已运行或原始结果。未标明来源/示例的围栏在送审前拒绝，不请求模型纠正这些机械错误。

```bash
python "$RS_SKILL_ROOT/scripts/audit_dispatch.py" \
  --pack "<新包>" --inputs "<清单>" --lint-pack
```

该操作不需要复核点、不访问代理、永远 `gate_passed=false`。正常送审和dry-run执行同一检查；旧包/回执不改写，
离线回放旧意见仍走 `--validate-report`。规则变化不能继承旧通过，也不授权自动重开已停的审查周期。

## 运行证据

数字化pytest结果必须绑定 `verification.json`，不再只给日志路径或手抄摘要。
在既有授权内运行验证时，用下面的显式命令记录器；lint/送审不会执行记录或包内提供的命令。

```bash
python "$RS_SKILL_ROOT/scripts/review_evidence.py" \
  --verify "<新的verification.json>" --input "<实现文件>" --input "<测试文件>" \
  --cwd "<测试目录>" -- python -m pytest tests -q
```

`--input` 重复列出该运行所需的全部行为依赖，包括相关实现、测试、配置；不能用只绑定一个无关文件冒充完整运行版本。
记录含显式argv/cwd、带时区起止时间、exit、运行前后输入SHA、日志路径/SHA和从日志提取的pytest摘要。
同名日志/记录禁止覆盖。它不是任意旧日志的“盖章器”：没有当时输入/命令证据的历史运行标未核实，不能补造成功记录。

输入清单的 `files` 同时绑定上述输入、verification文件及日志；另加：

```json
{"verifications": ["verification.json"]}
```

`verifications` 路径相对输入清单，记录内路径用绝对路径。校验当前输入SHA、运行前后SHA一致、起止时间和日志SHA；
输入mtime晚于运行开始则证据过期，重新运行而不是touch旧证据。mtime只是附加条件，不代替内容哈希。
exit=0与失败摘要冲突拒绝；正文pytest数字须与某条已绑定运行摘要相符，不同命令结果分行并标明范围。
同一摘要行出现“184 passed”和“183 passed”直接判材料未完成；真实失败日志可作为失败证据提交，不强求造绿。
自动摘要检查覆盖pytest数字，其他工具或中文自然语言结果仍须给原始字段并由主会话核对，不宣称通用矛盾检测。

## 状态与争议

输入清单可附 `timeline`、`closures`、`disputes`，会进入绑定和提示，不再静默丢弃。
每条时间线/我方销项必须有 `id/evidence/verification_status`；时间线另有 `event/timestamp`：

- `verified_local`：本地已核对，附具体证据；不表示复核方已重新核对。
- `verified_external`：有可绑定的外部核验记录；不表示本地已复现。
- `unverified`：有说法或记录但未核实，不得推定为真。
- `absent`：从未采集/确无记录，时间写null，不虚构历史。

模型的 `closed_findings` 也填此字段。旧格式缺字段只归为unverified，不追改旧回执或销项裁决。
状态本身不决定科学有效性：泄漏、协议矛盾、缺原始证据仍按当前硬门处理；非必要过程记录缺失时明确限制主张，
不把历史没采集变成永远补不齐的任务。例如无法核验盲态就不能称盲注册，不要求补造设计者历史接触时间。

第二轮允许在输入清单给定量异议；只引用上一轮当前blocking ID：

```json
{"disputes": [{"id": "D1", "finding_id": "B02", "claim": "表有142条记录、141个唯一路径，并非140条",
  "reproduction": {"artifact": "<已绑定原件绝对路径>", "command": ["<复算工具>", "<参数>"],
                   "observed": 142, "expected": 142}}]}
```

也可用 `json_pointer`（RFC6901，根为 `""`）替代command；复杂计数还须说明统计口径/分母，pointer只定位数据，不自动证明计数。
复核方的新定量断言在对应finding给 `quantitative_claim`，结构同reproduction；lint只验定位结构，不执行命令。
同轮 `dispute_resolutions` 逐项给 `id/status/evidence/verification_status/reproduction`，状态accepted/rejected/unresolved。
未决不能自动通过，接受计数异议也不自动关闭该finding的其他缺陷，不为异议开第三轮。
缺结构字段是合同未完成，先本地查错；不把格式失败当科学否决，也不能由harness代替模型自动降级真实阻断。

## 修复优先

新意见用 `domain=implementation/scientific/evidence/wording` 区分问题。实现blocking同次给 `repair`，上下文足够时必须给补丁：

```json
{"status": "patch_proposed", "reason": "在比较差异前拒绝角色级列表",
 "targets": [{"path": "<已绑定实现/测试路径>", "sha256": "<当前原件SHA>"}],
 "patch": "<可应用的最小diff>", "test_case": "<完整最小输入、真实调用前置、实际/期望与断言>"}
```

没法安全给补丁时用 `needs_context`（指出缺失函数/行/字段）、`needs_decision`（具体科学/权限取舍）或
`not_safely_patchable`（具体原因），不要重复“修改后再来”。纯数据缺失、科学设计矛盾不要求伪造代码修复。
当前脚本只把补丁存回执并用 `proposed_repair_ids` 指出，不运行patch、不赋予外部模型写权限。

主会话检查目标SHA与实际diff、当前授权、允许路径及依赖；接受时直接用本端编辑工具落盘到权威实现/测试，
运行反例和受影响回归、生成新验证记录。不得让模型补丁改阈值、seed、数据、预算、科学判词、共享配置或冻结证据；
新测试文件须检查父目录/命名及授权，不能借new-file绕过范围检查。证据不足只补对应片段，不重发全历史。
有文件工具且已有相应授权的独立修复任务可在限定路径直接编辑；这属于修复交付，不自动成为对自己补丁的独立验收。

## 未通过的终点

首轮待整改先直接修复/验证，最多一次增量复核；明确否决停依赖动作和自动重送，仍可完成已授权普通修复。
第二轮仍未通过标 `needs_user_decision`，交付未解决项、已做修复及验证、科学影响和最小决策，不默认继续追求放行。
用户可停止、缩小主张或明确另开周期；不能把某种处置写成“外审已通过”。暂停要求优先，不重开项目或监测。

输出cap由 `--max-output-tokens` 明确；保留12000缺省不擅增预算。可显式加 `--retry-truncated-tokens <更高cap>`，
仅当上游reason=max_output_tokens时再试一次；每次失败留attempt回执、不改包正文、不算实质轮次，不降档或换模型。
同一发送仍受原有最多4次HTTP重试影响，dry-run展示此上限；两种重试都不是无限调用授权。cap是上限，不是实耗。
