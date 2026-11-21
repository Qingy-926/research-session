---
name: experiment-matrix-audit
description: Use when checking whether an executed or preregistered experiment matrix covers every declared method and random axis without changed assignments or selectively omitted runs.
---

# Experiment Matrix Audit · 矩阵核验

核对人群、样本和训练随机轴分别改变什么；根据实际角色/样本记录计算指纹，配对方法共享相同分配。列出 methods × 各随机轴的完整 run 数、实际运行状态、缺失和重复格。开发/探路不冒充正式结果。

从插件根目录调用 `python scripts/repro_governance.py verify-matrix --profile <profile.json>`。它核对现有 profile 和 matrix，不负责设计新的科学命题；方案设计使用 Research Judgment 的 `claim-driven-experiment-design`。读 [证据接口](../research-session/references/profile-and-evidence.md) 了解 matrix 字段；对 disputed 协议语义只阻断依赖它的判定。
