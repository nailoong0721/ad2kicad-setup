# AD2KiCad v3：Node.js 与中文配置向导

发布日期：2026-09-29。工具包：`AD2KiCad_Setup_20260929_v3_wizard.zip`。工程数据格式继续使用 schema v2；本轮主要改进环境安装和使用流程。

## 普通用户的操作

1. 自行安装完整 Windows 版 KiCad，保留 Python 和标准库；准备已从 Altium 导入的工程副本。
2. 完整解压工具包，双击 `Setup-AD2KiCad.cmd`，确认 KiCad、工程副本与库名。
3. 在一个中文窗口中确认或修改六个程序/配置目录，点击“开始配置”。完成后查看绿色成功提示，再打开 KiCad 项目；新增 MCP 需重启 Codex 后使用。

无需提前安装 Git、Node.js 或另一套 Python。首次缺失安装需要网络；工具包不内置第三方二进制。不是将未导入的 Altium 原始文件直接一键转换成 KiCad 的工具。

## 改进内容

- Node.js 同时检查版本、LTS 状态、Windows 架构、实际可执行性和 npm；缺失时选择官方可用 LTS ZIP，核对官方 SHA-256，暂存检查通过后发布至所选目录。支持 Windows x64/ARM64 的选择规则，拒绝覆盖未知非空目录。
- npm 的 `npmrc`、缓存、日志和全局包前缀使用独立配置目录，并实际运行 `npm config get` 核验；已有配置字节保持。系统 PATH 不修改，MCP 登记使用 Node 绝对路径。
- 程序与配置目录均可自定义。默认目录可统一调整，已安装工具可复用；支持中文、空格，并实测含方括号、`&` 和 `#` 的配置路径。目录冲突、权限、缺失参数、KiCad/Python/pcbnew 和项目基本完整性在昂贵操作前检查。
- Git 不是必需项：缺 Git 时从两个官方仓库获取当前提交 SHA，再下载固定提交的源码 ZIP；记录本地哈希。仅省略 kicad-happy 中严格验证的内部技能链接别名，真实技能内容仍完整安装。不是上游签名验证。
- MCP 的已知纯 TypeScript 构建通过 Node 绝对路径直接调用编译器，避开 npm 在含 `&` 目录下生成的批处理脚本问题。检查根构建脚本及锁文件后才跳过生命周期；未来上游若引入运行时安装钩子，明确停止提示升级工具包。开发用 esbuild/Vitest 安装不属于该服务配置目标。
- 缺 pip 时尝试所选 KiCad Python 的 `ensurepip --user`，再安装缺少包。路径先记录，失败重跑重新探测；旧的失效路径不会阻止重新选择。Windows 目录发布的权限错误有限重试，最终失败保留已验证暂存目录。
- 中文进度、汇总目录窗口、取消反馈、明确退出码及日志；保留库名输入。`-NonInteractive` 要求显式补齐缺失目录；`-CodexRoot` 可选择 Codex 配置根。`-VerifyOnly` 仍执行环境配置，仅限制工程整理步骤为验证。

## 本轮实测

- 当前 Windows x64 机器上，将测试子进程 PATH 限制为 Windows 系统目录，确认无法找到 Node 或 Git；使用独立 Codex 根及六个新目录，真实安装 Node.js **v24.21.0** / npm **11.19.0**、kicad-happy 11 个技能、KiCAD MCP **2.8.2** 和 EasyKiConverter **3.1.11**。MCP 初始化、244 项工具目录和 155 个封装库只读查询通过。
- 完整环境新装成功步骤耗时 **69.557 秒**。修复路径大小写造成的重复保存后，再次运行耗时 **10.775 秒**（外部测量），没有重装 Node、重建 MCP 或修改注册及配置文件。这是本机当前网络及既有 KiCad Python 包条件下的测量，不是其他电脑的耗时承诺。
- **82 项 Python 回归通过**：Node/npm、下载校验、发布重试、目录冲突、失败重跑、Git/ZIP、MCP 登记、上游构建变更防护及既有工程兼容。
- **29 项 PowerShell 5.1 前端检查通过**：AST、中文脚本 BOM、真实 KiCad 10.0.4/Python 3.11.5/pcbnew、参数传递、失败退出码、主入口控制流及实际 WinForms 接受/取消事件。已查看窗口渲染截图；安装后端在这些前端控制流测试中使用 stub，真实安装证据来自上面的独立完整实测。

测试发现并修复的源码链接别名、Windows 发布拒绝访问、npm 特殊字符路径和路径记录大小写问题，均保留了失败证据，不将失败尝试计为通过。

## 发布验收与证据

发行包使用 37 个工具/说明/测试文件的显式清单，加上 `DISTRIBUTION-SHA256.json` 共 38 个文件。发布前从候选 ZIP 解压，执行 Python 与 PowerShell 回归，再用完整 PowerShell 入口对已整理的 speedup 工程复验，核对原始工程、活动工程及用户配置哈希。整体 ZIP 的校验值以旁边 `.zip.sha256.txt` 为准。旧 ZIP 和分发目录升级前快照保留。

维护工作区中的完整证据集中在 `AD2Kicad_original_prj_test/_ad2kicad_work/node_wizard_20260929/`：

- `cold-acceptance.json`：完整真实安装和重跑、源码提交、MCP 操作、配置字节保持。
- `node-backend-real-acceptance.json`：Node 官方安装与 npm 配置独立实测。
- `frontend/final/frontend-test-report.json`、`directory-dialog.png`：29 项前端检查及窗口渲染。
- `candidate-verification.json`：候选解压后的回归、完整工程入口与受保护文件哈希。
- `archive-validation.json`：最终 ZIP 清单、CRC、解压 SHA-256、源码一致性。

这些维护证据不装入转发 ZIP，避免包含工程、绝对路径、缓存或个人设置。转发包中的说明及清单可独立阅读。

## 验证边界

本次不是另一台全新电脑的冷机测试：虽然 Node、Git、技能、MCP 和 EasyKi 从隔离条件验证，仍复用了本机已安装的 KiCad 及其 Python 包；pip 缺失修复分支为回归模拟。ARM64 没有实体机器验收。EasyKiConverter 自定义配置加载探针因为本机已有事务恢复备份而明确跳过，状态为 `skipped_existing_transaction_data`，不声称实际加载通过；未执行联网器件转换。工程原有电气 ERC 与缺失 3D 模型不由环境安装修复。
