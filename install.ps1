# Delegate fresh module installation to the installed Core lifecycle.
[CmdletBinding()]
param([string]$Root, [switch]$Plan)
$ErrorActionPreference = 'Stop'
$core = Get-Command contextopus -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $core) {
    [Console]::Error.WriteLine('Install Contextopus Core >=1.5,<2 first: https://github.com/pikiby/contextopus-core#install-contextopus')
    exit 127
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.git')) {
    [Console]::Error.WriteLine('Build first: python scripts/build-module.py dist/workspace; then run ./dist/workspace/install.ps1 [-Root DATA_DIRECTORY] [-Plan].')
    exit 2
}
$arguments = @()
if ($Root) { $arguments += @('--root', $Root) }
$arguments += @('modules', 'install', 'workspace', '--source', $PSScriptRoot)
if ($Plan) { $arguments += '--plan' }
& $core.Source @arguments
exit $LASTEXITCODE
