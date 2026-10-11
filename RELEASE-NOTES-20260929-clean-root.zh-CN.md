# 2026-09-29 转换产物归档版

本版将转换过程产物集中到工程 `_ad2kicad_work`，保持原生工程与项目库的既定位置。分发名称为 `AD2KiCad_Setup_20260929_v2_clean_root.zip`。本版自动回归及三个现有工程的产物迁移已通过，结果如下；此前 CS Power 故障修复证据另存于 `RELEASE-NOTES-20260929.zh-CN.md`。

## 本版目录约定

以下产物直接位于 `工程/_ad2kicad_work/`：

| 文件 | 用途 |
| --- | --- |
| `AD2KICAD_REPORT.json` | 整理结果、验证项、运行目录和受管文件哈希 |
| `AD2KICAD_MAPPING.json` | 符号、封装及模型引用映射 |
| `AD2KICAD_MISSING_3D.json` | 待补模型清单 |
| `AD2KICAD_FOOTPRINT_VARIANTS.json` | 封装变体与保留策略 |
| `AD2KICAD_FOOTPRINT_INDEX.md` | 封装索引，库链接从工作目录使用 `../01_Lib/...` |
| `AD2KICAD_STANDALONE_PADS.json` | 独立焊盘清单 |
| `AD2KICAD_SETUP_RUN.json` | 完整入口运行状态 |
| 本工具生成的修复说明 Markdown | 本工程的修复过程和证据说明 |

输入快照、环境检查、日志、备份和逐次运行的审计目录也继续位于 `_ad2kicad_work` 内。原生 `.kicad_sch`、`.kicad_pcb`、`.kicad_pro`、`sym-lib-table`、`fp-lib-table`、`00_sheet` 和 `01_Lib` 不因本次产物归档而移动；独立顶层仍在根目录，真实层级子图仍在 `00_sheet`。

## 升级与路径基准

- 新版自动识别旧根目录报告，先按原字节备份，再迁入工作目录。重新运行、改库名和失败恢复兼容新旧报告位置，不取消成功项目的恢复防护。
- `run_directory` 和 `output_hashes` 的相对路径始终以工程根目录为基准。例如受管映射报告采用 `_ad2kicad_work/AD2KICAD_MAPPING.json`，模型仍采用 `01_Lib/...`。不能因报告换了目录就重复拼接 `_ad2kicad_work`。
- 从工程根目录解析 `run_directory` 后，在其 `audit/native-board.json`、`audit/native-board.log` 和 `audit/native-project/session_report.json` 查看原生板加载及逐顶层验收证据。
- 已收集模型的字节、引用和变换保护继续适用；本版已复验报告移位后改库名，即使原始外部模型来源不可用，已收集模型仍保持完整。

## 本次自动验收

| 验收项 | 实际结果 |
| --- | --- |
| 干净输入完整入口 | 退出码 0，45.521 秒；根目录转换产物为 0，报告哈希和 69 个索引链接通过，4 个模型及 6 个引用保留 |
| 旧报告迁移与再次只验证 | 迁移 10.337 秒，再次 `VerifyOnly` 11.270 秒；8 份旧报告原字节备份可核验，原生资产字节保持，根目录转换产物为 0 |
| 外部模型来源不可用时改库名 | 53.367 秒；4 个模型、6 个引用完整，模型 SHA-256 保持，82 项受管哈希及 69 个链接通过，同名工程原生板加载成功，根目录转换产物为 0 |
| 索引兼容 | CS Power 新旧报告位置均通过 69 个链接、13 个独立焊盘检查；旧层级工程通过 164 个链接、77 个独立焊盘检查，原文件保持 |
| PowerShell 5.1 入口 | 脚本解析、新旧报告读取通过；运行结果只写入工作目录 |
| 迁移回滚与失败恢复 | 嵌套报告备份、发布和回滚通过，工作历史保持；不同内容的新旧残留报告均归档，恢复再次执行保持幂等；新旧成功标记均拒绝失败恢复 |

三个现有工程也已完成迁移，均退出成功，根目录转换产物为 0，全部受管哈希通过：

| 工程 | 耗时 | 迁移产物 | 字节保持的原生资产 |
| --- | ---: | ---: | ---: |
| CS Power | 16.079 秒 | 8 项 | 110 项 |
| 原始导入测试副本 | 19.541 秒 | 7 项 | 265 项 |
| 原始导入快速配置副本 | 19.631 秒 | 7 项 | 265 项 |

这些数量和耗时只描述本次样本，不作为接收方工程的固定验收门槛。

## 证据位置

每个工程的主要结果为 `_ad2kicad_work/AD2KICAD_REPORT.json` 和 `_ad2kicad_work/AD2KICAD_SETUP_RUN.json`。旧报告原字节备份位于其 `_ad2kicad_work/artifact-relocation-*/legacy-root/`。原生审计仍按报告的项目相对 `run_directory` 定位。

发送方调试记录以 `cs_power_repair` 为记录根目录，使用以下相对路径：

- `clean-root-layout-verified/result.json`：干净输入、旧报告迁移和再次只验证。
- `external-artifact-migration-result.json`：外部模型来源不可用时的迁移改库名回归。
- `artifact-index-tests.json`、`artifact-powershell-tests.json`：索引与 PowerShell 入口兼容。
- `artifact-recovery-fixtures-1790650771074191000/test-result.json`：报告回滚、失败恢复和成功标记保护；恢复临时文件也只位于工作目录内。
- `active-artifact-migration/result.json`：三个现有工程的迁移结果。

这些内部测试记录不随工具包发送；接收方应以自己的工程报告验收。

## 分发与验证边界

发布流程将生成 `DISTRIBUTION-SHA256.json` 与 ZIP 整体 SHA-256，校验清单、ZIP CRC、解压后的逐文件 SHA-256、维护源一致性和 Python/PowerShell 语法，再运行解压入口复验。以最终清单确定文件数量，不沿用旧包数量或校验值。工具包只包含工具与说明，不包含工程、运行缓存、个人配置或认证信息。

- 其他电脑冷机安装：未验证。
- GUI 人工交互：未验证。
