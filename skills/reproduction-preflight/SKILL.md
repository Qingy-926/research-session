---
name: reproduction-preflight
description: Use before a paper reproduction run to verify the actual code, data, protocol, tests, exclusions, and local resource evidence.
---

# Reproduction Preflight · 训练前门控

先读项目权威状态和冻结协议，核对实际读取的数据路径、manifest 内容指纹、代码 commit 和 dirty diff、环境及测试证据。检查身份/录音交集、排除集合、gallery、seed、损失及指标定义。不能把本地磁盘通过当成远端 GPU 或 PVC 可用；外部训练器也不会被插件强制拦截。

从插件根目录调用 `python scripts/repro_governance.py preflight --profile <profile.json>`；需只看整体状态时使用 `status`。初始化 `init --profile <新文件> --project-root <项目>` 会创建空模板，缺证应为 BLOCKED。冻结需 `freeze-protocol --profile ... --output <新路径>` 并记录哈希，不能覆盖或把冻结当成执行授权。详见 [证据接口](../research-session/references/profile-and-evidence.md)。
