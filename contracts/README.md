# 双端契约 v1

本目录是 research-session 5.0.0 的最小版本化契约，Trae 与 Codex 共用同一份文件；测试只依赖标准库，不引入 JSON Schema 库或遥测服务。

- `provider-adapter.v1.json`：provider 配置允许的键只有 `url`、`model`、`provider`、`api_key_env`，与 `audit_dispatch.py` 的 `_provider_config` 严格一致。`api_key_env` 只写环境变量名，任何密钥值都不得写入配置、日志或回执。解析后的路由把 `wire_model` 视为 `model` 后仍应符合该 schema。
- `run-manifest.v1.json`：每次 CLI 运行的脱敏 JSON 清单。只有 `status=gate_passed` 表示复核门通过；`completed` 仅表示进程正常结束（含 dry-run、只读查询），不是科学通过或授权。默认关闭：设置 `RESEARCH_SESSION_RUN_MANIFEST_DIR` 后，`audit_dispatch.py`、`review_preflight.py`、`autonomy_run.py` 退出时各写一份 `<run_id>.json`，不覆盖既有文件；未设置则不产生文件。
- `recovery.v1.json`：暂停/恢复与错误语义的稳定字符串，逐项对应脚本真实输出；新增状态必须先改脚本再加枚举。

## 版本策略

2026-11-22，6.1.0：`recovery.v1.json`新增可选出口`task_revise`，用于已批准任务内连续整改；受控模式已有状态保持原义。新任务绑定和CLI用法见[独立复核](../skills/research-session/references/independent-review.md#任务内连续整改)。

契约版本固定为 `v1`，文件名带主版本号。稳定接口是上述三个文件的字段名、枚举值与必填集；新增可选字段不升主版本，删除或改义字段、改变枚举语义必须新建 `*.v2.json` 并保留 v1 至少一个发布周期。变更与弃用在本文件登记一行：日期、版本、字段、原因、迁移方式；已弃用字段在下一主版本移除，旧清单只读保留，不就地改写。
