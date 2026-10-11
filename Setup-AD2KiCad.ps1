#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$KiCadCli,
    [string]$LibraryName,
    [string]$CodexRoot,
    [string]$McpRoot,
    [string]$McpConfigRoot,
    [string]$EasyKiRoot,
    [string]$EasyKiConfigRoot,
    [string]$NodeRoot,
    [string]$NodeConfigRoot,
    [switch]$NonInteractive,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$global:OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$started = Get-Date
. (Join-Path $PSScriptRoot 'Setup-AD2KiCadUi.ps1')

try {
    Write-Host 'AD2KiCad 一键配置'
    Write-Host '请先完整解压安装包，并选择已经导入 KiCad 的项目副本。'
    $KiCadCli = Get-AD2KiCadCli -Value $KiCadCli -NonInteractive:$NonInteractive
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        if ($NonInteractive) { throw '非交互模式必须提供 -ProjectRoot，指定待配置的项目副本目录。' }
        $ProjectRoot = Select-AD2KiCadDirectory -Description '选择已导入 KiCad 的项目副本（例如 test 或 speedup）；不要选择原始项目' -InitialDirectory (Split-Path -Parent $PSScriptRoot) -ExistingOnly
        if (-not $ProjectRoot) { throw [System.OperationCanceledException]::new('已取消选择项目；未开始配置。') }
    }
    $ProjectRoot = Get-AD2KiCadFullPath $ProjectRoot
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) { throw "项目目录不存在：$ProjectRoot" }
    if ([System.IO.Path]::GetFileName($ProjectRoot.TrimEnd([char]92)) -ieq 'AD2Kicad_original_prj') { throw '原始项目受到保护。请复制项目后，选择 test 或 speedup 等副本目录。' }
    $existingReport = Join-Path $ProjectRoot '_ad2kicad_work\AD2KICAD_REPORT.json'
    if (-not (Test-Path -LiteralPath $existingReport -PathType Leaf)) { $existingReport = Join-Path $ProjectRoot 'AD2KICAD_REPORT.json' }
    $hasReport = Test-Path -LiteralPath $existingReport -PathType Leaf
    $reportData = $null
    if ($hasReport) {
        try { $reportData = Get-Content -LiteralPath $existingReport -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { throw "已有项目报告无法读取，请保留原文件并检查：$existingReport" }
    } else {
        if ($VerifyOnly) { throw '此项目还没有成功配置记录，不能仅验证。请先不带 -VerifyOnly 完成一次配置。' }
        $files = @(Get-ChildItem -LiteralPath $ProjectRoot -File)
        $projects = @($files | Where-Object { $_.Extension -ieq '.kicad_pro' })
        $boards = @($files | Where-Object { $_.Extension -ieq '.kicad_pcb' })
        if ($projects.Count -ne 1 -or $boards.Count -ne 1 -or $projects[0].BaseName -ine $boards[0].BaseName -or -not (Test-Path -LiteralPath (Join-Path $ProjectRoot ($projects[0].BaseName + '.kicad_sch')) -PathType Leaf)) {
            throw '所选目录不是完整的 KiCad 项目副本：应有同名的 .kicad_pro、.kicad_pcb 和主 .kicad_sch 文件。请先在 KiCad 中导入 Altium 项目。'
        }
    }
    $askLibraryName = -not $NonInteractive -and [string]::IsNullOrWhiteSpace($LibraryName)
    if ([string]::IsNullOrWhiteSpace($LibraryName)) {
        $LibraryName = 'AD2Kicad'
        if ($reportData -and $reportData.library_name) { $LibraryName = [string]$reportData.library_name }
    }
    if ($askLibraryName) {
        Add-Type -AssemblyName Microsoft.VisualBasic
        $LibraryName = [Microsoft.VisualBasic.Interaction]::InputBox('输入项目库名称；保留默认值即可。支持 1–64 个英文字母、数字、下划线或连字符。', 'AD2KiCad — 项目库名称', $LibraryName)
        if ([string]::IsNullOrWhiteSpace($LibraryName)) { throw [System.OperationCanceledException]::new('已取消设置库名；未开始配置。') }
    }
    if ($LibraryName -cnotmatch '^[A-Za-z0-9_-]{1,64}$' -or $LibraryName -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { throw '库名只能使用 1–64 个英文字母、数字、下划线或连字符，不能使用 Windows 保留名称。' }
    foreach ($requiredFile in @('Setup-AD2KiCadEnvironment.ps1', 'environment_setup.py', 'organize_project.py', 'index_footprints.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $requiredFile) -PathType Leaf)) { throw "安装包不完整：缺少 $requiredFile。请完整解压后重试。" }
    }
    Write-Host ("项目：{0}{1}KiCad：{2}{1}库名：{3}" -f $ProjectRoot, [Environment]::NewLine, $KiCadCli, $LibraryName)
    if ($VerifyOnly) { Write-Host '验证模式：仍检查和配置工具环境；项目整理器仅验证已配置的项目。' }
    $audit = Join-Path $ProjectRoot ('_ad2kicad_work\environment-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $environmentArguments = @{KiCadCli=$KiCadCli; WorkspaceRoot=(Split-Path -Parent $ProjectRoot); OutputDirectory=$audit; NonInteractive=$NonInteractive}
    foreach ($key in @('CodexRoot', 'McpRoot', 'McpConfigRoot', 'EasyKiRoot', 'EasyKiConfigRoot', 'NodeRoot', 'NodeConfigRoot')) {
        $value = Get-Variable -Name $key -ValueOnly
        if ($value) { $environmentArguments[$key] = $value }
    }
    & (Join-Path $PSScriptRoot 'Setup-AD2KiCadEnvironment.ps1') @environmentArguments
    $pythonExecutable = Join-Path ([System.IO.Path]::GetDirectoryName($KiCadCli)) 'python.exe'
    $projectLog = Join-Path $audit 'project-console.log'
    Write-Host '正在整理并验证项目，请勿关闭窗口…'
    $arguments = @((Join-Path $PSScriptRoot 'organize_project.py'), '--project', $ProjectRoot, '--kicad-cli', $KiCadCli, ('--library-name=' + $LibraryName))
    if ($VerifyOnly) { $arguments += '--verify-only' }
    Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList $arguments -Label '项目整理与验证' -LogPath $projectLog
    Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList @((Join-Path $PSScriptRoot 'index_footprints.py'), '--project', $ProjectRoot) -Label '封装索引验证' -LogPath $projectLog
    $summary = [ordered]@{
        passed = $true
        project = $ProjectRoot
        kicad_cli = $KiCadCli
        library_name = $LibraryName
        verify_only = [bool]$VerifyOnly
        seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        environment_report = (Join-Path $audit 'environment-state.json')
    }
    $workDirectory = Join-Path $ProjectRoot '_ad2kicad_work'
    [System.IO.Directory]::CreateDirectory($workDirectory) | Out-Null
    $summaryPath = Join-Path $workDirectory 'AD2KICAD_SETUP_RUN.json'
    [System.IO.File]::WriteAllText($summaryPath, ($summary | ConvertTo-Json -Depth 4), $utf8NoBom)
    Write-Host ("配置与验证全部通过，用时 {0} 秒。" -f $summary.seconds) -ForegroundColor Green
    Write-Host "结果报告：$summaryPath"
    Write-Host '下一步：在 KiCad 中打开该项目；如正在运行 Codex，请重启后使用新配置的 MCP。'
} catch [System.OperationCanceledException] {
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    exit 2
} catch {
    Write-Host '配置未完成。' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($audit -and (Test-Path -LiteralPath $audit -PathType Container)) { Write-Host "请保留诊断目录和项目恢复输入：$audit" }
    exit 1
}
