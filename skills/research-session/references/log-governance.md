# 日志治理兼容说明

原有 `encoding_utils.py`、`summary_extractors.py`、`utf8_process_runner.py` 保留；`log_governance.py` 保留旧动作和配置，并收紧安全边界。

- `status`/内部 audit 函数读源文件；旧 CLI `audit` 写项目审计 JSON/Markdown，`summarize` 在原目录生成摘要；它们不是完全零写入命令。只读诉求请用新入口 `audit-logs`（默认 stdout）。
- `archive` 默认 dry-run；新入口 archive 仅计划，真归档用旧入口 `archive --apply`，先按用户授权确认范围。
- 缺少进程匹配范围或无法检查进程时拒绝认定无活动；存在活动时拒绝 rewrite 和 archive --apply。检查只覆盖当前主机；远端训练还须读取远端完成 marker。
- 重写仅针对当前文本日志，保留原始字节与 SHA-256。历史原件不重写；无法可靠恢复的字符标记，不猜测数字或实验结论。
- 模型、JSON、数据数组、配置扩展名具有不可被 profile 取消的保护下限；日志 profile 可增加 protected_globs。重要文本协议/源脚本也必须显式保护。不能把整个项目当“可归档日志目录”。
- 归档先检查完整目标清单是否存在冲突；再记录带 SHA-256 的计划清单，才移动。文件系统移动不是原子事务；中途系统故障时按清单和实际源/目标哈希恢复，不宣称已全部完成。不要删除已有目标强行继续。
- 不通过插件删除实验、剥离 optimizer、设置 Windows ReadOnly、杀训练或改模型/代理配置。
- `utf8_process_runner.py` 是同步输出辅助模块，不是长任务监控系统；长任务按项目 wrapper/完成 marker/超时轮询规则执行。
- 摘要正则抽出的数字只作索引，不具有正式指标资格；必须经原始数据和口径核对。
