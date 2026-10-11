# 给接收方 Codex 的执行入口

接收方开始只需三步：安装包含 Python 与标准封装库的完整 Windows KiCad；完整解压 `AD2KiCad_Setup_20260929_v3_wizard.zip` 并准备工程副本；双击 `Setup-AD2KiCad.cmd`，按中文向导选择 CLI、工程、库名和工具位置。Git 和 Node.js 无需预装，缺失工具由流程按需获取。

先读取同包 `README-TRANSFER.zh-CN.md` 和 `CONFIGURATION-RECORD-TRANSFER.zh-CN.md`；本轮 Node.js 与六目录向导的实际验收状态见 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md`。旧 EasyKiConverter 验收、clean-root 报告迁移及 CS Power 修复分别见对应历史发布说明。不得把历史测试结果写成本轮已验证结果，也不得代填发送方本机路径。

执行顺序：

1. 确认用户已安装 KiCad，使用用户确认的 `kicad-cli.exe` 路径；已有确认继续有效。用户可双击 `Setup-AD2KiCad.cmd` 选择。按 README 检查所选安装的 Python/pcbnew、标准封装库及其余前提。
2. 确认输入是符合 README 范围的已导入工程副本。读取 `.kicad_pro` 的顶层清单与全部原理图关系，保留独立顶层语义。保护原始工程及既有失败快照。
3. 运行 `Setup-AD2KiCad.ps1 -ProjectRoot <工程副本> -KiCadCli <用户确认的路径>`；必要时加 `-LibraryName <名称>` 和指向实际 Codex 用户目录的 `-CodexRoot <目录>`。六个可自定义目录为 `-NodeRoot`、`-NodeConfigRoot`、`-McpRoot`、`-McpConfigRoot`、`-EasyKiRoot`、`-EasyKiConfigRoot`。窗口会展示发现结果与建议位置，允许用户修改；全部必需目录通过冲突、链接及写权限预检后才安装，取消时中止。无人值守加 `-NonInteractive` 并提供项目、CLI 和缺失工具对应的程序/配置路径；已保存且有效的位置可复用。缺少必填路径应补齐后重跑，不猜测用户的新安装位置。
4. 检查当前运行退出码和 `_ad2kicad_work/AD2KICAD_SETUP_RUN.json`；读取同目录 `AD2KICAD_REPORT.json` 的全部验证项，包括 `native_board_validation.passed` 和 `same_stem_project_loaded`。从工程根目录解析 `run_directory`，读取其中 `audit/native-board.json`、`audit/native-board.log` 与 `audit/native-project/session_report.json`。`output_hashes` 也按工程根目录解析。核对全部顶层的网表/ERC 证据并报告保留的电气问题。
5. 核对 `_ad2kicad_work/AD2KICAD_MAPPING.json` 覆盖所有符号和 PCB 封装；核对模型资产哈希、引用与变换，结合同目录 `AD2KICAD_MISSING_3D.json` 报告待补模型。已有真实模型必须收集，不能套用其他样本的空模型选择。
6. 核对布局：所有独立顶层均留在根目录，`.kicad_pro` 的 `top_level_sheets.filename` 均为纯文件名；`00_sheet` 仅含真实层级子图，无子图时可为空。此布局避免已复现的 `pcbnew.LoadBoard` 同名工程加载断言，保留原有拓扑。`01_Lib` 中各有一个活动 `.kicad_sym`、`.pretty`、`.3dmodels`，旧库进入 `AD_lib_bak`。重新运行入口复验后，记录完成情况和未验证范围。
7. 核对所有转换报告、索引、独立焊盘清单和修复说明均直接位于 `_ad2kicad_work`；根目录不留这些新旧产物。确认旧报告原字节已有迁移备份，索引的 `../01_Lib/...` 链接可达。升级项目还应检查二次运行、改库名和失败恢复对新旧报告位置的兼容，保留成功工程恢复防护。独立环境脚本默认报告同样进入工作区 `_ad2kicad_work`。
8. 读取本次 `environment-state.json`、`node-state.json` 和 `setup-console.log`，核对 Node.js/npm、kicad-happy、MCP 与 EasyKiConverter 的结果。Node 复用要求支持的 LTS 22 及以上版本、Windows 架构和 npm 健康检查均通过；新装按官方版本索引动态选支持的最新 LTS 并校验官方 ZIP SHA-256。核对 npm 的 `npmrc`、`cache`、`prefix`、`logs` 位于所选配置目录，已有 npmrc 保留。不要要求修改永久 PATH：构建子进程有临时路径，Codex 使用 Node 绝对路径。
9. 确认 MCP 程序与自身配置分别使用所选位置，Codex 通过 `--config` 登记 MCP `config.json`。确认 EasyKiConverter 程序、配置和生成启动器的路径；启动器对新配置使用 `--config`、`--cache-dir`、`--log-file`，已有用户配置和缓存设置保持。分别读取 `easykiconverter.checks` 与 `config_smoke`：事务恢复数据存在时探针应明确跳过，不能把总环境通过表述为实际配置加载通过。目录记录 `ad2kicad-tools.json` 用于本机重跑，不打入转发 ZIP；保存位置并不等于安装成功。

缺少 Git 时，源码获取通过官方 GitHub API 确定默认分支提交，并从 codeload 下载固定 SHA 的 ZIP；有 Git 时可浅克隆。健康的已有 ZIP 源码也可复用，不能仅凭 `.git` 判断完整。检查安装标记 `.ad2kicad-source.json` 中仓库、提交、来源和本地 SHA-256；记录本地哈希不等于验证上游签名。kicad-happy 指向真实技能的已验证内部别名可省略，实际 `skills` 文件必须完整，其他未知链接仍拒绝。源码或安装失败时保留暂存证据。

MCP 缺少 Python 依赖时使用所选 KiCad Python 安装。若该解释器缺 pip，流程尝试 `ensurepip --user`；若仍失败，应修复这套 KiCad Python，不能因另一套 Python 正常就宣称环境通过。新目录选择在预检通过后保存，联网失败后按记录重跑；不要删除未知目录来绕过保护。

`-VerifyOnly` 仍执行完整环境检查、按需安装及配置，可能更新 MCP 注册和路径记录；它只让工程整理器验证已有结果。不能把该开关当作整个入口的只读模式，也不能用于从未成功整理的工程。`-CodexRoot` 只决定本次写入哪套 Codex 配置，不自动改变 Codex 应用的数据目录；省略时遵循 `CODEX_HOME` 或用户 `.codex`。

若输入不满足范围或验证失败，查看 `_ad2kicad_work` 的实际错误并保留恢复数据。整理器检查子进程的 180 秒限时和封装索引的 120 秒限时均为失败保护；看到超时应诊断对应步骤，不能绕过检查发布。控制字符的 XML 兼容处理仅作用于可逆的内存解析，不能以修改网名或原文件绕过验证。新 MCP 注册后需重载连接。EasyKiConverter 是后续 LCSC/EasyEDA 元件补库工具；其环境检查通过不代表缺失模型已经补齐，也不要求本次工程整理调用它转换器件。

本轮交付为 `AD2KiCad_Setup_20260929_v3_wizard.zip`，核对包内 `DISTRIBUTION-SHA256.json` 及包旁整体 SHA-256。本轮实际测试计数、联网安装结果、二次复用、发布字节一致性和未验证范围统一读取 node-wizard 发布说明，不提前宣称通过。旧 easyki 说明里的 28 项回归、30 文件包、MCP 2.8.2 及工具数均为历史数据，不是本版验收标准。

接收方仍须检查自己的工程报告。本机隔离目录或移除 Node/Git PATH 的安装不能称为另一台电脑全套冷装；stub 对话框测试不能称为真实 GUI 点选。若某次 Easy 配置加载探针被跳过，应明确报告该事实；安装工具不表示已完成联网器件转换、模型补齐或 GUI 验收。clean-root 历史说明继续保留报告迁移、重跑、外部模型保护和恢复回归证据。

