# 可移植交付记录

接收方操作三步：安装包含 Python 与标准封装库的完整 Windows KiCad；完整解压 `AD2KiCad_Setup_20260929_v3_wizard.zip` 并准备工程副本；双击 `Setup-AD2KiCad.cmd`，按中文向导选择 CLI、工程、库名及工具位置。Git 和 Node.js 无需提前安装。

这份记录随工具包发送，供后续 Codex 会话恢复配置思路。软件前提、输入限制与运行步骤以同包 `README-TRANSFER.zh-CN.md` 为准；本轮 Node.js、六目录及中文向导的实际验收状态见 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md`。旧 easyki、clean-root 和修复发布说明仅保留各自历史证据。接收方不需要发送方本机配置或包外历史文档。

## 已确定的行为

- KiCad 由接收方安装并选择 CLI；工程和 MCP 使用所选安装的 Python/pcbnew，环境检查还要求该安装的标准封装库可用。
- 入口发现 `Node.js/npm`、`kicad-happy`、`KiCAD-MCP-Server` 与 `EasyKiConverter`，复用健康安装，按需安装和构建。选择目录先通过冲突、链接与写权限预检，才开始安装。Node 和 EasyKiConverter 新装分别使用官方 Windows ZIP；源码来自限定的两个官方 GitHub 仓库。Codex 配置修改前写入所选 Codex 根目录 `backups/ad2kicad` 备份。
- Node 复用要求受支持的 LTS 22 及以上版本、适配的 Windows 架构和 npm 健康检查通过；新装动态读取官方版本索引，选择受支持的最新 LTS，并核对官方 SHA-256。不会静默覆盖旧版、损坏或未知的非空程序目录，可另选空目录安装。永久系统/用户 PATH 不改写，npm 构建子进程临时补充 Node 路径，MCP 使用绝对 Node 路径启动。
- Git 不是先决条件。有 Git 可浅克隆；无 Git 时从 GitHub API 获取默认分支提交，再从 codeload 下载固定 SHA 的 ZIP，暂存校验通过才发布。已有健康 ZIP 源码可复用；`.git` 本身不证明完整。安装标记记录仓库、提交、下载来源、下载与源码本地 SHA-256，不声称验证上游签名。kicad-happy 内已验证且指向真实技能的快捷别名可省略，其他未知链接拒绝。
- 所选 KiCad Python 缺少依赖时按需安装；若没有 pip，先尝试该解释器的 `ensurepip --user`，失败则报出具体原因。其他 Python 的检查结果不能替代所选解释器。
- 输入必须是平铺的、已从 AD 导入的单 PCB KiCad 工程副本。原始工程不得直接选择，失败时保留 `_ad2kicad_work` 现场。
- 所有独立顶层留在根目录，只有真实层级子图进入 `00_sheet`；无层级子图时该目录可为空。`.kicad_pro` 保留所有顶层入口，`top_level_sheets.filename` 使用不含目录分量的纯文件名，并修正所属顶层 UUID，不新增包装图改变电路作用域。活动符号库、封装库和 3D 目录各一个放在 `01_Lib`，旧库备份在 `AD_lib_bak`。
- 本次原生加载测试发现，顶层 `filename` 含目录分量时，`pcbnew.LoadBoard` 读取同名 `.kicad_pro` 会触发 `wxFileName::Assign` 断言并挂起。顶层留根目录、子图进入 `00_sheet` 是因此确定的布局；该决定不改变原理图拓扑。
- 现有内嵌模型与可解析文件模型按原字节收集到唯一 `.3dmodels`，校验、去重并保留模型变换。真正缺失的模型继续列出；未取得真实模型时不生成替代模型。其他样本的空模型选择不适用于已有模型的工程。
- 发布前在候选目录执行原生 PCB 加载预检，连同同名 `.kicad_pro` 一起验证，再对每个顶层分别执行网表/ERC，并核验封装加载和文件哈希。整理器检查子进程限时 180 秒，封装索引限时 120 秒，超时报告失败。额外顶层的临时上下文和报告放在审计目录，真实 `00_sheet` 只含 `.kicad_sch`。只检查主图不足以验收多顶层工程。受管文件有未记录的人工修改时拒绝覆盖。
- 某些 AD 导入网名含 XML 1.0 不允许的控制字符。网表解析仅对内存文本进行可逆代换，解析后恢复；原设计及原始 XML 审计文件不因该兼容处理而改写。不能通过删除字符或忽略电路变更使验证通过。

## 工具位置与配置约定

- 缺少 Node.js 时，用户分别选择 `-NodeRoot` 程序目录与 `-NodeConfigRoot` 配置目录。后者保存本流程的 `npmrc`、`cache`、`prefix` 和 `logs`。已有 npmrc 内容保留，环境参数绑定本流程使用的位置，报告记录核对结果。复用系统安装 Node 时，其程序位置可不变，配置使用独立可写目录。
- 缺少 MCP 时，由用户分别选择 `-McpRoot` 程序/仓库 checkout 目录与 `-McpConfigRoot` MCP 自身配置目录。服务以 `node .../dist/index.js --config <McpConfigRoot/config.json>` 使用所选配置，已有配置内容保留。已装 MCP 按已有 Codex 启动参数的 `--config`、本机路径记录或 checkout 的 `config/default-config.json` 发现其配置。Codex 当前用户的 `.codex/config.toml` 只负责登记启动命令、配置路径等连接信息，与 MCP 程序目录及自身配置目录区分。
- 缺少 EasyKiConverter 时，分别选择 `-EasyKiRoot` 程序目录与 `-EasyKiConfigRoot` 配置目录。连同 Node 与 MCP 共六个目录，中文向导集中展示已发现的位置与建议值，可分别修改，支持中文和空格。新装目录应独立、可写；不同工具的目录不可重合或嵌套，配置不能放入待安装的程序目录。全部必需目录预检通过后才安装或注册，取消中止。`-NonInteractive` 模式明确提供项目/CLI 及缺失工具对应的程序、配置参数；缺参报错，不自动选择新的安装位置。
- 全部目录预检通过后，选择保存到 Codex 用户目录 `ad2kicad-tools.json`，后续联网失败仍可按记录重跑；记录只代表位置选择，成功状态以本次环境报告为准。已有健康软件保持安装位置，不强制更新，也不关闭正在使用的 GUI。分发时排除路径记录和本机配置。
- 主工程入口与独立环境入口均支持 `-CodexRoot`，明确指定实际使用的 Codex 用户目录。默认遵循 `CODEX_HOME`，否则使用用户 `.codex`。路径记录、技能、MCP 注册和配置备份在该根目录；该参数不会自动迁移或改变 Codex 应用的数据目录。MCP 自身 `config.json` 继续独立保存于用户选择的位置。
- `-VerifyOnly` 仍可能安装和配置工具、更新路径记录或 MCP 注册，只将工程整理阶段切换为验证已有结果；不是整体只读模式。没有成功整理记录的项目不能使用该开关。
- EasyKiConverter 新的自定义配置使用上游 `--config` 指向 JSON 文件，`--cache-dir` 和 `--log-file` 分别指向所选目录下的缓存、日志。先创建合法 JSON，再从生成的启动器传入参数；复用已有用户配置时保留原内容及缓存设置。上游导出事务恢复备份仍使用默认 AppData 位置，不随这些参数重定向。执行位置、版本、配置模式和启动器应可从环境报告中核对。
- EasyKiConverter 的原生命令健康检查与配置加载探针分别报告。检测到默认 AppData 已有事务恢复数据时，跳过会触发上游恢复的配置探针；报告 `config_smoke.status = skipped_existing_transaction_data`、`passed = null`。这时只可报告已完成的版本/帮助命令、合法 JSON 与启动器参数检查，不能将总环境通过推断为真实自定义配置加载通过。
- EasyKiConverter 是 LCSC/EasyEDA 元件转库工具。环境就绪检查包含它，工程整理不依赖它执行转换；后续补库由用户选择器件并核实映射，安装成功不表示缺失的 3D 模型已补齐。

## 本版报告目录约定

- 所有转换报告、封装索引、独立焊盘清单和修复说明 Markdown 直接放入工程 `_ad2kicad_work`。具体文件清单见 README；日志、快照和各次运行审计继续位于该目录内部。
- 独立运行环境脚本且未指定 `-OutputDirectory` 时，报告默认位于所选工作区 `_ad2kicad_work/environment-...`；工程入口会将其放入该工程的工作目录。环境检查不在工程根目录增加报告。
- 旧版根目录报告先按原字节备份，再迁入工作目录。后续重跑、改库名及失败恢复兼容新旧位置；恢复仍只用于未完成的失败运行，不取消成功工程保护。
- `run_directory` 和 `output_hashes` 的相对路径以工程根目录为基准。报告搬入工作目录不改变路径解析基准，指向映射等产物的受管路径则采用 `_ad2kicad_work/...`。
- 索引的项目库链接改为 `../01_Lib/...`。原生工程文件、库表、`00_sheet` 和 `01_Lib` 的位置不变。

## 来源与验证边界

本轮新增 Node.js 自动检查/安装、npm 独立配置、缺少 Git 的固定提交 ZIP 路径、六目录中文向导、目录预检与重跑记录。本轮实际安装、复用、回归计数、发布校验结果和未完成范围，统一以 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md` 的记录为准，不用历史数据代替。

历史 easyki 版本曾完成两工具中文空格程序/配置目录的真实联网安装：15 项检查，首次 50.552 秒、二次复用 13.579 秒。其 MCP 2.8.2 的 244 工具/155 库查询、EasyKiConverter v3.1.11 官方 ZIP 下载及复用，属于当时具体安装的结果。该次复用了本机 Python 包与复制的 11 个 kicad-happy 技能，不是另一台电脑的全套冷装；以上版本、计数和耗时不作为本轮通用验收标准。

历史 easyki 版本的本机 CS Power 复验 18.420 秒、候选包 speedup 复验 21.317 秒，均退出码 0，活动工程、原始工程、Easy 配置和事务备份哈希保持，根目录转换产物 0；当时 28 项分发回归、3 个 PowerShell AST、9 项 stub 目录控制流及 Python 语法检查的完整证据见 easyki 发布说明。其真实配置加载探针因事务恢复数据跳过。这些数据仅用于追溯旧版本。

clean-root 历史版本的报告目录调整已完成独立回归：干净输入完整入口、旧报告迁移、再次验证、新旧位置索引、PowerShell 入口读取及失败恢复检查均通过。三个现有工程均迁移成功，根目录转换产物为 0，原生资产字节保持，受管哈希通过；详细结果与相对证据路径见 `RELEASE-NOTES-20260929-clean-root.zh-CN.md`。本轮环境改动的验证单独记录在 node-wizard 发布说明。

本版以 `_ad2kicad_work/AD2KICAD_REPORT.json`、`_ad2kicad_work/AD2KICAD_SETUP_RUN.json` 及同目录的映射/模型清单为准。从工程根目录解析报告 `run_directory`；其中 `audit/native-board.json` 和 `audit/native-project/session_report.json` 分别记录原生 PCB 预检与逐顶层证据。发送方样本数量不是通用通过标准。验证比较各顶层元件及引脚网络成员关系，并检查 PCB、映射和模型变换；整理消除库解析问题，保留原电气问题。

clean-root 版本已复验报告移位后的外部模型保护：原始来源不可用时迁移并改库名，4 个模型和 6 个引用完整保留，模型 SHA-256 保持，82 项受管哈希通过，根目录转换产物为 0。保留清单继续按实例 UUID、模型索引和原引用匹配，禁止已收集模型丢失或变更。

本版 ZIP 名称为 `AD2KiCad_Setup_20260929_v3_wizard.zip`，使用显式工具清单，包内附 `DISTRIBUTION-SHA256.json`，包旁附整体 SHA-256。实际文件数、CRC、逐文件哈希、源码/分发/解压一致性与入口复验结果读取本轮发布说明及配套校验文件。旧版 30 文件包的验收不适用于本版。不装入测试工程、缓存、路径记录、个人配置、令牌或第三方软件二进制。

本机隔离目录及移除 Node/Git PATH 的测试不等于另一台电脑冷装；stub 测试不等于真实目录对话框点选。多顶层 GUI 操作、EasyKiConverter 联网器件转换与实际配置加载探针，应分别按本次记录说明范围；探针跳过不能记为通过。CLI 检查只证明所记录的自动检查。本包不承诺任意 AD 工程均可整理，工具配置成功也不代表电气设计或模型已完备。

## 上游项目

- https://github.com/aklofas/kicad-happy
- https://github.com/mixelpixx/KiCAD-MCP-Server
- https://github.com/EasyKiconverter/EasyKiConverter/
- https://nodejs.org/

