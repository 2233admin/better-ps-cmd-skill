# Architecture Decision: QMT Phase-1 Boundary

Decision date: 2026-05-16.

## Decision

`k-atana` is the primary research, data, factor, and backtest skeleton.

EasyXT is not the research framework. EasyXT is only an execution adapter for
QMT/xtquant and the phase-1 simulation bridge.

```text
k-atana
  data -> factor -> signal -> portfolio -> backtest -> report -> trade intent

EasyXT
  trade intent -> phase1 bridge -> sim journal / QMT adapter
```

No `k-atana` research or backtest code may call QMT directly.

## Current Assessment

`k-atana` is structurally closer to the target system than `easyxt_backtest`:

- Polars/Parquet/DuckDB-friendly data path.
- Separate backend and frontend.
- Existing strategy, backtest, portfolio, risk, and trade modules.
- Existing A-share, futures, crypto, QMT/CTP/OKX/THS integration stubs.
- Better fit for a unified quant terminal.

But the current backtest core is not yet trusted for production-grade research.

Known issues to fix before promotion:

- `core/backtest.py` trade extraction uses the current position as previous position, so trade records are unreliable.
- Cost/slippage handling is simplified and currently applied to holding returns instead of explicit fills.
- Portfolio/account ledger is separate from vectorized backtest output.
- A-share constraints are incomplete: ST, suspension, limit up/down, lot size, failed fills, minimum turnover.
- QMT bridge under `backend/app/trade/qmt_bridge.py` is a direct broker adapter and must not be used by research code.
- README references some paths that differ from the actual code layout.

## Required Runtime Boundary

The only allowed outbound execution interface from `k-atana` is an execution
request compatible with EasyXT phase-1:

```text
request_id
strategy
account
symbol
side
qty
dry_run
note
market_snapshot
metadata
```

`account=sim` and `dry_run=true` must remain independently testable.

## Backtest Promotion Requirements

Before `k-atana` becomes the trusted backtest engine, it must satisfy:

- Deterministic output from fixed input data.
- Explicit next-bar execution by default.
- Explicit fills table with timestamp, symbol, side, qty, price, commission, slippage, status.
- Explicit orders table with rejected/filled/cancelled states.
- Explicit daily account ledger: cash, positions, market value, total equity, daily pnl.
- A-share constraints: 100-share lot size, ST, suspension, limit up/down, min turnover, failed fill.
- No look-ahead in data alignment or factor joins.
- Export results to Parquet/CSV/JSON independent of UI.
- Unit tests for trade extraction, costs, failed fills, limit constraints, and idempotent trade intent generation.

## Integration Plan

1. Keep EasyXT phase-1 bridge as the only QMT/sim execution entry.
2. Add a `k-atana` trade-intent exporter targeting EasyXT's bridge contract.
3. Repair `k-atana` backtest ledger and trade extraction.
4. Implement an MA rotation benchmark using fixed local data.
5. Compare backtest orders/fills against EasyXT phase-1 sim journal.
6. Only after the benchmark matches, promote `k-atana` as the trusted research engine.

## Non-Goals

- Do not port `easyxt_backtest` into `k-atana`.
- Do not use Streamlit demo code as a research interface.
- Do not connect `k-atana` directly to live QMT during phase-1.
- Do not let AI/AutoML tools generate broker orders directly.

The near-term goal is not a prettier panel. It is a research engine whose output
can survive audit and then safely flow into the EasyXT bridge.
