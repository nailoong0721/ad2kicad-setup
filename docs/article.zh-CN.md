# AD2KiCad：整理 Altium 导入工程，一次配好 KiCad 工具环境

把 Altium Designer 工程导入 KiCad 后，后续维护往往还有不少工作：整理图纸目录、提取符号和封装、修复库关联，以及准备辅助工具的运行环境。AD2KiCad 将这些步骤整合为一个 Windows 工具包，通过中文向导完成路径选择、环境检查和工程整理，让迁移后的工程更容易继续编辑、备份和交接。

> **适用阶段：已经从 Altium 导入并保存为 KiCad 格式之后。** 本文介绍的 AD2KiCad v3 中文向导版接收 KiCad 工程文件；原始 Altium 文件需要先在 KiCad 中导入。

本文对应 **2026-09-29 发布的 v3 中文向导版**，内容更新于 2026-10-03。

## 这个工具能做什么？

AD2KiCad 主要处理两件事：整理导入后的工程，以及配置配套工具。

| 功能 | 实际作用 |
| --- | --- |
| 整理图纸目录 | 顶层原理图保留在工程根目录，真正的层级子图移入 `00_sheet`，同步更新引用。 |
| 建立项目专用库 | 从原理图内嵌定义提取符号，从 PCB 提取封装，统一放入 `01_Lib`，生成项目库表和器件映射。 |
| 处理封装差异 | 检查焊盘等实际数据，保留必要的独立变体，避免仅凭名称合并不同封装。 |
| 整理已有 3D 资源 | 收集能解析的真实模型并更新引用；找不到的模型记录到缺失清单，便于后续补齐。 |
| 配置工具环境 | 检查并按需安装 Node.js/npm、kicad-happy、KiCAD MCP 和 EasyKiConverter，复用可用的已有安装。 |
| 验证并保留记录 | 先在候选目录中检查工程，验证通过后再发布整理结果，保留输入快照、日志和报告。 |

项目库表使用工程相对路径，便于连同工程一起迁移。配套工具各有用途：kicad-happy 提供 KiCad 相关技能，KiCAD MCP 用于连接 AI 客户端与 KiCad 工具，EasyKiConverter 可辅助从 LCSC/EasyEDA 元件资料生成 KiCad 库。

日常使用时，你只需按向导选择目录，无须手工逐项准备这些工具的依赖。工具包本身不附带第三方软件二进制，首次安装缺失组件时会联网获取。

## 开始前需要准备什么？

### 1. 完整的 Windows 版 KiCad

准备 Windows 版 KiCad 9 或 10，并保留随安装提供的 Python 和标准封装库。向导会检查所选 `kicad-cli.exe` 同目录下的 `python.exe`，确认它能够使用 KiCad 的 `pcbnew` 模块；环境引导要求 Python 3.11 或更新版本。

普通用户不需要提前安装 Git、Node.js 或另一套 Python。若 KiCad 组件缺失，应修复所选 KiCad 安装。本文流程使用 Windows PowerShell 5.1；其他操作系统不在本版向导的适用范围内。

### 2. 已导入 KiCad 的工程副本

先通过 KiCad 的 Altium 导入功能完成导入并保存，再复制一份工程供工具处理。开始前关闭正在打开这份副本的 KiCad 窗口。

首次整理时，工程根目录应包含以下三个同名文件：

```text
MyBoard/
├── MyBoard.kicad_pro
├── MyBoard.kicad_pcb
├── MyBoard.kicad_sch
└── 其他原理图.kicad_sch
```

根目录中应恰有一个 `.kicad_pro` 和一个 `.kicad_pcb`，所有原理图起初均平铺在根目录。其他原理图需要是有引用关系的层级子图，或已在项目中登记的独立顶层。原理图还须包含可提取的内嵌符号定义，物理器件位号能够与 PCB 对应。

当前输入范围不包括多个 PCB、预先分散到子目录的原理图、继承符号、缺失或循环子图、未登记的孤立图、不同独立顶层共用同一子图，以及重复或无法匹配的物理位号。遇到这类工程，需要先检查结构或根据诊断处理。

### 3. 可用的网络和写入目录

首次安装或修复需要访问 Node.js 官方下载站、GitHub、npm 和 Python 包索引。工具包、程序目录和配置目录应位于当前用户可写的位置。

环境配置会安装相关技能并登记 Codex 的 MCP 配置。若要在 Codex 中使用这些功能，需自行准备 Codex，并确保工具写入的是该应用实际使用的配置目录；本工具包不负责安装 Codex。

## 快速使用：从解压到打开工程

### 第一步：完整解压工具包

下载并解压 `AD2KiCad_Setup_20260929_v3_wizard.zip`，保持包内文件在同一文件夹。请直接使用新解压的工具目录，避免混用旧版脚本。

准备工作完成后，最短操作顺序是：**双击启动 → 选择 KiCad → 选择工程副本 → 确认库名和目录 → 开始配置 → 查看结果。**

### 第二步：双击启动入口

双击解压目录中的：

```text
Setup-AD2KiCad.cmd
```

向导首先要求选择当前电脑上完整 KiCad 安装中的 `kicad-cli.exe`。它通常位于 KiCad 的 `bin` 文件夹中，请选择自己实际安装的位置。

### 第三步：选择工程副本和库名

选择待整理的 KiCad 工程副本目录，然后输入项目库名。默认名称是 `AD2Kicad`，也可以改成 `MyBoard_Lib` 等容易识别的名称。

库名允许使用 1–64 个英文字母、数字、下划线或连字符，不能使用 `CON` 等 Windows 保留名称。目录支持中文和空格，库名仍需遵守上述字符限制。

### 第四步：确认六个程序与配置目录

向导会集中显示 Node.js、KiCAD MCP 和 EasyKiConverter 的程序及配置位置。可以保留已发现的健康安装，也可以为新工具选择位置。

| 目录 | 存放内容 | 示例路径 |
| --- | --- | --- |
| Node.js 程序 | Node.js 及随附 npm | `D:\EDA\programs\nodejs` |
| Node.js 配置 | 本流程使用的 npm 配置、缓存、全局包和日志 | `D:\EDA\config\nodejs` |
| KiCAD MCP 程序 | 服务源码及构建文件 | `D:\EDA\programs\KiCAD-MCP-Server` |
| KiCAD MCP 配置 | MCP 自身的 `config.json` | `D:\EDA\config\mcp` |
| EasyKi 程序 | EasyKiConverter 程序 | `D:\EDA\programs\EasyKiConverter` |
| EasyKi 配置 / 缓存 | 配置、缓存和日志 | `D:\EDA\config\easyki` |

这些路径仅作示例，可按自己的磁盘情况修改。新装程序应使用可写空目录；六个目录应各自独立，不要相同或互相嵌套，也不要把配置目录放进程序目录。向导会在安装前检查冲突和写权限，未知的非空程序目录不会直接被覆盖。

MCP 自身的配置目录与 Codex 用户配置目录是两个位置。主入口默认使用 `CODEX_HOME` 指定的 Codex 目录；未设置时使用用户主目录下的 `.codex`。使用自定义 Codex 目录的用户，可通过后文的 `-CodexRoot` 参数明确指定。

### 第五步：点击“开始配置”

目录检查通过后，程序按需下载和配置依赖，再整理工程。Node.js 缺失时会选择官方可用的受支持 LTS 版本，校验下载文件的 SHA-256 后安装；缺少 Git 时，也能通过官方仓库的固定提交源码 ZIP 完成获取。

程序不会修改系统或用户的永久 PATH。修改 Codex 配置前，会将旧配置备份到该 Codex 目录下的 `backups/ad2kicad`。路径选择也会保存，便于失败后重新检查并继续运行。

等待窗口显示“配置与验证全部通过”。安装时间受网络、已有环境和工程规模影响，请以实际运行结果为准。

### 第六步：打开工程并检查

完成后，在 KiCad 中打开工程根目录的 `.kicad_pro`，检查原理图、PCB、符号及封装关联。如果需要使用本次新登记的 MCP，请重载连接或重启 Codex。

建议同时查看 `_ad2kicad_work/AD2KICAD_SETUP_RUN.json`，确认本次记录的 `passed` 为 `true`。工程详细结果位于 `AD2KICAD_REPORT.json`，可进一步检查 `verified`、`native_board_validation`、`top_level_sheets` 和 `circuit_counts`。这些文件均在工程的 `_ad2kicad_work` 中。

## 整理后会得到什么？

以库名 `MyBoard_Lib` 为例，主要目录如下。实际图纸数量和名称随工程而变化：

```text
MyBoard/
├── MyBoard.kicad_pro
├── MyBoard.kicad_pcb
├── MyBoard.kicad_sch
├── 其他独立顶层.kicad_sch          （如有）
├── sym-lib-table
├── fp-lib-table
├── 00_sheet/                     （仅真正的层级子图）
│   └── 子图.kicad_sch
├── 01_Lib/
│   ├── MyBoard_Lib.kicad_sym
│   ├── MyBoard_Lib.pretty/
│   ├── MyBoard_Lib.3dmodels/
│   └── AD_lib_bak/
└── _ad2kicad_work/
    ├── AD2KICAD_SETUP_RUN.json
    ├── AD2KICAD_REPORT.json
    ├── AD2KICAD_MAPPING.json
    ├── AD2KICAD_MISSING_3D.json
    └── 日志、输入快照及逐次运行记录
```

独立顶层原理图继续留在根目录，保留原有电路关系；没有层级子图时，`00_sheet` 可以为空。`.pretty` 是封装库目录，`.3dmodels` 是模型目录，旧库及必要的封装变体保存在 `AD_lib_bak`。

`AD2KICAD_MAPPING.json` 记录器件映射，`AD2KICAD_MISSING_3D.json` 记录待补模型。3D 目录为空可能只是原设计没有可收集的模型，不能据此认为模型已经齐全。

日志和报告集中在 `_ad2kicad_work`，便于保持工程根目录清晰。报告中的 `run_directory` 等相对路径仍以工程根目录为基准。确认结果和备份之前，请保留这个工作目录，尤其不要在失败后直接删除它。

## “验证通过”意味着什么？

工程整理会在隔离候选中进行原生 PCB 加载、封装加载，以及各独立顶层的网表导出和 ERC 检查，并比较整理前后的器件、网络和 PCB 数据。通过这些检查后，才将结果发布到工程副本。

因此，“通过”表示本次整理满足工具的库关联与数据保持检查。原工程已有的电气 ERC 问题仍需继续处理，布线、制造规则和电路功能也需要设计者复核。收集已有 3D 模型不会生成缺失模型；安装 EasyKiConverter 也不会自动完成补库或补模型。

根据 **2026-09-29 的发布验收记录**，该版本在 Windows x64、KiCad 10.0.4 环境中通过了 82 项 Python 回归和 29 项 PowerShell 5.1 前端检查，并在隔离目录、受限 PATH 条件下完成了缺少 Node/Git 时的实际环境安装。前端检查部分使用了替身后端，真实安装另有独立记录。

这些记录的范围是本机发行验收：复用了已有 KiCad 及其 Python 包，未在另一台全新电脑或 ARM64 实机上验收；pip 缺失修复分支通过模拟回归检查。EasyKiConverter 的实际自定义配置加载探针因已有恢复备份而跳过，也未执行联网器件转换。具体情况见工具包内的 `RELEASE-NOTES-20260929-node-wizard.zh-CN.md`。

## 常见问题

### 可以直接选择 Altium 原始工程吗？

请先在 KiCad 中导入并保存。本文工具包的主入口处理 `.kicad_pro`、`.kicad_pcb` 和 `.kicad_sch`，适用于导入后的整理阶段。

### 找不到 KiCad Python 或标准封装库怎么办？

修复完整 KiCad 安装，再选择该安装中的 `bin/kicad-cli.exe`。仅复制一个 CLI 可执行文件，或另装一套普通 Python，不能替代工具所需的 KiCad 运行环境。

### 下载失败后需要从头配置吗？

先检查相关官方站点和包索引能否访问，再重新运行入口。工具会重新检查保存的路径并复用健康安装。失败详情可在本次环境诊断目录的 `setup-console.log`、`environment-state.json` 中查看；工程阶段还会记录 `project-console.log`。

### 目录提示冲突、非空或不可写怎么办？

为各程序和配置重新选择独立的可写位置。若旧程序损坏，可以为新安装选择空目录；不要删除不明文件来强行通过检查。

### 重跑会覆盖后来手工修改的工程吗？

工具会核对受管文件。发现未记录的人工修改时，会拒绝直接覆盖。遇到提示应保留工程、快照和日志，先核对差异；不要删掉报告来绕过保护。

### 配置完成后，Codex 里为什么看不到 MCP？

先重载 MCP 连接或重启 Codex，再核对写入的 Codex 配置目录是否正确。安装过程中的独立协议检查通过，并不表示当前会话已经加载了新工具。

### 双击后仍无法定位问题怎么办？

在解压目录打开 PowerShell，运行下面的命令并查看输出。该命令会启动同一配置流程：

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '.\Setup-AD2KiCad.ps1'
```

## 进阶使用：在命令行预填参数

通常使用中文向导即可。如果需要指定工程、库名或 Codex 配置根目录，可以在解压目录执行以下命令，替换为自己的真实路径：

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '.\Setup-AD2KiCad.ps1' -ProjectRoot 'E:\Projects\MyBoard-copy' -KiCadCli 'D:\KiCad\bin\kicad-cli.exe' -LibraryName 'MyBoard_Lib' -CodexRoot 'E:\CodexProfile'
```

未预填的工具目录仍可在向导中选择。`-CodexRoot` 仅指定本次写入配置的位置，不会更改 Codex 应用自身的数据目录，必须与应用实际使用的位置一致。

自动化运行可以添加 `-NonInteractive`，并明确提供工程、CLI 和首次缺失工具的程序与配置路径。六个路径参数分别是 `-NodeRoot`、`-NodeConfigRoot`、`-McpRoot`、`-McpConfigRoot`、`-EasyKiRoot` 和 `-EasyKiConfigRoot`；完整示例见包内 `README-TRANSFER.zh-CN.md`。

`-VerifyOnly` 只把工程整理阶段限制为复验已有结果，仍会进行环境检查，并可能安装或配置工具、更新路径记录及 MCP 注册。它不是整个入口的只读开关，未成功整理过的工程也不能使用该参数。

## 下载资源

[下载 AD2KiCad v3 中文向导版](https://nailoong0721.com/downloads/AD2KiCad_Setup_20260929_v3_wizard.zip)

- 文件大小：153,117 字节，约 150 KiB。
- SHA-256：`B09042248816493F1606FE0ABFCDD68562AC8273A388BAA85358326E5BDC591F`。

## GitHub

[查看 AD2KiCad v3 中文向导 项目仓库](https://github.com/nailoong0721/ad2kicad-setup)。仓库包含本文与对应工具包。

## 相关项目

AD2KiCad 的配套环境使用了 [Node.js](https://nodejs.org/)、[kicad-happy](https://github.com/aklofas/kicad-happy)、[KiCAD MCP Server](https://github.com/mixelpixx/KiCAD-MCP-Server) 和 [EasyKiConverter](https://github.com/EasyKiconverter/EasyKiConverter/) 等第三方项目。各项目的许可、运行要求和后续更新以其官方仓库为准。
