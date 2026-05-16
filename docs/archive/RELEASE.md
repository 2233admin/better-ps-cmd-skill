# Source Release Notes

This release restores `k-atana` as a maintainable source tree.

## Scope

- Preserve the currently validated research, data, strategy, backtest, and terminal skeleton.
- Preserve the QMT phase-1 boundary: `k-atana` emits trade intent; EasyXT owns QMT/xtquant execution.
- Remove generated runtime artifacts from version control.

## Non-Goals

- No new trading behavior.
- No direct QMT calls from research or backtest code.
- No promotion of the current backtest core to production-trusted status.

See `docs/ARCHITECTURE_DECISION_QMT_PHASE1.md` for the execution boundary and promotion requirements.
