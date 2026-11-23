# 安装与配置

本文件是 research-session 的安装唯一真源。同一份源码树同时提供 Trae 与 Codex 清单，共享 `skills/`、`scripts/`、`references/`、`contracts/` 与 `assets/`；两端安装都不依赖本机私有 marketplace 或 workspace。

## 目录形态

插件目录必须整体保留以下结构（复制时不要只挑 `skills/`）：

```
research-session/
├── .trae-plugin/plugin.json     # Trae 清单
├── .codex-plugin/plugin.json    # Codex 清单（与 Trae 清单同一版本与元数据）
├── skills/                      # 1 个入口技能 + 15 个子技能
├── scripts/                     # 通用治理 CLI
├── references/                  # 通用参考
├── contracts/                   # v1 契约（provider、run manifest、recovery）
├── assets/                      # 图标与配置模板
└── docs/                        # 本安装说明与 provider 示例
```

两份清单的 `version` 必须一致；运行时预检发现版本不一致会直接拒绝，而不是择一信任。

## Trae 安装

1. 将整个插件目录（含 `.trae-plugin/plugin.json` 与 `skills/`）放入 Trae 的插件目录。具体路径随 Trae 版本变化，以宿主设置为准；不要放进任何私有 marketplace 目录。
2. 重启 Trae 或执行宿主的插件重新加载。
3. 在会话中选择 Research Session，确认能看到 `research-session` 入口技能及其子技能。

Trae 读取 `.trae-plugin/plugin.json`，`skills` 字段指向插件根下的 `./skills/`。

## Codex 安装

1. 将同一插件目录放入 Codex 的 marketplace 插件目录（保持目录名与清单 `name` 一致：`research-session`）。
2. Codex 以 `plugin.json` 的 `version`（当前 `6.4.2`）识别与升级插件。
3. 在会话中确认入口技能与子技能可见。

## 升级

- 用新版本整体替换插件目录，不保留旧目录的混合文件；项目侧的复核台账与证据不做迁移。
- 规则摘要变化时的续接语义：
  - 未知规则摘要一律拒绝续接，旧回执仍然只读保留；
  - 只有已登记为兼容对的版本之间可以续接；
  - 无论是否兼容，都必须重新核验原证据，不因升级把旧结论当作当前通过。

## 卸载

- 删除插件目录即可；不会删除用户项目中的复核台账、证据或记录。
- 也不会删除 `~/.agents/state/research-review`（Windows 为 `%USERPROFILE%\.agents\state\research-review`）：
  - 它是跨项目的只追加复核台账（`ledger.jsonl`）与发送锁记录，在首次执行复核/治理相关命令时创建；
  - 记录项目身份、review_id、回执路径与哈希，用于跨目录续接与发送锁恢复；
  - 需要彻底重置复核历史时再自行删除该目录，删除后历史续接与锁恢复证据随之丢失；可用 `RESEARCH_SESSION_LEDGER_DIR` 指向其他目录。

## 配置在线复核（可选）

离线预检、只读治理与合成夹具不需要任何 provider。在线复核必须显式配置 Responses 端点与模型：

- 参考 `docs/provider-config.example.json` 创建自己的 provider 配置（只含 `url`、`model`、`provider`、`api_key_env` 四个键）；
- 凭据只从 `api_key_env` 指定的环境变量读取，配置文件、日志与回执中都不出现密钥；
- 通过 `--provider-config` 传入，或设置 `RESEARCH_SESSION_PROVIDER_CONFIG`。

## 故障排查

- **`provider_not_configured`**：未提供端点或模型。命令以退出码 2 停止，输出 `stage=provider` 与 `provider_not_configured: set Responses URL and model explicitly`；按上节配置后重试。不会尝试连接任何本机默认网关。
- **CLI 自检**：在插件根执行
  `python -m pytest tests skills/research-session/tests -q`。
  测试不访问网络；真实 provider 调用不在默认测试范围内。
- **Trae/Codex 未识别插件**：
  1. 确认对应目录存在且可解析：`.trae-plugin/plugin.json` / `.codex-plugin/plugin.json`；
  2. 确认两份清单 `version` 一致且等于技能文件声明的 `6.4.2`；
  3. 确认清单 `skills` 指向的 `./skills/` 存在，且其中含 `research-session/SKILL.md`；
  4. 重新加载宿主。
- **清单版本不一致**：预检直接报错（fail-closed），不会选用其中一份继续。

## Python 与验证状态

- 代码使用 `X | None` 与 `dict[str, ...]` 等语法，最低 Python 3.10；已在 3.12.3（Ubuntu）与 3.13.5（Windows）实机验证，3.10 尚未实机验证。
- 已验证：Windows + Python 3.13.5 与 Ubuntu 24.04（WSL2）+ Python 3.12.3 的离线测试（离线夹具、契约、双端一致性），pytest 8.3.4。
- CI 矩阵声明 Python 3.10 与 3.13（Windows/Ubuntu），但 GitHub Actions **尚未在线运行**；在 CI 通过前，不得把 3.10 写成已验证版本。
- 未验证：干净环境中的实际安装、Trae 与 Codex 宿主的真实加载、以及真实 provider 的在线调用。这些项在测试中以显式 skip 记录，不冒充通过。
