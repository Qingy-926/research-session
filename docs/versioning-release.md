# 版本、发布与回滚

本文件说明 research-session 公共版本的版本号规则、发布流程、回滚方式、契约弃用与同步边界。当前源树为 **6.4.1 本地候选**，以下流程用于准备发布包。

## 1. 语义化版本

- **MAJOR**：删除或改义公共契约字段/枚举、改变暂停恢复或错误语义、移除技能、清单元数据不兼容、必须重新配置才能运行。
- **MINOR**：新增技能、新增可选契约字段、新增默认关闭的能力，现有配置和回执继续可用。
- **PATCH**：文档、测试、无行为变化的修复；复核合同、科研门和轮次语义保持不变。
- 两份 `plugin.json`、`skills/research-session/SKILL.md` 的版本行与 `CHANGELOG.md` 必须同步；不一致按预检失败处理。

## 2. 发布流程

1. **候选冻结**：功能与文档冻结，只接受发布阻断修复；`CHANGELOG.md` 写好对应版本段落。
2. **全量离线测试**：在插件根执行
   `python -m pytest tests skills/research-session/tests -p no:cacheprovider -q -ra`，
   要求 failed=0；每个 skip 必须有明确理由（宿主加载、平台专有内核路径、真实 provider 等）。
3. **隐私与许可扫描**：确认无本机绝对路径、私有项目名、真实令牌、真实台账、缓存或 `.bak`；核对 [LICENSE](../LICENSE) 与 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 未被改动或遗漏。
4. **打包**：运行 `python tools/package_release.py`；脚本从干净源树生成 `dist/research-session-<version>.zip`，按固定规则排除 `.git`、`dist`、`__pycache__`、`.pytest_cache`、虚拟环境、日志、`.bak`、私有 provider 配置、运行清单与 `04-验证` 类私有证据，保留双端清单、`skills/`、`scripts/`、`references/`、`contracts/`、`assets/`、`docs/` 与 `.github/`。
5. **计算 SHA-256**：脚本同时写入 `<zip>.sha256` 与 `<version>.manifest.json`；记录两者，并确认同一版本的两次打包哈希一致。
6. **打标签**：使用附注标签 `vX.Y.Z`，标签说明即发布说明的权威摘要。
7. **发布说明**：对应 `CHANGELOG.md`，逐项列出迁移影响、破坏性变更与未验证项；不得把未验证能力写成已通过。
8. **发布后核对**：从发布包（而非源目录）做一次离线冒烟：版本识别、16 个技能可见、契约文件齐全、`audit_dispatch.py --probe` 在未配置 provider 时拒绝且不发起请求。

未执行的步骤（例如 GitHub Actions 在线运行、公开下载、市场上架）只能在发布说明里标注为未验证，不能写成已完成。

### 打包与校验命令

打包脚本只依赖标准库，Windows 与 Ubuntu 均可运行。版本取自 `.codex-plugin/plugin.json`，输出为 `dist/research-session-<version>.zip`、`<zip>.sha256`（`sha256sum` 兼容行）与 `<version>.manifest.json`（版本、契约版本、逐文件 SHA-256、文件总数、排除规则摘要）。

```bash
python tools/package_release.py                  # 输出到 dist/
python tools/package_release.py --out /tmp/release-a
python tools/package_release.py --check-only     # 只构建并自检，不写 dist
```

同一源树两次打包必须字节一致：

```bash
python tools/package_release.py --out /tmp/release-a
python tools/package_release.py --out /tmp/release-b
sha256sum /tmp/release-a/research-session-6.3.0.zip /tmp/release-b/research-session-6.3.0.zip
```

校验已生成的包（在 `dist/` 内执行）：

```bash
sha256sum -c research-session-6.3.0.zip.sha256
```

Windows PowerShell：

```powershell
Get-FileHash .\dist\research-session-6.3.0.zip -Algorithm SHA256
```

脚本在写盘前后都会解包到临时目录自检：两份 `plugin.json` 版本一致、技能数为 16、必需路径齐全、无被排除文件、逐文件哈希一致；任一失败即以非零状态退出。发布包包含 `.github/`（issue/PR 模板、CODEOWNERS 与 workflow），便于接收方复现同样的检查。

CI 定义在 `.github/workflows/ci.yml`：`windows-latest` / `ubuntu-latest` × Python 3.10 / 3.13，只安装 pytest，默认离线执行；`tests/conftest.py` 会阻止非回环网络连接，需要真实 provider 的用例须同时设置 `RESEARCH_SESSION_ALLOW_LIVE=1` 与显式 provider 配置。该 workflow **尚未推送**，在线状态未验证。

## 3. 回滚

- 保留上一版本的发布目录与 SHA-256，回滚即切回旧目录或旧标签；不就地改写历史台账、回执或已生成的运行清单。
- 规则摘要未知时一律拒绝续接；回滚后也必须重新核验原证据，不把旧结论当作当前通过，不重置轮次。
- 台账为只追加结构；回滚不得删除或就地修改记录。

示例（`<old>` 为上一版本号）：

```bash
cd dist && sha256sum -c research-session-<old>.zip.sha256
# 解压旧包，整体替换插件目录；保留项目内的台账、回执与证据目录
python -m pytest tests skills/research-session/tests -p no:cacheprovider -q -ra
```

## 4. 公共契约弃用

- `contracts/*.v1.json` 的稳定接口是字段名、枚举值和必填集；新增可选字段不升主版本。
- 删除字段、改变含义或改变枚举语义必须新建 `*.v2.json`，并让 v1 至少保留一个发布周期；新旧主版本并存，而不是直接覆盖。
- 每次变更或弃用在 [contracts/README.md](../contracts/README.md) 登记一行：日期、版本、字段、原因、迁移方式。
- 已弃用字段在下一主版本移除；旧清单与旧回执只读保留。

## 5. 上游、私有源与公开树的同步边界

- 方向是**单向**的：私有维护源或 `.agents` 共享副本进入公开候选时，必须重新做隐私扫描、路径与 provider 检查、许可核对和离线测试；不能整目录复制后直接发布。
- 公开树的改动不自动回写私有维护源或共享入口。用户另行授权本机升级时，从已验发布包更新指定安装源，保留旧版本与恢复入口；宿主缓存继续由宿主管理机制生成，不由脚本直接改写。
- 公开树的改动不自动进入宿主 marketplace 或本机插件目录；安装与升级按 [install.md](install.md) 手动完成。
- 私有项目数据、真实台账、备份文件和凭据不进入公开树；合成夹具必须标明为合成。
