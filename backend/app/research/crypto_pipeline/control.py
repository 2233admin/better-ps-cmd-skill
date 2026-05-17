"""Rule-based control layer for crypto research sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import polars as pl

from app.research.backtest import LedgerBacktestResult
from app.research.crypto_pipeline.factors import CryptoFactorValue, CryptoSignal

ControlDecision = Literal["promote", "observe", "reject", "halt"]
EvidenceLevel = Literal["fixture_smoke", "pit_backtest", "strategy_evidence"]


@dataclass(frozen=True)
class PITValidationSummary:
    input_rows: int
    visible_rows: int
    rejected_future_rows: int
    duplicate_rows: int
    non_monotonic_symbols: tuple[str, ...] = ()
    source_update_before_available_rows: int = 0
    has_funding_evidence: bool = False


@dataclass(frozen=True)
class ControlReport:
    decision: ControlDecision
    evidence_level: EvidenceLevel
    objective: dict
    observed_state: dict
    error_terms: dict
    invariants: dict[str, bool]
    halt_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest_hash: str = ""
    dataset_version: str = ""


@dataclass(frozen=True)
class ControlConfig:
    market_type: str
    allow_short: bool
    leverage: float
    max_drawdown_limit: float = 0.30
    min_visible_rows: int = 21
    source_is_fixture: bool = False


def summarize_pit(
    frame: pl.DataFrame,
    visible: pl.DataFrame,
    *,
    has_funding_evidence: bool | None = None,
) -> PITValidationSummary:
    duplicate_rows = (
        frame.select(pl.struct(["inst_id", "event_time"]).is_duplicated().sum()).item()
        if {"inst_id", "event_time"}.issubset(frame.columns)
        else 0
    )
    non_monotonic: list[str] = []
    if {"inst_id", "event_time"}.issubset(frame.columns):
        for inst_id in frame["inst_id"].unique().to_list():
            times = frame.filter(pl.col("inst_id") == inst_id)["event_time"].to_list()
            if any(times[idx] < times[idx - 1] for idx in range(1, len(times))):
                non_monotonic.append(str(inst_id))
    source_update_before_available = (
        frame.filter(pl.col("source_updated_at") < pl.col("available_at")).height
        if {"source_updated_at", "available_at"}.issubset(frame.columns)
        else 0
    )
    observed_funding = "funding_rate" in frame.columns and frame["funding_rate"].is_not_null().sum() > 0
    return PITValidationSummary(
        input_rows=frame.height,
        visible_rows=visible.height,
        rejected_future_rows=frame.height - visible.height,
        duplicate_rows=int(duplicate_rows),
        non_monotonic_symbols=tuple(sorted(non_monotonic)),
        source_update_before_available_rows=source_update_before_available,
        has_funding_evidence=bool(observed_funding if has_funding_evidence is None else has_funding_evidence),
    )


def evaluate_control(
    *,
    config: ControlConfig,
    pit_summary: PITValidationSummary,
    factors: tuple[CryptoFactorValue, ...],
    signals: tuple[CryptoSignal, ...],
    ledger_results: dict[str, LedgerBacktestResult],
    manifest_hash: str,
    dataset_version: str,
) -> ControlReport:
    halt_reasons: list[str] = []
    warnings: list[str] = []
    reject_reasons: list[str] = []
    observe_reasons: list[str] = []

    if pit_summary.visible_rows == 0:
        halt_reasons.append("no PIT rows visible at as_of")
    if pit_summary.duplicate_rows:
        halt_reasons.append("duplicate PIT event rows")
    if pit_summary.non_monotonic_symbols:
        halt_reasons.append("non-monotonic PIT event_time")
    if pit_summary.source_update_before_available_rows:
        halt_reasons.append("source_updated_at before available_at")
    if config.market_type == "spot" and any(signal.side == "short" for signal in signals):
        halt_reasons.append("spot signal emitted short")
    if config.market_type == "swap" and not config.allow_short and any(
        signal.side == "short" for signal in signals
    ):
        halt_reasons.append("swap short emitted without allow_short")
    if not manifest_hash:
        halt_reasons.append("missing manifest hash")
    if not dataset_version:
        halt_reasons.append("missing dataset version")

    if pit_summary.visible_rows < config.min_visible_rows:
        observe_reasons.append("sample shorter than minimum visible rows")
    if not signals:
        observe_reasons.append("no signals generated")
    if not ledger_results:
        observe_reasons.append("no ledger backtests generated")

    max_drawdown = max((result.max_drawdown for result in ledger_results.values()), default=0.0)
    trade_count = sum(len(result.trades) for result in ledger_results.values())
    final_equity_min = min((result.final_equity for result in ledger_results.values()), default=0.0)
    if max_drawdown > config.max_drawdown_limit:
        reject_reasons.append("max drawdown exceeds control limit")
    if ledger_results and trade_count == 0:
        observe_reasons.append("ledger backtests produced no completed trades")
    if ledger_results and final_equity_min <= 0:
        halt_reasons.append("ledger equity is non-positive")

    factor_names = {factor.factor for factor in factors}
    if not factors:
        observe_reasons.append("no factor values generated")
    if factor_names and factor_names != {"momentum_20", "volatility_20", "funding_pressure"}:
        halt_reasons.append("unexpected crypto factor set")

    if config.market_type == "swap" and not pit_summary.has_funding_evidence:
        warnings.append("swap session lacks funding evidence; evidence level capped at fixture_smoke")

    evidence_level: EvidenceLevel = "strategy_evidence"
    if config.source_is_fixture or (config.market_type == "swap" and not pit_summary.has_funding_evidence):
        evidence_level = "fixture_smoke"
    elif observe_reasons:
        evidence_level = "pit_backtest"

    if halt_reasons:
        decision: ControlDecision = "halt"
    elif reject_reasons:
        decision = "reject"
    elif observe_reasons:
        decision = "observe"
    else:
        decision = "promote"

    error_terms = {
        "max_drawdown": max_drawdown,
        "max_drawdown_limit": config.max_drawdown_limit,
        "trade_count": float(trade_count),
        "visible_rows_shortfall": float(max(0, config.min_visible_rows - pit_summary.visible_rows)),
        "reject_reasons": reject_reasons,
        "observe_reasons": observe_reasons,
    }
    observed_state = {
        "market_type": config.market_type,
        "allow_short": config.allow_short,
        "leverage": config.leverage,
        "input_rows": pit_summary.input_rows,
        "visible_rows": pit_summary.visible_rows,
        "rejected_future_rows": pit_summary.rejected_future_rows,
        "factor_values": len(factors),
        "signals": len(signals),
        "backtests": len(ledger_results),
        "completed_trades": trade_count,
    }
    invariants = {
        "pit_visible_at_as_of": pit_summary.visible_rows > 0,
        "no_duplicate_events": pit_summary.duplicate_rows == 0,
        "monotonic_event_time": not pit_summary.non_monotonic_symbols,
        "spot_never_short": config.market_type != "spot" or all(signal.side != "short" for signal in signals),
        "swap_short_requires_allow_short": config.market_type != "swap"
        or config.allow_short
        or all(signal.side != "short" for signal in signals),
        "manifest_bound": bool(manifest_hash and dataset_version),
    }
    objective = {
        "decision": "promote only when PIT, factor, signal, backtest, and boundary evidence pass controls",
        "max_drawdown_limit": config.max_drawdown_limit,
        "min_visible_rows": config.min_visible_rows,
    }
    return ControlReport(
        decision=decision,
        evidence_level=evidence_level,
        objective=objective,
        observed_state=observed_state,
        error_terms=error_terms,
        invariants=invariants,
        halt_reasons=tuple(halt_reasons),
        warnings=tuple(warnings),
        manifest_hash=manifest_hash,
        dataset_version=dataset_version,
    )
