# Task Scheduler-friendly wrapper for scripts/run-daily-evo.py.
# Sets up env, invokes the Python orchestrator, writes a top-level run log,
# and propagates the exit code.
#
# Port notes (archive/crypto-w1-complete -> factor-framework-merge, 2026-05-20):
#   - Paths unchanged (same VENV_PY, same runner path).
#   - --commit default preserved (Task Scheduler runs leave a git trail).

$ErrorActionPreference = "Stop"

$KAtanaRoot = "D:\projects\k-atana"
$VenvPy     = "$KAtanaRoot\backend\.venv\Scripts\python.exe"
$Runner     = "$KAtanaRoot\scripts\run-daily-evo.py"
$LogRoot    = "$KAtanaRoot\artifacts\daily_evo\_run_logs"
$DateUtc    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$RunLog     = "$LogRoot\run-$DateUtc.log"

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

# Kill switch -- let humans pause the schedule by setting this env var system-wide.
if ($env:KATANA_DAILY_EVO -eq "skip") {
    "[$([DateTime]::UtcNow.ToString('o'))] KATANA_DAILY_EVO=skip -> no-op exit." | Tee-Object -FilePath $RunLog -Append | Out-Host
    exit 0
}

if (-not (Test-Path $VenvPy)) {
    "[$([DateTime]::UtcNow.ToString('o'))] FAIL: venv python missing: $VenvPy" | Tee-Object -FilePath $RunLog -Append | Out-Host
    exit 2
}

"[$([DateTime]::UtcNow.ToString('o'))] start daily-evo for $DateUtc" | Tee-Object -FilePath $RunLog -Append | Out-Host

# Forward all extra positional args to the Python runner; --commit defaults on
# so Task Scheduler runs leave a git trail.
$ExtraArgs = $args
if (-not ($ExtraArgs -contains "--commit")) { $ExtraArgs = @("--commit") + $ExtraArgs }

& $VenvPy $Runner @ExtraArgs *>> $RunLog
$ExitCode = $LASTEXITCODE

"[$([DateTime]::UtcNow.ToString('o'))] daily-evo finished exit=$ExitCode" | Tee-Object -FilePath $RunLog -Append | Out-Host
exit $ExitCode
