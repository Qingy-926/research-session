---
name: research-code-audit
description: Use when investigating how research code, preprocessing, protocol generation, training, selection, and evaluation jointly affect a claimed result.
---

# Research Code Audit · 科研代码因果链

沿实际入口追溯数据与预处理 → 协议/随机分配 → 训练/选模 → 评分/评估 → 持久化/汇总。每项问题写首次出错位置、可复现症状、对指标的影响、最小回归用例；测试要有独立手算或权威锚点，不能只复用同一错误函数。区分权威 commit、dirty patch 和实际执行文件。

方法见 [实验与代码审查](../research-session/references/experiment-and-code-review.md)。`snapshot-code --profile ... --output <新清单>` 仅为哈希清单，不是代码备份。遇到代码/CLI 报错按本机 §7.1 先分析并给用户修复选择，不借本技能自动扩大重构范围。
