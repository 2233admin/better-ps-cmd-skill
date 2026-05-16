param(
    [string]$Target = "tests",
    [switch]$Coverage
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Missing backend virtualenv python: $Python"
}

$Args = @("-m", "pytest", "-q", "-p", "no:cacheprovider", $Target)
if ($Coverage) {
    $Args = @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=src", "--cov-report=term-missing", $Target)
}

& $Python @Args
exit $LASTEXITCODE
