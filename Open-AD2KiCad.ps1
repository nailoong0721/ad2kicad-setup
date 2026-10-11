#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProjectRoot, [string]$KiCadCli)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ProjectRoot)
if (-not $KiCadCli) {
    $saved = Join-Path $ProjectRoot '_ad2kicad_work\AD2KICAD_SETUP_RUN.json'
    if (-not (Test-Path -LiteralPath $saved -PathType Leaf)) {
        $saved = Join-Path $ProjectRoot 'AD2KICAD_SETUP_RUN.json'
    }
    if (Test-Path -LiteralPath $saved -PathType Leaf) {
        $KiCadCli = (Get-Content -LiteralPath $saved -Raw -Encoding UTF8 | ConvertFrom-Json).kicad_cli
    }
}
if (-not $KiCadCli -or -not (Test-Path -LiteralPath $KiCadCli -PathType Leaf)) { throw 'Pass the confirmed -KiCadCli path.' }
$pythonExecutable = Join-Path ([System.IO.Path]::GetDirectoryName($KiCadCli)) 'python.exe'
& $pythonExecutable (Join-Path $PSScriptRoot 'project_session.py') --project $ProjectRoot --kicad-cli $KiCadCli --mode gui --output-dir (Join-Path $ProjectRoot '_ad2kicad_work\last-gui-session')
if ($LASTEXITCODE -ne 0) { throw 'KiCad session failed; see last-gui-session report.' }
