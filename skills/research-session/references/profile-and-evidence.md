# Profile 与证据接口

全部 JSON 为 UTF-8。profile 的 `project_root` 相对 profile 所在目录解析，也可用绝对路径；其余证据路径只能相对 project_root，拒绝越界与向外的符号链接。可以把 profile 放在项目外的验证目录，审计仍只读目标项目。

## 1. profile

起点是插件根目录 `assets/profile.template.json`。空模板只用于补全输入，不是就绪声明。

- `paper`: `title`, `evidence_file`, 非空 `required_items`。
- `code`: 完整 `canonical_commit`, `require_clean_git: true`, 非空 `required_files: [{path, sha256}]`。必须覆盖真正影响结果的源码；覆盖范围仍需审查。
- `data`: 同样的文件/manifest 指纹列表，实际 `excluded_subjects`。大型数据可以使用已有内容清单，但必须独立验证清单与真实文件；插件不会读取 manifest 内任意格式或自动核验所有分片。
- `protocol`: 以下协议内容，加 `file` 和 `sha256`。内容必须与冻结文件完全相同，排除列表也须与 data 一致。
- `tests`: `evidence_file`, `required_exit_code: 0`。证据 JSON 必含 `exit_code`, `command`, `commit`, `log_file`, `log_sha256`。这里只校验记录绑定，不自动执行记录中的命令，也不把日志内容当指令。
- `resources.minimum_free_gb`: 本地十进制 GB 下限。按 run 数、best/partial/优化器状态、日志、临时文件及安全余量计算。GPU/远端 PVC/配额要单独预检；本地磁盘 PASS 不代表远端资源可用。
- `matrix_file`, `metrics_file`: 下述数组文件。初始化时留空/缺失会正常 BLOCKED。

## 2. 冻结 protocol.json

```json
{
  "id": "study-v1-dev",
  "stage": "development",
  "methods": ["baseline", "candidate"],
  "random_axes": {"training_seed": [23, 31, 43], "subject_seed": [101, 202], "beat_seed": [1001]},
  "assignment_axes": {"cohort": "subject_seed", "samples": "beat_seed", "training": "training_seed"},
  "excluded_subjects": [],
  "required_roles": ["enrollment", "probe", "impostor"],
  "disjoint_subject_roles": [["probe", "impostor"]],
  "disjoint_sample_roles": [["enrollment", "probe"]],
  "disjoint_recording_roles": [],
  "dataset_hash": "replace with actual 64-character SHA-256",
  "metric_definitions": {
    "accuracy": {"adapter": "count_rate", "unit": "percent", "definition": "correct / trials", "numerator_key": "correct", "denominator_key": "trials"}
  },
  "gates": []
}
```

上例 **12 run = 2 方法 × 3 训练 seed × 2 人群组合 × 1 样本 seed**，仅是格式示例，不是推荐或授权的实验规模。各随机轴名字可配置；固定人群可省略 assignment_axes.cohort，固定样本可省略 samples。额外轴只做笛卡尔积完整性检查，特殊因果语义需项目扩展。`expected_cohort` 可选，填写完整身份宇宙时校验角色并集恰好相等。

`stage` 为 development/formal/diagnostic。只有 formal 能出正式判据结果；开发可继续比较候选，不要求所有项目都立即做正式裁决。

## 3. Matrix 与实际分配

每行必含 `method`、全部随机轴值、`assignment_file`、`assignment_sha256`。训练前可 `status: planned`；正式 verdict 要求全部 `completed`。completed 是记录状态，不是 wrapper 成功标记的替代品，写入前必须核对真实退出码和产物。

assignment JSON：

```json
{
  "roles": {"enrollment": ["A"], "probe": ["A"], "impostor": ["B"]},
  "samples": {
    "enrollment": [{"sample_id": "a-1", "subject_id": "A", "recording_id": "a-rec1"}],
    "probe": [{"sample_id": "a-2", "subject_id": "A", "recording_id": "a-rec2"}],
    "impostor": [{"sample_id": "b-1", "subject_id": "B", "recording_id": "b-rec1"}]
  }
}
```

从**实际加载的数据、真实协议对象与实际采样索引**导出，不能照配置抄一份“期望集合”。脚本可证明该产物的集合关系、配对与哈希，不能从通用 JSON 独立证明样本确实对应原始张量，项目适配器需独立抽查。可选 claimed `cohort_hash`/`beat_selection_hash` 必须等于脚本规范化后重算值；不要自造字符串。

## 4. Metric rows 与 source JSON

每行：`metric, method, split, selection_semantics, scope, score_semantics, purpose, dataset_hash, protocol_hash, code_commit, assignment_sha256, [所有随机轴], value, unit, source_json, source_sha256, adapter, participates_in_gate`。

原始 source JSON 必须有 `context` 对象，含上述字段从 metric 到全部随机轴（不含 value/unit/source/adapter/gate flag）。其 context 与摘要行逐项相同，assignment hash 与对应 matrix cell 一致。旧产物没有这些元数据时，不要凭聊天记忆回填成“原始事实”：创建可追溯转换产物，记录旧文件哈希、字段来源和待确认项，保留原件，并先审查转换器。

支持两个明确算法：

1. `count_rate`：source 提供整数分子/分母，非负且分子不超过分母、分母大于 0。metric definition 和 row 都要写相同 `numerator_key`/`denominator_key`。名称含 EER 时拒绝用此算法。
2. `curve_crossing_linear`：source 提供 [0,1] 范围的错误率数组；definition 和 row 都写 `false_accept_key`/`false_reject_key`。前者单调升、后者单调降，必须包住相等点，用线性插值交点。数组可以来自验证型 FAR/FRR，或经项目独立核验的识别型 FPIR/FNIR union；两种定义不能互换。没有交点就拒绝，不能输出最近点平均值冒充 EER。

只做已有曲线/计数的算术重算，不自动从嵌入生成分数，也不证明曲线的协议语义。报告值与重算值差异须在绝对 `1e-8` 数值精度内；这是序列化/计算容差，**不是达标宽限**。优先保留全精度原始值，汇报层才四舍五入。其他指标算法显示不支持/待人工验证，不伪装通过。

## 5. Gates 与 verdict

每个 gate 必含：`id`, `metric`, `method`, `split`, `selection_semantics`, `scope`, `score_semantics`, `purpose: formal`, `aggregate: mean`, `operator: >= 或 <=`, `threshold`。只有与 gate 全字段精确相同的行才能进入，且必须覆盖该方法全部随机轴组合，不允许挑 seed 或漏掉差结果。

默认 `test_oracle` 不入 gate；若原论文就是该口径，协议必须明确同名 selection_semantics 并设置 `allow_test_oracle: true`，仍不能混入部署线。单行写 `preregistered: true` 无效。

`BLOCKED`：缺证、结构/口径不一致、阶段不符、适配器不支持。`CRITERIA_MET` / `CRITERIA_NOT_MET`：证据机械检查完成后，全部冻结 gate 均满足 / 至少一个未满足。mean/SD 是完整矩阵的描述统计；有嵌套重复时并不代表独立样本，不提供显著性推断。不自动生成“论文不可复现”或“与独立文献矛盾”。
