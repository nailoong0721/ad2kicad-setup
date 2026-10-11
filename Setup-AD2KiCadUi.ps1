#requires -Version 5.1
# Shared UI and preflight helpers. Dot-sourcing this file makes no changes.
function Get-AD2KiCadFullPath {
    param([Parameter(Mandatory=$true)][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { throw '目录不能为空。' }
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath([Environment]::ExpandEnvironmentVariables($Value.Trim()))
}

function Select-AD2KiCadDirectory {
    param([string]$Description, [string]$InitialDirectory, [switch]$ExistingOnly)
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    try {
        $picker.Description = $Description
        $picker.ShowNewFolderButton = -not $ExistingOnly
        if ($InitialDirectory -and (Test-Path -LiteralPath $InitialDirectory -PathType Container)) { $picker.SelectedPath = $InitialDirectory }
        if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return $null }
        return $picker.SelectedPath
    } finally { $picker.Dispose() }
}

function Get-AD2KiCadCli {
    param([string]$Value, [switch]$NonInteractive)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        if ($NonInteractive) { throw '非交互模式必须提供 -KiCadCli，指定 kicad-cli.exe 的完整路径。' }
        # The known path is only preselected; the user confirms the executable.
        $knownCli = 'D:\Kicad\bin\kicad-cli.exe'
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        try {
            $picker.Title = '确认或选择 KiCad：bin 文件夹中的 kicad-cli.exe'
            $picker.Filter = 'KiCad 命令行程序 (kicad-cli.exe)|kicad-cli.exe'
            $picker.InitialDirectory = [Environment]::GetFolderPath('ProgramFiles')
            if (Test-Path -LiteralPath $knownCli -PathType Leaf) {
                $picker.InitialDirectory = [System.IO.Path]::GetDirectoryName($knownCli)
                $picker.FileName = $knownCli
            }
            $picker.CheckFileExists = $true
            if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw [System.OperationCanceledException]::new('已取消选择 KiCad；未开始配置。') }
            $Value = $picker.FileName
        } finally { $picker.Dispose() }
    }
    $Value = Get-AD2KiCadFullPath $Value
    if ([System.IO.Path]::GetFileName($Value) -ine 'kicad-cli.exe' -or -not (Test-Path -LiteralPath $Value -PathType Leaf)) { throw "找不到有效的 kicad-cli.exe：$Value" }
    return $Value
}

function Invoke-AD2KiCadNative {
    param([string]$Executable, [string[]]$ArgumentList, [string]$Label, [string]$LogPath, [switch]$Capture)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "找不到程序：$Executable" }
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    $previousPreference = $ErrorActionPreference
    $nativeExitCode = $null
    try {
        # PowerShell 5.1 wraps native stderr in ErrorRecord even on success.
        # Keep that output, then decide success from the actual process exit code.
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = $null
        $lines = @(& $Executable @ArgumentList 2>&1 | ForEach-Object {
            $line = [string]$_
            if ($LogPath) { [System.IO.File]::AppendAllText($LogPath, ($line + [Environment]::NewLine), $encoding) }
            if (-not $Capture) { Write-Host $line }
            $line
        })
        $nativeExitCode = $global:LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($null -eq $nativeExitCode -or $nativeExitCode -ne 0) {
        $detail = ($lines | Select-Object -Last 12) -join [Environment]::NewLine
        throw ("{0}失败（退出码：{1}）。`n{2}" -f $Label, $nativeExitCode, $detail)
    }
    if ($Capture) { return ($lines -join [Environment]::NewLine) }
}

function Test-AD2KiCadPrerequisites {
    param([string]$KiCadCli)
    $pythonExecutable = Join-Path ([System.IO.Path]::GetDirectoryName($KiCadCli)) 'python.exe'
    if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) { throw 'KiCad 安装不完整：bin 文件夹内缺少 python.exe。请使用完整的 KiCad Windows 安装包修复安装。' }
    $version = Invoke-AD2KiCadNative -Executable $KiCadCli -ArgumentList @('--version') -Label 'KiCad 版本检查' -Capture
    if ($version -notmatch '\d+\.\d+') { throw "KiCad 未返回有效版本号：$version" }
    $probe = "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ is required'; import tomllib, pcbnew; print(sys.version.split()[0])"
    $pythonVersion = Invoke-AD2KiCadNative -Executable $pythonExecutable -ArgumentList @('-c', $probe) -Label 'KiCad Python、tomllib 和 pcbnew 检查' -Capture
    Write-Host ("KiCad：{0}；Python：{1}；必要模块检查通过。" -f $version.Trim(), $pythonVersion.Trim())
    return $pythonExecutable
}

function Get-AD2KiCadDirectoryChoices {
    param($Discovery, [string]$BaseDirectory)
    $definitions = @(
        @{Key='NodeRoot'; Tool='node'; Field='root'; Relative='programs\nodejs'},
        @{Key='NodeConfigRoot'; Tool='node'; Field='config_root'; Relative='config\nodejs'},
        @{Key='McpRoot'; Tool='mcp'; Field='root'; Relative='programs\KiCAD-MCP-Server'},
        @{Key='McpConfigRoot'; Tool='mcp'; Field='config_root'; Relative='config\kicad-mcp'},
        @{Key='EasyKiRoot'; Tool='easyki'; Field='root'; Relative='programs\EasyKiConverter'},
        @{Key='EasyKiConfigRoot'; Tool='easyki'; Field='config_root'; Relative='config\easyki'}
    )
    $choices = [ordered]@{}
    foreach ($definition in $definitions) {
        $value = [string]$Discovery.($definition.Tool).($definition.Field)
        if (-not $value) { $value = Join-Path $BaseDirectory $definition.Relative }
        $choices[$definition.Key] = $value
    }
    return $choices
}

function Assert-AD2KiCadNonInteractiveChoices {
    param($Discovery)
    $missing = @()
    foreach ($tool in @(@{Name='node'; Root='NodeRoot'; Config='NodeConfigRoot'}, @{Name='mcp'; Root='McpRoot'; Config='McpConfigRoot'}, @{Name='easyki'; Root='EasyKiRoot'; Config='EasyKiConfigRoot'})) {
        if ($Discovery.($tool.Name).needs_directory) { $missing += ('-' + $tool.Root) }
        if (-not $Discovery.($tool.Name).config_root) { $missing += ('-' + $tool.Config) }
    }
    if ($missing.Count) { throw ('非交互模式缺少目录参数：' + ($missing -join '、') + '。请指定完整目录，或双击 Setup-AD2KiCad.cmd 使用目录向导。') }
}

function Show-AD2KiCadDirectoryChoices {
    param($Discovery, [string]$BaseDirectory, [scriptblock]$Validate)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $choices = Get-AD2KiCadDirectoryChoices -Discovery $Discovery -BaseDirectory $BaseDirectory
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'AD2KiCad 一键配置 — 确认目录'
    $form.StartPosition = 'CenterScreen'
    $form.ClientSize = New-Object System.Drawing.Size(830, 490)
    $form.MinimumSize = New-Object System.Drawing.Size(846, 529)
    $form.AutoScaleMode = 'Dpi'
    $form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
    $form.MaximizeBox = $false
    $tip = New-Object System.Windows.Forms.ToolTip
    try {
        $intro = New-Object System.Windows.Forms.Label
        $intro.SetBounds(16, 12, 798, 42)
        $intro.Text = "已找到的程序会自动复用；缺少的程序会下载并安装。`n可保留推荐目录，也可直接修改各项路径；配置目录存放设置、缓存和日志。"
        $form.Controls.Add($intro)
        $baseLabel = New-Object System.Windows.Forms.Label
        $baseLabel.SetBounds(16, 61, 128, 24)
        $baseLabel.Text = '新工具默认位置'
        $form.Controls.Add($baseLabel)
        $baseBox = New-Object System.Windows.Forms.TextBox
        $baseBox.SetBounds(148, 57, 542, 27)
        $baseBox.Text = $BaseDirectory
        $baseBox.ReadOnly = $true
        $form.Controls.Add($baseBox)
        $baseButton = New-Object System.Windows.Forms.Button
        $baseButton.SetBounds(700, 55, 112, 29)
        $baseButton.Text = '选择并应用'
        $form.Controls.Add($baseButton)
        $fields = [ordered]@{}
        $rows = @(
            @{Key='NodeRoot'; Label='Node.js 程序'; Tool='node'; Relative='programs\nodejs'},
            @{Key='NodeConfigRoot'; Label='npm 配置 / 缓存'; Tool='node'; Relative='config\nodejs'},
            @{Key='McpRoot'; Label='KiCad MCP 程序'; Tool='mcp'; Relative='programs\KiCAD-MCP-Server'},
            @{Key='McpConfigRoot'; Label='MCP 配置'; Tool='mcp'; Relative='config\kicad-mcp'},
            @{Key='EasyKiRoot'; Label='EasyKi 程序'; Tool='easyki'; Relative='programs\EasyKiConverter'},
            @{Key='EasyKiConfigRoot'; Label='EasyKi 配置 / 缓存'; Tool='easyki'; Relative='config\easyki'}
        )
        $rowIndex = 0
        foreach ($row in $rows) {
            $top = 112 + 45 * $rowIndex
            $label = New-Object System.Windows.Forms.Label
            $label.SetBounds(16, ($top + 4), 150, 25)
            $label.Text = $row.Label
            $form.Controls.Add($label)
            $box = New-Object System.Windows.Forms.TextBox
            $box.SetBounds(170, $top, 520, 27)
            $box.Text = $choices[$row.Key]
            $fields[$row.Key] = $box
            $form.Controls.Add($box)
            $button = New-Object System.Windows.Forms.Button
            $button.SetBounds(700, ($top - 1), 112, 29)
            $button.Text = '浏览…'
            $button.Tag = $row.Key
            $button.Add_Click({
                $target = $fields[[string]$this.Tag]
                $selected = Select-AD2KiCadDirectory -Description '选择目录；也可取消后直接在输入框填写新目录' -InitialDirectory $target.Text
                if ($selected) { $target.Text = $selected }
            })
            $form.Controls.Add($button)
            $detected = $Discovery.($row.Tool)
            $status = if ($detected.installed) { '检测到现有程序，将复用；修改路径后会重新检查。' } else { '需要安装；可选空目录或填写尚未创建的新目录。' }
            $tip.SetToolTip($box, ($status + ' 路径可包含中文和空格。'))
            $rowIndex++
        }
        $baseButton.Add_Click({
            $selected = Select-AD2KiCadDirectory -Description '选择新工具的统一根目录；已找到的工具与配置不会被移动' -InitialDirectory $baseBox.Text
            if ($selected) {
                $baseBox.Text = $selected
                foreach ($row in $rows) {
                    $fieldName = if ($row.Key -match 'ConfigRoot$') { 'config_root' } else { 'root' }
                    if (-not $Discovery.($row.Tool).($fieldName)) { $fields[$row.Key].Text = Join-Path $selected $row.Relative }
                }
            }
        })
        $note = New-Object System.Windows.Forms.Label
        $note.SetBounds(16, 385, 798, 42)
        $note.Text = "程序将检查所选目录，并保护已有配置；失败原因会保存在诊断报告中。`n点击「开始配置」后执行下载、安装和配置。取消会退出，不开始安装。"
        $form.Controls.Add($note)
        $start = New-Object System.Windows.Forms.Button
        $start.SetBounds(550, 444, 128, 32)
        $start.Text = '开始配置'
        $start.Add_Click({
            try {
                $selected = [ordered]@{}
                foreach ($key in $fields.Keys) { $selected[$key] = Get-AD2KiCadFullPath $fields[$key].Text }
                if ($Validate) {
                    $form.UseWaitCursor = $true
                    $start.Enabled = $false
                    try { & $Validate $selected | Out-Null }
                    finally { $form.UseWaitCursor = $false; $start.Enabled = $true }
                }
                $form.Tag = $selected
                $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
                $form.Close()
            } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '请检查目录', 'OK', 'Warning') | Out-Null }
        })
        $form.Controls.Add($start)
        $cancel = New-Object System.Windows.Forms.Button
        $cancel.SetBounds(690, 444, 122, 32)
        $cancel.Text = '取消'
        $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $form.Controls.Add($cancel)
        $form.AcceptButton = $start
        $form.CancelButton = $cancel
        if ($form.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw [System.OperationCanceledException]::new('已取消；未开始安装或配置。') }
        return $form.Tag
    } finally { $tip.Dispose(); $form.Dispose() }
}
