#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PYTHON="${PYTHON:-$BACKEND/.venv/bin/python}"
HIST_MAT_HOME="${HIST_MAT_HOME:-/srv/hist-mat}"
ASHARE_DATA_ROOT="${ASHARE_DATA_ROOT:-/srv/lan-ai/data/ashare}"

usage() {
  cat <<'USAGE'
Usage: scripts/test-katana.sh [unit|crypto|ashare|frontend|all]

unit      Compile core backend entry points and run backend unit/contract tests.
crypto    Run OKX comprehensive smoke and verify stdout is valid JSON.
ashare    Verify the A-share lake coverage manifest can be read.
frontend  Build the Next.js frontend when node_modules is installed.
all       Run unit + crypto + ashare. Frontend stays explicit.
USAGE
}

require_python() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "Python venv not found: $PYTHON" >&2
    echo "Create or sync backend/.venv before running tests." >&2
    exit 2
  fi
}

unit() {
  require_python
  cd "$BACKEND"
  "$PYTHON" -m py_compile \
    app/api/strategy_api.py \
    app/api/crypto/okx_api.py \
    app/api/research/backtest.py \
    app/api/research/strategy.py \
    app/api/trading/crypto_live_test.py \
    app/api/trading/paper.py \
    app/markets/crypto/crypto_market_data.py \
    app/research/hmc.py \
    app/research/models.py \
    app/research/pit.py \
    app/trading/executor.py \
    app/trading/intent.py \
    app/trading/risk.py \
    app/trading/adapters/okx/bridge.py \
    app/trading/adapters/qmt/bridge.py \
    run_okx_comprehensive.py
  "$PYTHON" -c "import pytest" >/dev/null 2>&1 || {
    echo "pytest not installed in $PYTHON; install backend test dependencies first." >&2
    exit 2
  }
  "$PYTHON" -m pytest tests/unit tests/contract
}

crypto() {
  require_python
  cd "$BACKEND"
  local stdout_file stderr_file
  stdout_file="$(mktemp)"
  stderr_file="$(mktemp)"
  HIST_MAT_HOME="$HIST_MAT_HOME" "$PYTHON" run_okx_comprehensive.py \
    --pairs "${KATANA_CRYPTO_PAIRS:-BTC-USDT}" \
    --limit "${KATANA_CRYPTO_LIMIT:-80}" \
    --json >"$stdout_file" 2>"$stderr_file"
  "$PYTHON" -m json.tool "$stdout_file" >/dev/null
  if [[ -s "$stderr_file" ]]; then
    echo "crypto smoke wrote stderr:" >&2
    cat "$stderr_file" >&2
    exit 1
  fi
  echo "crypto smoke OK: $(wc -c <"$stdout_file") bytes JSON"
}

ashare() {
  require_python
  local manifest="$ASHARE_DATA_ROOT/_manifest/coverage.json"
  if [[ ! -f "$manifest" ]]; then
    echo "A-share coverage manifest not found: $manifest" >&2
    echo "Run the data-lake daily check on the Arch/NAS host first." >&2
    exit 1
  fi
  "$PYTHON" - "$manifest" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
payload = json.loads(manifest.read_text(encoding="utf-8"))
entries = payload.get("items", payload.get("entries", [])) if isinstance(payload, dict) else payload
kline_entries = [item for item in entries if item.get("dataset") == "kline_daily"]
if not kline_entries:
    raise SystemExit(f"no kline_daily coverage entries in {manifest}")
print(f"ashare coverage OK: {len(kline_entries)} kline_daily entries from {manifest}")
PY
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
  crypto) crypto ;;
  ashare) ashare ;;
  frontend) frontend ;;
  all)
    unit
    crypto
    ashare
    ;;
  -h|--help|help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
