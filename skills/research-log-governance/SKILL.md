---
name: research-log-governance
description: Use when auditing, summarizing, encoding-repairing, or safely archiving research logs and code snapshots without losing original evidence.
---

# Research Log Governance · 研究日志治理

默认使用只读 `python scripts/repro_governance.py audit-logs --profile <日志配置>` 或 `archive` 生成计划；旧 `log_governance.py audit/summarize` 会写文件，不能标为只读。真归档或 rewrite 前核验进程、范围、原件 SHA-256、目标冲突和回滚路径；`--apply` 不是授权。远端训练需另外检查完成 marker；无法检查进程不等于没有活动任务。

完整命令和保护清单见 [日志治理兼容说明](../research-session/references/log-governance.md)。摘要提取出的数仅是索引，正式指标必须回原始 JSON 核对。不能通过日志工具删除 checkpoint、模型或受保护配置。
