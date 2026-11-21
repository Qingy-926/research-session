---
name: novelty-and-baseline-review
description: Use when checking whether a claimed research gap is genuinely supported by nearby literature or whether baselines and comparison conditions are fair.
---

# Novelty and Baseline Review · 查新与对照

检索近期最近邻与奠基工作：首选原论文、附录、官方代码及正式元数据；记录检索词、日期、来源、版本和覆盖范围。找不到不等于不存在。逐篇写研究问题、数据/协议、核心方法、可验证差异和可复用 baseline。外部论文的结果不得在不同 split、gallery、test/oracle 和样本口径下直接并列。

按[检索与正文证据](../research-session/references/retrieval-evidence.md)围绕缺证迭代，追溯核心工作和反例；按[主张与证据](../research-session/references/claim-evidence.md)核对主张、直接支持与比较条件。指标优势不自动支持新颖性、机制或迁移；预算结束仍缺证时收窄判断。

针对每个候选 baseline 检查：是否对应同一任务和威胁模型、能否按共同数据与预算重新运行、实现/超参/调参集是否公平、是否应有更简单的强对照。输出最近邻差异表与 baseline 选择表，分别标已核验、仅摘要、待查。没有原文/联网能力时列精确待查项，不能说‘新颖性已证实’。

本技能评估外部比较；原始项目矩阵和指标重算分别交 `experiment-matrix-audit`、`metric-verdict-audit`，从 `research-session` 的复现阶梯进入。
