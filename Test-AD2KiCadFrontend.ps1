#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$KiCadCli = 'D:\Kicad\bin\kicad-cli.exe',
    [string]$OutputRoot,
    [switch]$IncludeUiSmoke
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$global:OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
. (Join-Path $PSScriptRoot 'Setup-AD2KiCadUi.ps1')
if (-not $OutputRoot) { $OutputRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('AD2KiCad-frontend-tests-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
$OutputRoot = Get-AD2KiCadFullPath $OutputRoot
[System.IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$results = New-Object 'System.Collections.Generic.List[object]'
function Check([bool]$Condition, [string]$Name) {
    $results.Add(@{name=$Name; passed=$Condition})
    if (-not $Condition) { throw "FAIL: $Name" }
    Write-Host "PASS: $Name"
}
function Expect-Failure([scriptblock]$Action, [string]$Match, [string]$Name) {
    $message = ''
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    Check ($message -match $Match) $Name
}

foreach ($name in @('Setup-AD2KiCad.ps1', 'Setup-AD2KiCadEnvironment.ps1', 'Setup-AD2KiCadUi.ps1')) {
    $path = Join-Path $PSScriptRoot $name
    $tokens = $null; $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors) | Out-Null
    Check (@($parseErrors).Count -eq 0) ("PowerShell 5.1 AST: $name")
    Check ([System.BitConverter]::ToString([System.IO.File]::ReadAllBytes($path), 0, 3) -eq 'EF-BB-BF') ("UTF-8 BOM: $name")
    $null = Get-Content -LiteralPath $path -Raw -Encoding UTF8
}
$python = Test-AD2KiCadPrerequisites -KiCadCli $KiCadCli
Check (Test-Path -LiteralPath $python -PathType Leaf) 'Real KiCad + bundled Python + tomllib + pcbnew preflight'
$nativeLog = Join-Path $OutputRoot 'native-output.log'
$nativeOutput = Invoke-AD2KiCadNative -Executable $python -ArgumentList @('-c', "import sys; print('stderr notice', file=sys.stderr); print('stdout success')") -Label 'Test' -Capture -LogPath $nativeLog
Check ($nativeOutput -match 'stderr notice' -and $nativeOutput -match 'stdout success') 'Native stderr with exit 0 stays successful and is logged'
Expect-Failure { Invoke-AD2KiCadNative -Executable $python -ArgumentList @('-c', "import sys; print('specific failure', file=sys.stderr); sys.exit(7)") -Label 'Test' -Capture } '7[\s\S]*specific failure' 'Native nonzero code and stderr details are preserved'
Expect-Failure { Get-AD2KiCadCli -NonInteractive } '-KiCadCli' 'Missing CLI fails without UI in noninteractive mode'

$empty = [pscustomobject]@{
    node=[pscustomobject]@{installed=$false; needs_directory=$true; root=$null; config_root=$null}
    mcp=[pscustomobject]@{installed=$false; needs_directory=$true; root=$null; config_root=$null}
    easyki=[pscustomobject]@{installed=$false; needs_directory=$true; root=$null; config_root=$null}
}
$base = Join-Path $OutputRoot '中文 [paths]'
$choices = Get-AD2KiCadDirectoryChoices -Discovery $empty -BaseDirectory $base
Check ($choices.Count -eq 6 -and $choices.NodeRoot -eq (Join-Path $base 'programs\nodejs') -and $choices.NodeConfigRoot -eq (Join-Path $base 'config\nodejs')) 'Missing tools get distinct editable program/config suggestions'
Expect-Failure { Assert-AD2KiCadNonInteractiveChoices -Discovery $empty } '-NodeRoot[\s\S]*-NodeConfigRoot[\s\S]*-McpRoot[\s\S]*-McpConfigRoot[\s\S]*-EasyKiRoot[\s\S]*-EasyKiConfigRoot' 'Noninteractive reports all six missing directory parameters'
$existing = $empty | ConvertTo-Json -Depth 4 | ConvertFrom-Json
foreach ($entry in @(@{Name='node'; Root='NodeRoot'; Config='NodeConfigRoot'}, @{Name='mcp'; Root='McpRoot'; Config='McpConfigRoot'}, @{Name='easyki'; Root='EasyKiRoot'; Config='EasyKiConfigRoot'})) {
    $existing.($entry.Name).installed = $true
    $existing.($entry.Name).needs_directory = $false
    $existing.($entry.Name).root = $choices[$entry.Root]
    $existing.($entry.Name).config_root = $choices[$entry.Config]
}
$reused = Get-AD2KiCadDirectoryChoices -Discovery $existing -BaseDirectory (Join-Path $base 'different')
Check ($reused.NodeRoot -eq $choices.NodeRoot -and $reused.McpConfigRoot -eq $choices.McpConfigRoot) 'Existing installations and configurations keep their selected locations'
Assert-AD2KiCadNonInteractiveChoices -Discovery $existing

# Exercise the real front-end process against a local fake backend: no download,
# installation, Codex registration, project conversion or real config writes.
$fixture = Join-Path $base 'fixture'
[System.IO.Directory]::CreateDirectory($fixture) | Out-Null
foreach ($name in @('Setup-AD2KiCadEnvironment.ps1', 'Setup-AD2KiCadUi.ps1')) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $fixture $name) }
$stub = @'
import json, os, pathlib, sys
args = sys.argv[1:]
mode = os.environ.get('AD2KICAD_FRONTEND_TEST_MODE', 'success')
def get(flag, default=None):
    return args[args.index(flag)+1] if flag in args else default
output = pathlib.Path(get('--output'))
events = pathlib.Path(__file__).with_name('events.jsonl')
phase = 'inspect' if '--inspect-only' in args else 'validate' if '--validate-plan' in args else 'setup'
with events.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'phase': phase, 'args': args}, ensure_ascii=False) + '\n')
locations = {}
for name, root_flag, config_flag in [('node','--node-root','--node-config-root'), ('mcp','--mcp-root','--mcp-config-root'), ('easyki','--easyki-root','--easyki-config-root')]:
    root = get(root_flag)
    config = get(config_flag)
    locations[name] = {'root':root, 'config_root':config, 'installed':False, 'needs_directory':not bool(root)}
if phase == 'inspect':
    print(json.dumps(locations, ensure_ascii=False))
elif phase == 'validate':
    print(json.dumps({'passed': mode != 'invalid', 'error': 'selected paths overlap' if mode == 'invalid' else None}))
    sys.exit(3 if mode == 'invalid' else 0)
elif mode == 'failure':
    print('backend installation detail', file=sys.stderr)
    sys.exit(9)
else:
    output.mkdir(parents=True, exist_ok=True)
    (output / 'environment-state.json').write_text(json.dumps({'passed':True}), encoding='utf-8')
    print('stub environment complete')
'@
[System.IO.File]::WriteAllText((Join-Path $fixture 'environment_setup.py'), $stub, $utf8NoBom)
$childPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$environmentScript = Join-Path $fixture 'Setup-AD2KiCadEnvironment.ps1'
$eventsPath = Join-Path $fixture 'events.jsonl'
$previousMode = $env:AD2KICAD_FRONTEND_TEST_MODE
try {
    foreach ($mode in @('missing', 'invalid', 'success', 'failure')) {
        $env:AD2KICAD_FRONTEND_TEST_MODE = $mode
        if (Test-Path -LiteralPath $eventsPath -PathType Leaf) { Remove-Item -LiteralPath $eventsPath -Force }
        $output = Join-Path $fixture ('output-' + $mode)
        $arguments = @('-NoProfile', '-STA', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $environmentScript, '-KiCadCli', $KiCadCli, '-WorkspaceRoot', $fixture, '-OutputDirectory', $output, '-SkipDependencySmoke', '-NonInteractive')
        if ($mode -ne 'missing') { foreach ($key in $choices.Keys) { $arguments += @(('-' + $key), $choices[$key]) } }
        $message = ''; $passed = $false
        try { $message = Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList $arguments -Label $mode -Capture; $passed = $true }
        catch { $message = $_.Exception.Message }
        $events = @(Get-Content -LiteralPath $eventsPath -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
        $phases = @($events | ForEach-Object { $_.phase }) -join ','
        if ($mode -eq 'missing') { Check (-not $passed -and $phases -eq 'inspect' -and -not (Test-Path -LiteralPath $output)) 'Missing directories stop before validation/install and output creation' }
        if ($mode -eq 'invalid') { Check (-not $passed -and $phases -eq 'inspect,validate' -and -not (Test-Path -LiteralPath $output)) 'Failed path preflight prevents installation and output creation' }
        if ($mode -eq 'success') {
            Check ($passed -and $phases -eq 'inspect,validate,setup' -and (Test-Path -LiteralPath (Join-Path $output 'environment-state.json'))) 'Valid noninteractive flow runs inspect, validation, setup in order'
            Check (@($events[2].args | Where-Object { $_ -eq $choices.NodeRoot }).Count -eq 1) 'Chinese, spaces and brackets survive child PowerShell and Python arguments'
        }
        if ($mode -eq 'failure') { Check (-not $passed -and $phases -eq 'inspect,validate,setup' -and (Get-Content -LiteralPath (Join-Path $output 'setup-console.log') -Raw -Encoding UTF8) -match 'backend installation detail') 'Backend failure propagates and full diagnostic detail is saved' }
    }
} finally { $env:AD2KICAD_FRONTEND_TEST_MODE = $previousMode }

# Main-entry control flow uses fake environment/project workers in the same
# isolated fixture, while retaining the real PowerShell entry and native runner.
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Setup-AD2KiCad.ps1') -Destination (Join-Path $fixture 'Setup-AD2KiCad.ps1')
$environmentStub = @'
param($KiCadCli,$WorkspaceRoot,$OutputDirectory,$CodexRoot,$NodeRoot,$NodeConfigRoot,$McpRoot,$McpConfigRoot,$EasyKiRoot,$EasyKiConfigRoot,[switch]$NonInteractive)
[System.IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
$encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$record = @{codex_root=$CodexRoot; node_root=$NodeRoot; node_config_root=$NodeConfigRoot; mcp_root=$McpRoot; easyki_root=$EasyKiRoot; noninteractive=[bool]$NonInteractive}
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'main-environment-call.json'), ($record | ConvertTo-Json), $encoding)
[System.IO.File]::WriteAllText((Join-Path $OutputDirectory 'environment-state.json'), '{"passed":true}', $encoding)
'@
[System.IO.File]::WriteAllText($environmentScript, $environmentStub, $utf8NoBom)
$projectStub = @'
import json, pathlib, sys
events = pathlib.Path(__file__).with_name('project-events.jsonl')
with events.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'worker':pathlib.Path(__file__).name, 'args':sys.argv[1:]}, ensure_ascii=False) + '\n')
print('stub project worker complete')
'@
foreach ($name in @('organize_project.py','index_footprints.py')) { [System.IO.File]::WriteAllText((Join-Path $fixture $name), $projectStub, $utf8NoBom) }
$mainArguments = @('-NoProfile','-STA','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $fixture 'Setup-AD2KiCad.ps1'),'-NonInteractive','-KiCadCli',$KiCadCli)
Expect-Failure { Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList $mainArguments -Label 'Main' -Capture } '-ProjectRoot' 'Main missing project fails without UI'
$protected = Join-Path $fixture 'AD2Kicad_original_prj'
[System.IO.Directory]::CreateDirectory($protected) | Out-Null
Expect-Failure { Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList ($mainArguments + @('-ProjectRoot',$protected)) -Label 'Main' -Capture } '原始项目受到保护' 'Main rejects the protected original before environment changes'
$project = Join-Path $fixture 'project copy'
[System.IO.Directory]::CreateDirectory($project) | Out-Null
Expect-Failure { Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList ($mainArguments + @('-ProjectRoot',$project)) -Label 'Main' -Capture } '完整的 KiCad 项目副本' 'Main rejects incomplete projects before environment changes'
Check (-not (Test-Path -LiteralPath (Join-Path $fixture 'main-environment-call.json'))) 'Main validation failures never call environment setup'
foreach ($suffix in @('.kicad_pro','.kicad_pcb','.kicad_sch')) { [System.IO.File]::WriteAllText((Join-Path $project ('example' + $suffix)), 'test fixture', $utf8NoBom) }
$mainRunArguments = $mainArguments + @('-ProjectRoot',$project,'-CodexRoot',(Join-Path $base 'codex settings'),'-NodeRoot',$choices.NodeRoot,'-NodeConfigRoot',$choices.NodeConfigRoot)
Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList $mainRunArguments -Label 'Main' -Capture | Out-Null
$mainCall = Get-Content -LiteralPath (Join-Path $fixture 'main-environment-call.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Check ($mainCall.codex_root -eq (Join-Path $base 'codex settings') -and $mainCall.node_root -eq $choices.NodeRoot -and $mainCall.node_config_root -eq $choices.NodeConfigRoot -and $mainCall.noninteractive) 'Main forwards custom Codex/Node paths and noninteractive mode'
$runSummary = Get-Content -LiteralPath (Join-Path $project '_ad2kicad_work\AD2KICAD_SETUP_RUN.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Check ($runSummary.passed -and -not $runSummary.verify_only -and $runSummary.library_name -eq 'AD2Kicad') 'Main success writes a structured run summary'
[System.IO.File]::WriteAllText((Join-Path $project '_ad2kicad_work\AD2KICAD_REPORT.json'), '{"library_name":"ExistingLib"}', $utf8NoBom)
Invoke-AD2KiCadNative -Executable $childPowerShell -ArgumentList ($mainRunArguments + @('-VerifyOnly')) -Label 'Main verify' -Capture | Out-Null
$projectEvents = @(Get-Content -LiteralPath (Join-Path $fixture 'project-events.jsonl') -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
Check ($projectEvents.Count -eq 4 -and $projectEvents[2].args -contains '--verify-only' -and $projectEvents[2].args -contains '--library-name=ExistingLib' -and $projectEvents[3].worker -eq 'index_footprints.py') 'VerifyOnly preserves environment step, prior library name, project verification and index validation'

# Read-only execution of the argument builder proves default-config.json is
# not silently changed into config.json by an unchanged form submission.
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Setup-AD2KiCadEnvironment.ps1'), [ref]$tokens, [ref]$parseErrors)
$functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-EnvironmentArguments' }, $true)
. ([scriptblock]::Create($functionAst.Extent.Text))
$baseArguments = @('environment_setup.py'); $locationValues = @{}
$locationDefinitions = @(@{Key='McpRoot'; Flag='--mcp-root'; Tool='mcp'; Field='root'; ProgramKey='McpRoot'}, @{Key='McpConfigRoot'; Flag='--mcp-config-root'; Tool='mcp'; Field='config_root'; ProgramKey='McpRoot'})
$discovery = $existing
$forwarded = @(Get-EnvironmentArguments -Chosen $reused)
Check ($forwarded -notcontains '--mcp-config-root') 'Unchanged MCP config selection preserves the original config filename'
$reused.McpConfigRoot = Join-Path $base 'custom config'
$forwarded = @(Get-EnvironmentArguments -Chosen $reused)
Check ($forwarded -contains '--mcp-config-root' -and $forwarded -contains $reused.McpConfigRoot) 'Changed MCP config folder is forwarded explicitly'

if ($IncludeUiSmoke) {
    Add-Type -AssemblyName System.Windows.Forms
    foreach ($uiMode in @('accept', 'cancel')) {
        $timer = New-Object System.Windows.Forms.Timer
        $timer.Interval = 150
        $timer.Add_Tick({
            foreach ($window in @([System.Windows.Forms.Application]::OpenForms)) {
                if ($window.Text -like 'AD2KiCad*') {
                    $timer.Stop()
                    if ($uiMode -eq 'accept') {
                        $bitmap = New-Object System.Drawing.Bitmap($window.Width, $window.Height)
                        try {
                            $window.DrawToBitmap($bitmap, (New-Object System.Drawing.Rectangle(0, 0, $window.Width, $window.Height)))
                            $bitmap.Save((Join-Path $OutputRoot 'directory-dialog.png'), [System.Drawing.Imaging.ImageFormat]::Png)
                        } finally { $bitmap.Dispose() }
                        $window.AcceptButton.PerformClick()
                    }
                    else { $window.CancelButton.PerformClick() }
                }
            }
        })
        try {
            $timer.Start()
            if ($uiMode -eq 'accept') {
                $script:validated = $false
                $value = Show-AD2KiCadDirectoryChoices -Discovery $existing -BaseDirectory $base -Validate { param($candidate) $script:validated = ($candidate.Count -eq 6) }
                Check ($script:validated -and $value.Count -eq 6) 'Form accept invokes preflight callback with six paths'
            } else {
                Expect-Failure { Show-AD2KiCadDirectoryChoices -Discovery $existing -BaseDirectory $base } '取消' 'Form cancel returns explicit cancellation'
            }
        } finally { $timer.Stop(); $timer.Dispose() }
    }
}
$report = @{passed=$true; checks=$results.ToArray(); backend='local stub; no installations or registration'; real_kicad_preflight=$true; ui_smoke=[bool]$IncludeUiSmoke; output=$OutputRoot}
[System.IO.File]::WriteAllText((Join-Path $OutputRoot 'frontend-test-report.json'), ($report | ConvertTo-Json -Depth 6), $utf8NoBom)
Write-Host ("Frontend tests passed: {0}; report: {1}" -f $results.Count, (Join-Path $OutputRoot 'frontend-test-report.json'))
