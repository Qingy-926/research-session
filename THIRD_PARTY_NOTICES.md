# Third-Party Notices

research-session is licensed under Apache-2.0 (see [LICENSE](LICENSE)). This file records third-party material that was **consulted for design orientation only**. No upstream code, SKILL.md text, or prompt text was copied into this repository.

## Referenced projects (design review only)

| Project | Pinned commit | License | How it was used |
|---|---|---|---|
| [HKUSTDial/Supervisor-Skills](https://github.com/HKUSTDial/Supervisor-Skills) | `207bc6f7a1aa107e544099c2c7cc86816fba9628` | CC BY-NC-SA 4.0 | Reviewed `idea-evaluator` evidence levels and `intro-drafter` argument checks. No text copied; no automatic scoring replaces research judgment. |
| [HaoYangJin/Research-workflow-Skill](https://github.com/HaoYangJin/Research-workflow-Skill) | `b089a37bfd9143426bf00f5cdf7eeff78db73568` | MIT | Reviewed the separation of mentor, reviewer, and mixed perspectives. No text copied; the task distinction is reimplemented as separate skills. |
| [wanshuiyin/Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) | `341f914024d270dc5c8fa51337d1ad38829273aa` | MIT | Reviewed problem anchoring, method refinement, and claim-driven experiments in `research-refine`. The overnight unattended chain, fixed model, multi-agent loop, and scoring gates were **not** adopted. |

Because no upstream material was copied, no upstream license obligations are inherited by the distributed files. If future contributions copy, translate, or adapt upstream content, the corresponding license must be reviewed first — in particular the non-commercial and share-alike terms of Supervisor-Skills. This project does not claim endorsement by, or affiliation with, any referenced project.

## Runtime dependencies

The plugin scripts use the Python standard library only. No third-party Python packages are required at runtime; pytest is used only for development and testing.

## Visual asset

`assets/research-session-icon.png` was generated with AI assistance in an earlier project session. The repository owner confirmed this provenance on 2026-11-20 and distributes the asset under Apache-2.0 together with the repository (see [LICENSE](LICENSE)). No external icon set or stock artwork is bundled.

## Documentation references

The skill layout follows the public OpenAI skill design notes at `https://developers.openai.com/plugins/build/skills` and `https://developers.openai.com/plugins/concepts/skills`. Those pages are references, not bundled content.
