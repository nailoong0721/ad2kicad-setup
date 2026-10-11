#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$KiCadCli,
    [string]$WorkspaceRoot = (Get-Location).Path,
    [string]$OutputDirectory = '',
    [string]$CodexRoot = '',
    [string]$McpRoot = '',
    [string]$McpConfigRoot = '',
    [string]$EasyKiRoot = '',
    [string]$EasyKiConfigRoot = '',
    [string]$NodeRoot = '',
    [string]$NodeConfigRoot = '',
    [string]$SmokeSchematic = '',
    [switch]$SkipDependencySmoke,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$global:OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
. (Join-Path $PSScriptRoot 'Setup-AD2KiCadUi.ps1')
$WorkspaceRoot = Get-AD2KiCadFullPath $WorkspaceRoot
if (-not (Test-Path -LiteralPath $WorkspaceRoot -PathType Container)) { throw "工作目录不存在：$WorkspaceRoot" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $WorkspaceRoot ('_ad2kicad_work\environment-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
$OutputDirectory = Get-AD2KiCadFullPath $OutputDirectory
$KiCadCli = Get-AD2KiCadCli -Value $KiCadCli -NonInteractive:$NonInteractive
Write-Host '[1/4] 检查 KiCad、Python 与必要模块…'
$pythonExecutable = Test-AD2KiCadPrerequisites -KiCadCli $KiCadCli

$dependencyRoot = Join-Path $env:USERPROFILE '.codex\workspace-dependencies'
$activationScript = Join-Path $dependencyRoot 'Activate-CodexWorkspace.ps1'
$smokeScript = Join-Path $dependencyRoot 'Test-CodexWorkspace.ps1'
if (Test-Path -LiteralPath $activationScript -PathType Leaf) {
    if (-not $SkipDependencySmoke) {
        if (-not (Test-Path -LiteralPath $smokeScript -PathType Leaf)) { throw "全局依赖检查脚本缺失：$smokeScript" }
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $smokeScript -WorkspaceRoot $WorkspaceRoot -ExitWithCode
        if ($LASTEXITCODE -ne 0) { throw '全局依赖检查未通过，请先修复上述 FAIL 项；未开始安装。' }
    }
    & $activationScript -WorkspaceRoot $WorkspaceRoot
}

$setupScript = Join-Path $PSScriptRoot 'environment_setup.py'
if (-not (Test-Path -LiteralPath $setupScript -PathType Leaf)) { throw "安装包不完整：缺少 $setupScript。请完整解压安装包后重试。" }
$baseArguments = @($setupScript, '--kicad-cli', $KiCadCli, '--workspace', $WorkspaceRoot, '--output', $OutputDirectory)
if ($CodexRoot) { $baseArguments += @('--codex-root', (Get-AD2KiCadFullPath $CodexRoot)) }
$locationValues = @{NodeRoot=$NodeRoot; NodeConfigRoot=$NodeConfigRoot; McpRoot=$McpRoot; McpConfigRoot=$McpConfigRoot; EasyKiRoot=$EasyKiRoot; EasyKiConfigRoot=$EasyKiConfigRoot}
$locationDefinitions = @(
    @{Key='NodeRoot'; Flag='--node-root'; Tool='node'; Field='root'; ProgramKey='NodeRoot'},
    @{Key='NodeConfigRoot'; Flag='--node-config-root'; Tool='node'; Field='config_root'; ProgramKey='NodeRoot'},
    @{Key='McpRoot'; Flag='--mcp-root'; Tool='mcp'; Field='root'; ProgramKey='McpRoot'},
    @{Key='McpConfigRoot'; Flag='--mcp-config-root'; Tool='mcp'; Field='config_root'; ProgramKey='McpRoot'},
    @{Key='EasyKiRoot'; Flag='--easyki-root'; Tool='easyki'; Field='root'; ProgramKey='EasyKiRoot'},
    @{Key='EasyKiConfigRoot'; Flag='--easyki-config-root'; Tool='easyki'; Field='config_root'; ProgramKey='EasyKiRoot'}
)
function Get-EnvironmentArguments {
    param($Chosen)
    $result = @($baseArguments)
    foreach ($definition in $locationDefinitions) {
        $value = $locationValues[$definition.Key]
        if ($Chosen) {
            $chosenValue = [string]$Chosen[$definition.Key]
            $knownValue = [string]$discovery.($definition.Tool).($definition.Field)
            $knownRoot = [string]$discovery.($definition.Tool).root
            $changedRoot = ([string]$Chosen[$definition.ProgramKey]).TrimEnd([char]92) -ine $knownRoot.TrimEnd([char]92)
            if ($chosenValue.TrimEnd([char]92) -ine $knownValue.TrimEnd([char]92) -or ($definition.Field -eq 'config_root' -and $changedRoot)) { $value = $chosenValue }
        }
        if ($value) { $result += @($definition.Flag, (Get-AD2KiCadFullPath $value)) }
    }
    return $result
}

Write-Host '[2/4] 检查已有 Node.js / npm、KiCad MCP、EasyKiConverter…'
$discoveryText = Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList ((Get-EnvironmentArguments) + @('--inspect-only')) -Label '已有工具检查' -Capture
try { $discovery = $discoveryText | ConvertFrom-Json }
catch { throw ("工具检查未返回有效结果；未开始安装。原始输出：" + [Environment]::NewLine + $discoveryText) }
foreach ($tool in @('node', 'mcp', 'easyki')) {
    if ($null -eq $discovery.$tool -or $null -eq $discovery.$tool.installed) { throw "工具检查结果缺少 $tool；请使用完整且版本一致的安装包。" }
    $state = if ($discovery.$tool.installed) { '已找到，将复用' } else { '待安装' }
    Write-Host ("  {0}：{1} {2}" -f $tool, $state, $discovery.$tool.root)
}
$chosen = $null
if ($NonInteractive) {
    Assert-AD2KiCadNonInteractiveChoices -Discovery $discovery
} else {
    $defaultBase = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AD2KiCad'
    $chosen = Show-AD2KiCadDirectoryChoices -Discovery $discovery -BaseDirectory $defaultBase -Validate {
        param($Candidate)
        Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList ((Get-EnvironmentArguments -Chosen $Candidate) + @('--validate-plan')) -Label '所选目录预检' -Capture | Out-Null
    }
}
$arguments = @(Get-EnvironmentArguments -Chosen $chosen)
if ($NonInteractive) {
    Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList ($arguments + @('--validate-plan')) -Label '所选目录预检' -Capture | Out-Null
}

# No installation or configuration occurs until every selected path passes.
[System.IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
$logPath = Join-Path $OutputDirectory 'setup-console.log'
[System.IO.File]::WriteAllText((Join-Path $OutputDirectory 'tool-discovery.json'), ($discovery | ConvertTo-Json -Depth 8), $utf8NoBom)
Write-Host '[3/4] 配置 Node.js / npm、KiCad MCP 与 EasyKiConverter…'
Write-Host "诊断日志：$logPath"
if ($SmokeSchematic) { $arguments += @('--smoke-schematic', (Get-AD2KiCadFullPath $SmokeSchematic)) }
try {
    Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList $arguments -Label 'AD2KiCad 环境配置' -LogPath $logPath
} catch {
    [System.IO.File]::AppendAllText($logPath, ([Environment]::NewLine + '配置失败：' + $_.Exception.Message + [Environment]::NewLine), $utf8NoBom)
    throw ("{0}{1}完整日志：{2}{1}环境报告：{3}{1}请保留诊断目录；修复原因后可再次运行。" -f $_.Exception.Message, [Environment]::NewLine, $logPath, (Join-Path $OutputDirectory 'environment-state.json'))
}
Write-Host '[4/4] 环境配置与验证完成。'
Write-Host ('环境报告：' + (Join-Path $OutputDirectory 'environment-state.json'))
