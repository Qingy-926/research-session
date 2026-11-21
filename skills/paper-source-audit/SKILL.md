---
name: paper-source-audit
description: Use when aligning a paper's original method and metric definitions with an implementation, or when a claimed reproduction lacks locatable primary-source evidence.
---

# Paper Source Audit · 论文核源

从论文正文、附录、官方代码及保留的可定位原文提取数据排除、切分、gallery、损失、选择规则、评分、阈值和指标定义；逐项对照实现文件及位置。对失效链接和二手总结写“来源未定位”，不猜测。保留文本中找到引文仅证明字面存在，权威性和语义仍需人工审阅。

从插件根目录调用 `python scripts/repro_governance.py audit-paper --profile <profile.json>`。profile 格式见 [证据接口](../research-session/references/profile-and-evidence.md)。结果需指出核验级别、原文/代码 SHA-256 和尚未确定的解释，不把 `review_status` 误写成系统独立证明。无 profile 时先列所需证据，不伪造 PASS。
