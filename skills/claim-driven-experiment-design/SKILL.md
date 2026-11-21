---
name: claim-driven-experiment-design
description: Use when converting research claims or reviewer objections into discriminating experiments, ablations, controls, resource budgets, and stopping rules.
---

# Claim Driven Experiment Design · 主张驱动实验

先列核心命题及至少一个竞争解释，随后为每项命题设计能把两者分开的最小实验。每行包含：命题、替代解释、单变量对照、数据/协议/选模规则、评价指标、随机轴、完整 run 数、资源、停止条件、阳性和阴性分别意味着什么。关键消融必须与具体机制相连；阴性结果不能通过改变阈值或补看 test 自动转正。

先开发集探索，正式方案冻结后再运行；复用项目 `research-session` 的预注册与批准流程，不自行新建平行状态板。实施后由 `experiment-matrix-audit` 核验实际矩阵，由 `metric-verdict-audit` 核验结果口径。此技能提出设计，不宣布实验已执行或获得授权。涉及 GPU、磁盘和并行任务按现行协作约定安排。

输出按信息增益排序的主张—实验表，最后明确最先运行的一项及失败分支。
