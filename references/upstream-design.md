# 上游设计来源、版本与许可（2026-09-26）

本插件的 Skill 文字和实现由本项目独立撰写；下列材料只用于核对设计方向，未复制其代码或 SKILL.md 正文。固定版本的原始文件、许可证及 SHA-256 见项目 `01-资料/20260926-upstream-reference/manifest.json`。未来复制、翻译或改编上游内容时先按相应许可证审查，尤其注意 Supervisor-Skills 的非商业与相同方式共享条件。当前本插件不自称继承或获得上游项目授权。

| 来源 | 本次核对的提交与许可 | 借鉴点及边界 |
|---|---|---|
| HKUSTDial/Supervisor-Skills | `207bc6f7a1aa107e544099c2c7cc86816fba9628`，CC BY-NC-SA 4.0；LICENSE 标明 Yuyu Luo（methodology）及贡献者 | 参考 `idea-evaluator` 的证据层级、贡献与证据对齐，`intro-drafter` 的问题动机与论证检查；不复制正文、不用自动评分替代研究裁决，不强制固定段数。 |
| HaoYangJin/Research-workflow-Skill | `b089a37bfd9143426bf00f5cdf7eeff78db73568`，MIT，Copyright 2026 JinhAo Yang | 参考导师、审稿人和混合视角的任务区分；这里保留独立技能入口。 |
| wanshuiyin/Auto-claude-code-research-in-sleep | `341f914024d270dc5c8fa51337d1ad38829273aa`，MIT，Copyright 2026 wanshuiyin | 参考 `research-refine` 的研究问题锚定、方法精炼与主张驱动实验；不引入睡眠期间无人值守链、指定模型、多智能体循环或评分门槛。 |

官方 Skill 设计说明：`https://developers.openai.com/plugins/build/skills` 与 `https://developers.openai.com/plugins/concepts/skills`。按目标、输入和成功标准拆分；发现由元数据引导，是否在当前会话加载需实际核验。