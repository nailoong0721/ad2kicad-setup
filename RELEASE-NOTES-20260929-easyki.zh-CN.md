# 2026-09-29：EasyKiConverter 与自定义工具目录

本版名称为 `AD2KiCad_Setup_20260929_v2_easyki.zip`。本次扩展环境检查与安装入口，保持 clean-root 版本的工程布局、库提取和转换产物目录约定。

## 变更

- 在 kicad-happy、KiCAD MCP 之外加入 [EasyKiConverter](https://github.com/EasyKiconverter/EasyKiConverter/) 的发现、健康检查、复用和按需安装配置。新安装使用官方 Release 的 Windows portable ZIP；不将第三方程序二进制装入本工具包。
- 缺少 MCP 时，由用户分别选择 `-McpRoot` 程序/checkout 目录、`-McpConfigRoot` MCP 自身配置目录。以服务的 `--config` 参数使用所选 `config.json`，现有配置内容保留。Codex 的 MCP 注册仍写入当前用户 `.codex/config.toml`，修改前备份。
- 缺少 EasyKiConverter 时，由用户分别选择 `-EasyKiRoot` 程序目录、`-EasyKiConfigRoot` 配置目录。两工具共四个独立目录，文件夹选择器逐项收集，支持中文和空格路径；全部必需选择完成才开始安装或注册。取消会中止；`-NonInteractive` 模式缺少必需参数时明确报错。
- 新 EasyKiConverter 配置先建立合法 JSON；生成启动器以 `--config`、`--cache-dir`、`--log-file` 使用选定目录。现有用户配置及缓存设置保留，健康的已有安装复用。上游导出事务恢复备份仍位于默认 AppData 位置。
- 在当前 Codex 用户目录的 `ad2kicad-tools.json` 记录工具位置，用于重跑发现；该文件属于本机状态，不随包发送。
- 独立环境脚本的默认报告进入工作区 `_ad2kicad_work/environment-...`。工程入口继续把环境报告、转换报告、日志及审计放入工程 `_ad2kicad_work`。

EasyKiConverter 用于 LCSC/EasyEDA 元件转 KiCad 库，是整理后的可选补库工具。它参加环境就绪检查，但工程整理不强制执行它；安装成功不代表缺失的 3D 模型已补齐。

## 本次验证状态

本节按实际证据逐项记录；原生命令检查、配置与启动器生成、实际配置加载探针分开记录。

| 检查 | 状态 |
| --- | --- |
| 本机已装 EasyKiConverter、MCP 与 kicad-happy 的发现和健康复用 | 通过；完整工程入口复用三工具，退出码 0 |
| MCP 原生协议与 KiCad 后端 | 通过：本机已有 MCP 为 233 工具，新装 MCP 2.8.2 为 244 工具；两者 initialize 与 155 个封装库查询均通过 |
| EasyKiConverter 原生 `--version`、`--help`、JSON 与必需启动参数 | 通过；复用 EasyKiConverter 3.1.11 |
| EasyKiConverter 实际自定义配置加载探针 | 安全跳过：本机存在上游事务恢复备份，`config_smoke.status = skipped_existing_transaction_data`、`passed = null`；不声称实际加载通过 |
| 目录选择、取消、缺参、非交互与主入口转发 | PowerShell 5.1 控制流测试 9 项通过；UI/Python 使用 stub，未模拟为真实窗口操作 |
| 新装 MCP 与 EasyKiConverter 的独立程序/配置目录、启动参数和重复运行 | 真实联网安装及 15 项检查通过；首次 50.552 秒，重复 13.579 秒；第二轮不重新下载、克隆或构建，注册与路径记录均 `changed=false` |
| 四项目录的真实中文、空格路径安装 | 通过；两个程序和两个配置分别位于中文空格目录，隔离 Codex 用户目录；不传四参数的后续发现从已保存选择恢复 |
| 本机已有 EasyKiConverter 配置及事务恢复备份 | 通过；完整入口前后哈希保持 |
| PowerShell 5.1 语法、Python 语法及分发回归 | 3 个脚本 AST 解析 0 错误，候选包 Python 语法检查通过；候选解压后 28 项测试通过（工具设置 9、MCP 注册 9、兼容 5、产物 5） |
| 工程完整入口复验及根目录转换产物为 0 | 通过；CS Power 复验 18.420 秒，工程与原始工程活动文件哈希保持，根目录转换产物为 0 |
| ZIP 清单、CRC、解压哈希与维护源一致性 | 30 文件的候选发行包通过；工具 allowlist、CRC、SHA-256、解压内容及源内容一致性均核验 |
| 候选解压入口的 speedup 完整复验 | 退出码 0、21.317 秒；三个工具复用、注册及路径记录未变；活动工程、原始工程及 Easy 配置/事务备份哈希保持，根目录转换产物 0 |

过去的工程修复证据见 `RELEASE-NOTES-20260929.zh-CN.md`；报告归档和迁移证据见 `RELEASE-NOTES-20260929-clean-root.zh-CN.md`。历史通过结果不代替本次环境变更的测试。

本次完整入口证据为工作区 `AD2Kicad_original_prj_test/_ad2kicad_work/easykiconverter_integration/integrated-acceptance.json`；该证据定位实际工程中的 `environment-20260929-123912/environment-state.json`。18.420 秒是**已安装工具、已整理工程的复验时间**，不是首次下载、安装或新工程转换时间。目录交互控制流的范围与结果见同一审计目录的 `ENVIRONMENT-INTEGRATION-REVIEW.md`。

真实缺失工具验收见同一审计目录 `REAL-INSTALL-ACCEPTANCE.md` 和 `真实 新装验收 20260929-124049/acceptance.json`。MCP 实际联网克隆、安装依赖及构建，版本 2.8.2，提交 `670d2a9c48e1588f2e07edaf656b5e301fc55a50`。EasyKiConverter 实际下载官方 v3.1.11 Windows x64 ZIP，74,293,664 bytes，下载 SHA-256 已校验。该次运行将已有 11 个 kicad-happy 技能复制到隔离用户目录后复用，所需 KiCad Python 包也已安装；50.552 秒不能作为另一台全新电脑的完整冷装耗时。

候选发行包证据为 `candidate-validation.json`；候选解压 speedup 运行证据为 `release-speedup-acceptance.json`，均在上述审计目录。21.317 秒同样是复验耗时。最终发行文件由同一工具 allowlist 和校验程序重新生成，最终 ZIP 的实际 SHA-256 以配套 `.zip.sha256.txt` 为准；候选 ZIP 的校验值不作为最终发行值。

## 转发范围

转发完整 ZIP 及配套 `.zip.sha256.txt`。包内附 `DISTRIBUTION-SHA256.json`，只包含安装整理脚本与说明。设计工程、运行缓存、本机路径记录、个人 Codex 配置、令牌和第三方软件二进制均不包含。

接收方自行安装 KiCad 并选择 CLI，准备符合 README 输入范围的工程副本。首次缺少工具时需要联网；健康工具可继续复用。本次已验证当前 Windows 机器上的全新目录联网安装，未验证另一台缺少运行库与 Python 包的冷机，也未执行真实 GUI 文件夹点选或器件转换。EasyKiConverter 实际配置加载探针因已有事务恢复备份安全跳过；本次没有调用其联网元件转换，也没有以测试为由停止或重启用户 GUI。
