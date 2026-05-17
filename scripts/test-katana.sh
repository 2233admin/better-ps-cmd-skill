#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
UV="${UV:-uv}"
HIST_MAT_HOME="${HIST_MAT_HOME:-/srv/hist-mat}"
ASHARE_DATA_ROOT="${ASHARE_DATA_ROOT:-/srv/lan-ai/data/ashare}"

usage() {
  cat <<'USAGE'
Usage: scripts/test-katana.sh [unit|arch|crypto|crypto-smoke|crypto-pipeline|ashare|ashare-lake|ashare-world|ashare-pipeline-smoke|ashare-benchmark|ashare-control|frontend|all]

unit      Compile core backend entry points and run backend unit/contract tests.
arch      Run sentrux architectural boundary checks.
crypto    Run crypto-smoke + crypto-pipeline.
crypto-smoke
          Run OKX comprehensive smoke and verify stdout is valid JSON.
crypto-pipeline
          Run the fixture-backed crypto PIT research pipeline.
ashare    Alias for ashare-lake.
ashare-lake
          Verify A-share lake validation, coverage, and calendar manifests.
ashare-world
          Build a PIT-safe world snapshot from the A-share lake.
ashare-pipeline-smoke
          Run the A-share research pipeline against fixture or lake data.
ashare-benchmark
          Run A-share benchmark when KATANA_RUN_FULL_ASHARE_BENCHMARK=1, otherwise fixture smoke.
ashare-control
          Run the unified A-share control plane.
frontend  Build the Next.js frontend when node_modules is installed.
all       Run unit + arch + crypto + ashare. Frontend stays explicit.
USAGE
}

require_uv() {
  if ! command -v "$UV" >/dev/null 2>&1; then
    echo "uv not found; install uv before running backend checks." >&2
    exit 2
  fi
}

uv_python() {
  PYTHONDONTWRITEBYTECODE=1 "$UV" run --no-project --with-requirements "$BACKEND/requirements.txt" python "$@"
}

require_sentrux() {
  if ! command -v sentrux >/dev/null 2>&1; then
    echo "sentrux not found; install sentrux before running architecture checks." >&2
    exit 2
  fi
}

unit() {
  require_uv
  cd "$BACKEND"
  uv_python -m py_compile \
    app/api/strategy_api.py \
    app/api/crypto/okx_api.py \
    app/api/research/backtest.py \
    app/api/research/strategy.py \
    app/api/trading/crypto_live_test.py \
    app/api/trading/paper.py \
    app/markets/crypto/crypto_market_data.py \
    app/research/ashare_data_contract.py \
    app/research/agent.py \
    app/research/hmc.py \
    app/research/manifest.py \
    app/research/models.py \
    app/research/morning_package/__init__.py \
    app/research/morning_package/builder.py \
    app/research/morning_package/cli.py \
    app/research/morning_package/converters.py \
    app/research/morning_package/models.py \
    app/research/morning_package/renderers.py \
    app/research/morning_package/validator.py \
    app/research/pit.py \
    app/research/backtest/__init__.py \
    app/research/backtest/engine.py \
    app/research/backtest/models.py \
    app/research/backtest/renderers.py \
    app/research/crypto_pipeline/__init__.py \
    app/research/crypto_pipeline/cli.py \
    app/research/crypto_pipeline/control.py \
    app/research/crypto_pipeline/factors.py \
    app/research/crypto_pipeline/runner.py \
    app/research/pipeline/__init__.py \
    app/research/pipeline/cli.py \
    app/research/pipeline/benchmark.py \
    app/research/pipeline/control.py \
    app/research/pipeline/data_lake.py \
    app/research/pipeline/runner.py \
    app/research/pipeline/world_snapshot.py \
    app/research/snapshot.py \
    app/trading/executor.py \
    app/trading/intent.py \
    app/trading/risk.py \
    app/trading/adapters/okx/bridge.py \
    app/trading/adapters/qmt/bridge.py \
    app/trading/adapters/qmt/morning_package_export.py \
    run_okx_comprehensive.py
  uv_python -m py_compile \
    ../scripts/ingest-ashare-bridge.py \
    ../scripts/ingest-ashare-incremental.py \
    ../scripts/validate-ashare-lake.py \
    ../scripts/build-ashare-coverage.py \
    ../scripts/build-ashare-calendar.py
  uv_python -m pytest tests/unit tests/contract -p no:cacheprovider
}

arch() {
  require_sentrux
  cd "$ROOT"
  sentrux check .
}

crypto_smoke() {
  require_uv
  cd "$BACKEND"
  local stdout_file stderr_file
  stdout_file="$(mktemp)"
  stderr_file="$(mktemp)"
  HIST_MAT_HOME="$HIST_MAT_HOME" uv_python run_okx_comprehensive.py \
    --pairs "${KATANA_CRYPTO_PAIRS:-BTC-USDT}" \
    --limit "${KATANA_CRYPTO_LIMIT:-80}" \
    --json >"$stdout_file" 2>"$stderr_file"
  uv_python -m json.tool "$stdout_file" >/dev/null
  if [[ -s "$stderr_file" ]]; then
    echo "crypto smoke wrote stderr:" >&2
    cat "$stderr_file" >&2
    exit 1
  fi
  echo "crypto smoke OK: $(wc -c <"$stdout_file") bytes JSON"
}

crypto_pipeline() {
  require_uv
  cd "$BACKEND"
  local out_dir
  out_dir="$(mktemp -d)"
  uv_python -m app.research.crypto_pipeline.cli \
    --date "${KATANA_CRYPTO_PIPELINE_DATE:-2026-05-17}" \
    --inst-ids "${KATANA_CRYPTO_PIPELINE_INST_IDS:-BTC-USDT,ETH-USDT}" \
    --market-type spot \
    --out "$out_dir" \
    --code-commit "${KATANA_CODE_COMMIT:-manual}"
  test -f "$out_dir/manifest.json"
  test -f "$out_dir/signals.json"
  test -f "$out_dir/factors.json"
  test -f "$out_dir/backtest/metrics.json"
  test -f "$out_dir/session_package/session_package.md"
  test -f "$out_dir/session_package/control_report.json"
  echo "crypto pipeline OK: $out_dir"
}

crypto() {
  crypto_smoke
  crypto_pipeline
}

ashare_lake() {
  require_uv
  local manifest="$ASHARE_DATA_ROOT/_manifest/coverage.json"
  local calendar_manifest="$ASHARE_DATA_ROOT/_manifest/trading_calendar.json"
  if [[ ! -f "$manifest" ]]; then
    echo "A-share coverage manifest not found: $manifest" >&2
    echo "Run the data-lake daily check on the Arch/NAS host first." >&2
    exit 1
  fi
  uv_python - "$manifest" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
payload = json.loads(manifest.read_text(encoding="utf-8"))
entries = payload.get("items", payload.get("entries", [])) if isinstance(payload, dict) else payload
kline_entries = [item for item in entries if item.get("dataset") in {"kline_daily", "ashare.kline_daily_pit"}]
if not kline_entries:
    raise SystemExit(f"no A-share kline_daily coverage entries in {manifest}")
print(f"ashare coverage OK: {len(kline_entries)} kline_daily entries from {manifest}")
PY
  if [[ ! -f "$calendar_manifest" ]]; then
    echo "A-share trading calendar manifest not found: $calendar_manifest" >&2
    echo "Run scripts/build-ashare-calendar.py for this lake before promoting it." >&2
    exit 1
  fi
  uv_python - "$calendar_manifest" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
payload = json.loads(manifest.read_text(encoding="utf-8"))
if payload.get("dataset") != "ashare.trading_calendar":
    raise SystemExit(f"unexpected calendar dataset in {manifest}: {payload.get('dataset')}")
if payload.get("source") != "tdx_lake_observed":
    raise SystemExit(f"unexpected calendar source in {manifest}: {payload.get('source')}")
print(f"ashare trading calendar OK: {payload.get('row_count')} rows from {manifest}")
PY
}

ashare_world() {
  require_uv
  cd "$BACKEND"
  local out_dir
  out_dir="$(mktemp -d)"
  uv_python -m app.research.pipeline.world_snapshot \
    --date "${KATANA_ASHARE_DATE:-2026-05-17}" \
    --data-root "$ASHARE_DATA_ROOT" \
    --out "$out_dir"
  test -f "$out_dir/world_snapshot.parquet"
  test -f "$out_dir/manifest.json"
  echo "ashare world snapshot OK: $out_dir"
}

ashare_pipeline_smoke() {
  require_uv
  cd "$BACKEND"
  local out_dir
  out_dir="$(mktemp -d)"
  uv_python -m app.research.pipeline.cli \
    --date "${KATANA_ASHARE_DATE:-2026-05-17}" \
    --symbols "${KATANA_ASHARE_SYMBOLS:-600000.SH,000001.SZ}" \
    --out "$out_dir" \
    --code-commit "${KATANA_CODE_COMMIT:-manual}"
  test -f "$out_dir/backtest_summary.parquet"
  test -f "$out_dir/scan_results.parquet"
  test -f "$out_dir/morning_package/control_report.json"
  echo "ashare pipeline smoke OK: $out_dir"
}

ashare_benchmark() {
  require_uv
  cd "$BACKEND"
  local out_dir
  out_dir="$(mktemp -d)"
  if [[ "${KATANA_RUN_FULL_ASHARE_BENCHMARK:-0}" == "1" ]]; then
    uv_python -m app.research.pipeline.benchmark \
      --date "${KATANA_ASHARE_DATE:-2026-05-17}" \
      --data-root "$ASHARE_DATA_ROOT" \
      --out "$out_dir" \
      --code-commit "${KATANA_CODE_COMMIT:-manual}"
  else
    uv_python -m app.research.pipeline.benchmark \
      --date "${KATANA_ASHARE_DATE:-2026-05-17}" \
      --data-root "$ASHARE_DATA_ROOT" \
      --symbols "${KATANA_ASHARE_SYMBOLS:-600000.SH,000001.SZ}" \
      --out "$out_dir" \
      --code-commit "${KATANA_CODE_COMMIT:-manual}"
  fi
  test -f "$out_dir/benchmark.json"
  test -f "$out_dir/benchmark.csv"
  test -f "$out_dir/control_report.compact.json"
  echo "ashare benchmark OK: $out_dir"
}

ashare_control() {
  require_uv
  pwsh -NoProfile -File "$ROOT/scripts/run-ashare-control-plane.ps1" \
    -LakeRoot "$ASHARE_DATA_ROOT" \
    -Date "${KATANA_ASHARE_DATE:-2026-05-17}" \
    -OutRoot "${KATANA_ASHARE_CONTROL_OUT:-$(mktemp -d)}" \
    -Mode all
}

ashare() {
  ashare_lake
}

frontend() {
  cd "$ROOT/frontend"
  if [[ ! -d node_modules ]]; then
    echo "frontend/node_modules missing; run npm install first." >&2
    exit 2
  fi
  npm run build
}

target="${1:-all}"
case "$target" in
  unit) unit ;;
  arch) arch ;;
  crypto) crypto ;;
  crypto-smoke) crypto_smoke ;;
  crypto-pipeline) crypto_pipeline ;;
  ashare) ashare ;;
  ashare-lake) ashare_lake ;;
  ashare-world) ashare_world ;;
  ashare-pipeline-smoke) ashare_pipeline_smoke ;;
  ashare-benchmark) ashare_benchmark ;;
  ashare-control) ashare_control ;;
  frontend) frontend ;;
  all)
    unit
    arch
    crypto
    ashare
    ;;
  -h|--help|help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
