---
name: metric-verdict-audit
description: Use when recomputing reported research metrics or judging a complete frozen experiment against preregistered gates and source JSON.
---

# Metric Verdict Audit · 指标与裁决

从原始结果重算支持的计数率或曲线交点，并核验每条结果的 dataset/protocol/code、实际分配、split、full/fence、union/intersection、oracle 和 seed 口径。默认 test-oracle 不进正式 gate；只有冻结协议明确允许且上下文一致才考虑。平均值判据仅在完整冻结矩阵上成立；标准差是描述量，不自动成为显著性证明。

从插件根目录先调用 `python scripts/repro_governance.py audit-metrics --profile <profile.json>`，证据齐备后用 `verdict`。退出码 0 表示检查通过，`CRITERIA_NOT_MET` 仍可能是有效阴性结果；退出码 2 查看 failures。检查器不支持的指标或缺证应 BLOCKED。输入见 [证据接口](../research-session/references/profile-and-evidence.md)。
