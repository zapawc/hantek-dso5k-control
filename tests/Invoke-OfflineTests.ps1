[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Repository,
    [Parameter(Mandatory)][string]$Python
)

$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath $Repository).Path
$helper = Join-Path $repoPath 'src\hantek_dso5k\_host_snapshot.ps1'
$tokens = $null
$parseErrors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($helper, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw $parseErrors[0] }
'Windows PowerShell AST PASS (parse only)'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $repoPath 'src'
    & $Python -B (Join-Path $repoPath 'tests\run_offline.py')
    $testExitCode = $LASTEXITCODE
    if ($testExitCode -ne 0) {
        Write-Error -ErrorAction Continue "Offline Python regression suite failed (exit $testExitCode)."
        exit $testExitCode
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
'Offline regression suite PASS'
