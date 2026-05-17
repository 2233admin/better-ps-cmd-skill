param(
  [Parameter(Mandatory=$true)][string]$LakeRoot,
  [Parameter(Mandatory=$true)][string]$Date,
  [string]$OutRoot = "C:\tmp\katana-ashare-control-runs",
  [ValidateSet("validate","world","pipeline","benchmark","all")][string]$Mode = "all",
  [string]$Symbols = "",
  [switch]$FullArtifacts
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Backend = Join-Path $RepoRoot "backend"
$RunId = "$Date-$(Get-Date -Format 'yyyyMMddTHHmmss')"
$RunRoot = Join-Path $OutRoot $RunId
New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null

function Invoke-BackendPython {
  param([string[]]$Arguments)
  Push-Location $Backend
  try {
    & uv run python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "python command failed: $($Arguments -join ' ')" }
  } finally {
    Pop-Location
  }
}

function Write-JsonFile {
  param([string]$Path, [object]$Payload)
  $Payload | ConvertTo-Json -Depth 20 | Set-Content -Path $Path -Encoding UTF8
}

$Manifest = [ordered]@{
  control_plane = "ashare_control_plane_v1"
  lake_root = $LakeRoot
  date = $Date
  mode = $Mode
  run_root = $RunRoot
  started_at = (Get-Date).ToUniversalTime().ToString("o")
  artifacts = [ordered]@{}
}
Write-JsonFile (Join-Path $RunRoot "control_plane_manifest.json") $Manifest

if ($Mode -in @("validate","all")) {
  Write-Host "stage: validate"
  Invoke-BackendPython -Arguments @("..\\scripts\\validate-ashare-lake.py", "--root", $LakeRoot, "--json") |
    Set-Content -Path (Join-Path $RunRoot "lake_validation.json") -Encoding UTF8
  Write-Host "stage: coverage"
  Invoke-BackendPython -Arguments @("..\\scripts\\build-ashare-coverage.py", "--root", $LakeRoot, "--json") |
    Set-Content -Path (Join-Path $RunRoot "coverage.json") -Encoding UTF8
  Write-Host "stage: calendar"
  Invoke-BackendPython -Arguments @("..\\scripts\\build-ashare-calendar.py", "--root", $LakeRoot, "--json") |
    Set-Content -Path (Join-Path $RunRoot "trading_calendar.json") -Encoding UTF8
}

$WorldDir = Join-Path $RunRoot "world_snapshot"
if ($Mode -in @("world","all")) {
  $worldArgs = @("-m", "app.research.pipeline.world_snapshot", "--date", $Date, "--data-root", $LakeRoot, "--out", $WorldDir)
  if ($Symbols) { $worldArgs += @("--symbols", $Symbols) }
  Write-Host "stage: world_snapshot"
  Invoke-BackendPython -Arguments $worldArgs
}

$SnapshotPath = Join-Path $WorldDir "world_snapshot.parquet"
$PipelineDir = Join-Path $RunRoot "pipeline"
if ($Mode -in @("pipeline","all")) {
  $artifactLevel = if ($FullArtifacts) { "full" } else { "summary" }
  $pipelineArgs = @("-m", "app.research.pipeline.cli", "--date", $Date, "--data-root", $LakeRoot, "--out", $PipelineDir, "--artifact-level", $artifactLevel)
  if ($Symbols) { $pipelineArgs += @("--symbols", $Symbols) }
  elseif (Test-Path $SnapshotPath) { $pipelineArgs += @("--world-snapshot", $SnapshotPath) }
  Write-Host "stage: pipeline"
  Invoke-BackendPython -Arguments $pipelineArgs
}

if ($Mode -in @("benchmark","all")) {
  $benchmarkArgs = @("-m", "app.research.pipeline.benchmark", "--date", $Date, "--data-root", $LakeRoot, "--out", $RunRoot, "--code-commit", "manual")
  if ($Symbols) { $benchmarkArgs += @("--symbols", $Symbols) }
  elseif (Test-Path $SnapshotPath) { $benchmarkArgs += @("--world-snapshot", $SnapshotPath) }
  Write-Host "stage: benchmark"
  Invoke-BackendPython -Arguments $benchmarkArgs
}

$GateReport = [ordered]@{
  decision = "observe"
  run_root = $RunRoot
  benchmark = $null
  control_report = $null
}
if (Test-Path (Join-Path $RunRoot "benchmark.json")) {
  $GateReport.benchmark = Get-Content (Join-Path $RunRoot "benchmark.json") -Raw | ConvertFrom-Json
  $GateReport.decision = $GateReport.benchmark.decision
}
if (Test-Path (Join-Path $RunRoot "control_report.compact.json")) {
  $GateReport.control_report = Get-Content (Join-Path $RunRoot "control_report.compact.json") -Raw | ConvertFrom-Json
}
Write-JsonFile (Join-Path $RunRoot "gate_report.json") $GateReport

$Manifest.completed_at = (Get-Date).ToUniversalTime().ToString("o")
$Manifest.artifacts = [ordered]@{
  lake_validation = "lake_validation.json"
  coverage = "coverage.json"
  trading_calendar = "trading_calendar.json"
  world_snapshot = "world_snapshot/"
  pipeline = "pipeline/"
  benchmark = "benchmark.json"
  gate_report = "gate_report.json"
}
Write-JsonFile (Join-Path $RunRoot "control_plane_manifest.json") $Manifest
Write-Output "Ashare control plane run: $RunRoot"
