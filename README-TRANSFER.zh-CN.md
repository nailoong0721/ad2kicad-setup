# AD2KiCad 工程整理包（Windows）

1. **安装完整 KiCad**：自行安装 Windows 版 KiCad 9 或 10，保留自带 Python 和标准封装库。
2. **解压并准备副本**：完整解压 `AD2KiCad_Setup_20260929_v3_wizard.zip` 到可写目录，准备已经从 Altium 导入 KiCad 的工程副本，关闭打开该副本的 KiCad 窗口。
3. **双击并按中文向导操作**：运行 `Setup-AD2KiCad.cmd`，选择自己的 `kicad-cli.exe`、工程副本和库名；检查向导中的程序及配置目录，按需要修改后开始配置。缺少的 Node.js、源码和依赖由流程处理。

本包用于整理**已经从 Altium 导入 KiCad** 的工程，并配置当前用户的 Node.js/npm、kicad-happy、KiCAD MCP 和 EasyKiConverter。健康安装会复用。无需提前安装 Git 或 Node.js；首次安装或修复需要联网。工具包不包含工程或第三方软件二进制。操作前请阅读下方输入限制；本轮验收状态见 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md`。

## 接收方准备

1. 自行安装 Windows 版 KiCad 9 或 10，包含标准封装库。环境检查会访问所选安装的 `share/kicad/footprints`；仅有 CLI 可执行文件不够。首次运行时选择自己电脑上的 `kicad-cli.exe`。
2. 确保可访问 Node.js 官方下载站、GitHub（包括 API、codeload 和 Release 下载）、npm 与 Python 包索引。所选 CLI 同目录须有能导入 `pcbnew` 的 `python.exe`；工程整理和 MCP 使用这一套 KiCad Python。环境引导需要 Python 3.11 或更新版本；完整 KiCad 自带运行时可作为引导 Python。另一套 Python 的检查结果不能替代所选 KiCad 环境的验证。缺少 pip 时流程尝试用该 KiCad Python 的 `ensurepip --user` 补齐，再安装缺少的 Python 包；无法补齐会给出诊断，不要求另装一套无关 Python。
3. 将本 ZIP 解压到可写目录，保持包内文件在同一文件夹。准备一个**工程副本**；不要直接选唯一的原始工程。关闭打开这个工程的 KiCad 窗口。

## 可处理的工程

首次整理要求文件平铺在工程根目录：恰有一个 `.kicad_pro`、一个 `.kicad_pcb`，以及与项目同名的主 `.kicad_sch`。其余原理图也须在根目录，可以是子图，也可以是 `.kicad_pro` 中明确登记的独立顶层。程序保留各独立顶层，不将它们强制拼接成一个电路。

原理图实例须有可提取的内嵌符号定义；物理器件位号须能与 PCB 对应，多单元符号共享同一物理位号。现有输入限制包括：多个 PCB、原理图预先分散在子目录、继承符号、缺失或循环子图、未登记的孤立图、不同独立顶层共用同一子图、重复或无法匹配的物理位号。遇到这些情况会保留诊断现场，本包不承诺兼容所有 AD 导入结果。

所有独立顶层原理图均保留在工程根目录；只有真实的层级子图进入 `00_sheet`，该目录仅含 `.kicad_sch`，没有层级子图时可为空。`.kicad_pro` 保留全部顶层入口，`top_level_sheets` 的 `filename` 使用纯文件名，不含目录分量，并修正所属顶层 UUID。每个顶层分别导出网表并执行 ERC；验证上下文和报告位于审计目录。`01_Lib` 中各有一个活动 `.kicad_sym`、`.pretty` 和 `.3dmodels`；旧库及必要变体备份放在 `01_Lib/AD_lib_bak`。

独立顶层留在根目录是原生加载兼容要求：本次测试中，顶层 `filename` 含目录分量会使 `pcbnew.LoadBoard` 读取同名工程时触发 `wxFileName::Assign` 断言并挂起。因此只移动真正的层级子图，同时保留原有独立电路与层级关系，不通过新增包装图改变拓扑。

有真实模型时，程序从 PCB/封装内嵌资源和可解析文件引用收集原字节，校验、去重后放入 `.3dmodels`，更新 PCB 与库封装的引用，并保持模型位移、旋转和缩放。无模型或找不到模型时，记录缺失项并保留无法解析的原引用，供后续补齐；不会伪造模型。空目录只表示当前没有可收集模型，不代表模型已齐全。

## 转换产物的位置

本版将转换报告、索引和修复说明统一放入工程的 `_ad2kicad_work`，不在工程根目录生成这些文件。以下文件直接位于 `_ad2kicad_work` 下：`AD2KICAD_REPORT.json`、`AD2KICAD_MAPPING.json`、`AD2KICAD_MISSING_3D.json`、`AD2KICAD_FOOTPRINT_VARIANTS.json`、`AD2KICAD_FOOTPRINT_INDEX.md`、`AD2KICAD_STANDALONE_PADS.json`、`AD2KICAD_SETUP_RUN.json`，以及本工具生成的修复说明 Markdown。输入快照、日志及逐次运行的审计目录继续保留在该工作目录中。

升级运行会先按原字节备份旧版根目录报告，再将它们迁入 `_ad2kicad_work`；重跑、改库名和失败恢复识别新旧报告位置。索引从工作目录使用 `../01_Lib/...` 链接项目库。原生 `.kicad_sch`、`.kicad_pcb`、`.kicad_pro`、`sym-lib-table`、`fp-lib-table`、`00_sheet` 和 `01_Lib` 的布局不因报告迁移而改变。

报告的 `run_directory` 和 `output_hashes` 中所有相对路径仍以**工程根目录**为基准，不以报告所在的 `_ad2kicad_work` 为基准。例如模型仍指向 `01_Lib/...`，已迁移的映射报告指向 `_ad2kicad_work/AD2KICAD_MAPPING.json`。

## 运行

双击 `Setup-AD2KiCad.cmd`，按窗口顺序选择自己的 `kicad-cli.exe`、待整理工程**副本**，再输入库名。库名可使用 1 到 64 个英文字母、数字、下划线或连字符；直接接受默认值也可以。若双击窗口关闭，打开 PowerShell 在本包目录执行下面的命令，以便看到报错：

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '.\Setup-AD2KiCad.ps1'
```

程序先发现已有工具，再检查所选目录冲突、链接及写权限；全部必需选择通过预检后才开始安装。向导显示 Node.js/npm、KiCAD MCP 和 EasyKiConverter 的六个程序/配置目录，可保留已发现的健康安装，也可修改位置。新程序使用可写空目录；未知的非空目录不会被覆盖。遇到目录错误，按提示修改后再次检查。

Node.js 缺失时下载官方 Windows ZIP，核对官方 SHA-256 后安装；支持复用通过版本、架构和 npm 检查的 Node.js LTS 22 及以上版本，过期或非 LTS 版本不会直接复用。新安装根据运行时的官方版本索引选择受支持的最新 LTS，不把文档里的某个版本号作为下载地址。npm 配置、缓存、全局安装前缀与日志使用所选配置目录。整个流程不修改系统或用户的永久 PATH；构建时只给子进程临时补充 Node 路径，Codex 注册使用 `node.exe` 的绝对路径。

kicad-happy 和 KiCAD MCP 从各自官方仓库获取：有 Git 时可浅克隆；无 Git 时通过 GitHub API 确定默认分支提交，再下载固定提交的官方 ZIP，在暂存目录校验必要文件后安装。源码记录包含仓库、提交和本地 SHA-256；这不等于验证了上游数字签名。kicad-happy 指向真实技能目录的已验证内部别名可以省略，不需要在 Windows 创建符号链接。其他未知链接会被拒绝。

改动 Codex 配置前会将旧配置备份到所选 Codex 根目录的 `backups/ad2kicad`。新登记的 MCP 需重启 Codex 或重载连接才能在当前会话中使用。工程在隔离候选中完成原生 PCB 加载预检、逐顶层网表/ERC 与封装加载校验后才发布。PCB 预检同时加载同名 `.kicad_pro`，用于提前发现工程上下文导致的原生加载问题。

`-VerifyOnly` 仅限制工程整理阶段为验证已有结果，**仍会执行环境检查、按需安装和配置工具、更新路径记录或 MCP 注册**；它不是整个入口的只读模式。未成功整理过的工程不能使用该参数。

### 新工具的自定义目录

向导集中展示下面六个目录，允许独立浏览或修改，支持中文、空格和常见特殊字符。新装时三个程序目录和三个配置目录应分别使用独立位置；不要让不同工具目录相同或互相嵌套，不要把配置放进待安装的程序目录。界面会给出建议位置，最终使用用户确认的选择。取消向导不会开始安装或注册。

| 参数 | 由用户选择的用途 |
| --- | --- |
| `-NodeRoot` | Node.js 程序目录；可选择健康 LTS 安装，或用于官方 ZIP 安装的空目录。 |
| `-NodeConfigRoot` | 本流程使用的 `npmrc`、`cache`、`prefix` 和 `logs` 目录。已有 `npmrc` 保留，本流程通过环境参数使用所选位置。 |
| `-McpRoot` | KiCAD MCP 的程序、仓库 checkout 和构建目录。 |
| `-McpConfigRoot` | KiCAD MCP 自身的配置目录；以 `--config` 传入其中的 `config.json`。与程序目录独立选择。 |
| `-EasyKiRoot` | EasyKiConverter 程序目录；新安装从官方 Release 获取 Windows portable ZIP。 |
| `-EasyKiConfigRoot` | 新安装 EasyKiConverter 的配置、缓存和日志目录，与程序目录独立选择。 |

可以在命令中提前指定这些目录。下面的路径仅为示例，须替换为接收方自己选择的位置：

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '.\Setup-AD2KiCad.ps1' -ProjectRoot 'E:\我的工程\工程副本' -KiCadCli 'D:\KiCad\bin\kicad-cli.exe' -NodeRoot 'D:\EDA 工具\Node.js' -NodeConfigRoot 'E:\EDA 配置\Node.js' -McpRoot 'D:\EDA 工具\KiCAD MCP' -McpConfigRoot 'E:\EDA 配置\KiCAD MCP' -EasyKiRoot 'D:\EDA 工具\EasyKiConverter' -EasyKiConfigRoot 'E:\EDA 配置\EasyKiConverter'
```

无人值守运行加 `-NonInteractive`，明确提供 `-ProjectRoot` 和 `-KiCadCli`。首次缺少 Node.js 时传 `-NodeRoot`、`-NodeConfigRoot`，首次缺少 MCP 时传 `-McpRoot`、`-McpConfigRoot`，首次缺少 EasyKiConverter 时传 `-EasyKiRoot`、`-EasyKiConfigRoot`。缺少必填路径会明确报错，不会弹窗或擅自决定新安装位置。已找到健康安装及有效路径记录时可以复用；失败后重跑也会重新检查这些位置。

全部目录通过预检后，选择会保存到 Codex 用户目录的 `ad2kicad-tools.json`；即使后续联网步骤失败，也可按保存位置重跑。位置记录只表示选择，不表示安装已成功。成功与否以本次环境报告为准。

主入口及独立环境入口都支持 `-CodexRoot`，用于明确指定实际使用的 Codex 用户目录；不传时使用 `CODEX_HOME`，否则使用用户主目录下 `.codex`。MCP 注册写入该目录的 `config.toml`，kicad-happy 技能写入其 `skills`，路径记录及备份也在这里。该参数不会自动改变 Codex 应用自身的数据目录，须与应用实际使用的目录一致。MCP 自身的 `config.json` 仍通过 `--config` 使用用户选择的位置，已有内容保留。

EasyKiConverter 新配置先写入合法 JSON，再生成带 `--config`、`--cache-dir` 和 `--log-file` 的启动器；从该启动器打开软件时使用选定配置。复用已有配置时保留其原内容和缓存设置。上游的导出事务恢复备份仍使用默认 AppData 位置，不随这些参数重定向。环境报告记录实际选择、复用或安装结果以及启动器路径，独立运行 `Setup-AD2KiCadEnvironment.ps1` 时默认也写入工作区 `_ad2kicad_work/environment-...`。

EasyKiConverter 检查分别记录版本/帮助命令、配置 JSON 与启动器检查，以及 `config_smoke` 配置加载探针。若发现已有事务恢复备份，探针会跳过，避免触发上游恢复流程；此时 `status` 为 `skipped_existing_transaction_data`、`passed` 为 `null`，不能表述为实际自定义配置加载已通过。该状态不影响其余已完成检查的记录。

EasyKiConverter 用于从 LCSC/EasyEDA 元件资料生成 KiCad 库，可作为整理后补充器件库的工具。环境检查会检查它，但本工程整理流程不强制调用它转换器件，也不会通过安装它自动补齐缺失的 3D 模型；实际补库仍须选择并核实对应元件。

整理器启动的检查子进程采用 180 秒超时，末尾原生封装索引采用 120 秒超时；超时会报错，不能视为通过。这是单步限时，不是整个配置流程的耗时承诺。失败时保留工程下的 `_ad2kicad_work` 及日志供诊断或恢复，不要直接删除。

本次运行退出成功后，检查 `_ad2kicad_work/AD2KICAD_SETUP_RUN.json` 的 `passed`，以及 `_ad2kicad_work/AD2KICAD_REPORT.json` 的 `verified`、`native_board_validation`、`top_level_sheets`、`circuit_counts`。从工程根目录解析报告的 `run_directory`，查看其中 `audit/native-board.json`、`audit/native-board.log` 和 `audit/native-project/session_report.json`；确认 PCB 连同同名项目成功加载，且 `top_level_results` 覆盖全部顶层。同一工作目录下的 `AD2KICAD_MAPPING.json` 记录器件映射，`AD2KICAD_MISSING_3D.json` 记录待补模型。原有电气 ERC 问题不由整理脚本修复；通过的含义是库映射错误消除且电路和 PCB 数据保持，并非电气设计已无错误。

完成后可打开根目录 `.kicad_pro` 检查工程。重新运行可复验已整理结果；受管文件若有未记录的人工修改，程序会拒绝覆盖。

## 常见失败怎么办

- **找不到 KiCad Python 或标准封装库**：修复完整 KiCad 安装，并重新选择该安装的 `bin/kicad-cli.exe`；不要仅复制 CLI，也不要用另一套 Python 的结果替代。
- **目录冲突、非空或无写权限**：在向导中为程序和配置选择独立的可写目录。旧版或损坏 Node 的原目录会保留，可选择新的空目录安装。不要通过删除未知内容来强行继续。
- **下载、npm 或 pip 失败**：查看提示和 `setup-console.log`、`environment-state.json`，确认官方站点与包索引可访问，再按已保存目录重跑。pip 的 `ensurepip` 修复失败时按报告修复所选 KiCad Python。
- **安装了 MCP 但当前会话看不到工具**：重载 MCP 连接或重启 Codex，核对实际 Codex 根目录；安装探针通过不表示当前会话已经热加载。
- **工程验证失败或超时**：保留工程及 `_ad2kicad_work`，根据对应日志诊断；不要忽略校验、删掉快照或覆盖人工修改继续发布。

本轮 Node.js、六目录中文向导及缺少 Git 的安装路径，具体实证、计时与未验证范围以 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md` 为准。此前的中文空格四目录安装、28 项分发回归、MCP 2.8.2 工具数等均为旧 easyki 版本的历史记录，见 `RELEASE-NOTES-20260929-easyki.zh-CN.md`，不能替代本轮验收。clean-root 的报告迁移及恢复证据见 `RELEASE-NOTES-20260929-clean-root.zh-CN.md`，此前故障修复证据见 `RELEASE-NOTES-20260929.zh-CN.md`。

本版分发名称为 `AD2KiCad_Setup_20260929_v3_wizard.zip`，配套 `.zip.sha256.txt`。ZIP 内 `DISTRIBUTION-SHA256.json` 列出工具文件 SHA-256；实际文件数、发布校验结果和整体 SHA-256 以本轮发布说明及配套文件为准，旧版 30 文件数量不适用于本版。本包只含工具和说明，不含设计工程、运行缓存、本机路径记录、个人 Codex 配置、令牌或第三方软件二进制。请完整解压并使用新工具文件夹，避免混用新旧模块。

本机隔离目录或剥离 PATH 的验收不等于接收方另一台全新电脑的完整冷装；使用 stub 的对话框测试也不等于真人点选。EasyKiConverter 是否完成实际配置加载应读取本次 `config_smoke`；跳过的探针不能算通过。工具安装和 CLI 验证不表示已经完成 GUI 操作、联网器件转换或电气设计审查。本包不包含工程数据，接收方须另行提供符合范围的工程副本。第三方项目包括 kicad-happy、KiCAD-MCP-Server、EasyKiConverter 和 Node.js，其许可与运行要求以对应项目为准。
