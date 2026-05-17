"""Rule-based control layer for A-share research packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl

from app.research.backtest import LedgerBacktestResult

ControlDecision = Literal["promote", "observe", "reject", "halt"]
EvidenceLevel = Literal["fixture_smoke", "pit_backtest", "morning_package_evidence"]


@dataclass(frozen=True)
class ASharePITValidationSummary:
    input_rows: int
    visible_rows: int
    rejected_future_rows: int
    duplicate_rows: int
    non_monotonic_symbols: tuple[str, ...] = ()
    source_update_before_available_rows: int = 0
    suspended_rows: int = 0
    st_rows: int = 0
    limit_up_rows: int = 0
    limit_down_rows: int = 0
    tradability_status_missing_rows: int = 0


@dataclass(frozen=True)
class AShareControlConfig:
    position_cap: float
    max_drawdown_limit: float = 0.20
    min_visible_rows: int = 21
    min_symbol_coverage_ratio: float = 1.0
    reject_symbol_coverage_ratio: float = 0.80
    source_is_fixture: bool = False
    require_tradability_status: bool = True
    max_gross_exposure: float = 1.0
    max_turnover_ratio: float = 1.0
    max_industry_concentration: float = 0.25
    max_liquidity_stress: float = 0.05


@dataclass(frozen=True)
class AShareControlReport:
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


def summarize_ashare_pit(frame: pl.DataFrame, visible: pl.DataFrame) -> ASharePITValidationSummary:
    duplicate_rows = (
        frame.select(pl.struct(["symbol", "event_time"]).is_duplicated().sum()).item()
        if {"symbol", "event_time"}.issubset(frame.columns)
        else 0
    )
    non_monotonic: list[str] = []
    if {"symbol", "event_time"}.issubset(frame.columns):
        for symbol in frame["symbol"].unique().to_list():
            times = frame.filter(pl.col("symbol") == symbol)["event_time"].to_list()
            if any(times[idx] < times[idx - 1] for idx in range(1, len(times))):
                non_monotonic.append(str(symbol))
    source_update_before_available = (
        frame.filter(pl.col("source_updated_at") < pl.col("available_at")).height
        if {"source_updated_at", "available_at"}.issubset(frame.columns)
        else 0
    )
    return ASharePITValidationSummary(
        input_rows=frame.height,
        visible_rows=visible.height,
        rejected_future_rows=frame.height - visible.height,
        duplicate_rows=int(duplicate_rows),
        non_monotonic_symbols=tuple(sorted(non_monotonic)),
        source_update_before_available_rows=source_update_before_available,
        suspended_rows=_true_count(visible, "is_suspended"),
        st_rows=_true_count(visible, "is_st"),
        limit_up_rows=_true_count(visible, "limit_up"),
        limit_down_rows=_true_count(visible, "limit_down"),
        tradability_status_missing_rows=_missing_tradability_status_rows(visible),
    )


def evaluate_ashare_control(
    *,
    config: AShareControlConfig,
    pit_summary: ASharePITValidationSummary,
    requested_symbols: tuple[str, ...] = (),
    visible_symbols: tuple[str, ...] = (),
    factor_values: int,
    signals: int,
    ledger_results: dict[str, LedgerBacktestResult],
    manifest_hash: str,
    dataset_version: str,
    package_decision: str,
    regime: str = "",
    gross_exposure: float = 0.0,
    net_exposure: float = 0.0,
    turnover_ratio: float = 0.0,
    industry_concentration: float = 0.0,
    liquidity_stress: float = 0.0,
    eligible_universe_count: int = 0,
    state_position_limit: float | None = None,
) -> AShareControlReport:
    halt_reasons: list[str] = []
    warnings: list[str] = []
    reject_reasons: list[str] = []
    observe_reasons: list[str] = []
    requested_symbol_set = set(requested_symbols)
    visible_symbol_set = set(visible_symbols)
    visible_requested_symbols = tuple(sorted(requested_symbol_set & visible_symbol_set))
    empty_or_missing_symbols = tuple(sorted(requested_symbol_set - visible_symbol_set))
    coverage_ratio = (
        len(visible_requested_symbols) / len(requested_symbol_set)
        if requested_symbol_set
        else 1.0
    )

    if pit_summary.visible_rows == 0:
        halt_reasons.append("no PIT rows visible at as_of")
    if pit_summary.duplicate_rows:
        halt_reasons.append("duplicate PIT event rows")
    if pit_summary.non_monotonic_symbols:
        halt_reasons.append("non-monotonic PIT event_time")
    if pit_summary.source_update_before_available_rows:
        halt_reasons.append("source_updated_at before available_at")
    if not manifest_hash:
        halt_reasons.append("missing manifest hash")
    if not dataset_version:
        halt_reasons.append("missing dataset version")
    if config.position_cap <= 0 or config.position_cap > 1:
        halt_reasons.append("invalid position cap")
    if not 0 <= config.reject_symbol_coverage_ratio <= config.min_symbol_coverage_ratio <= 1:
        halt_reasons.append("invalid symbol coverage thresholds")
    if config.max_gross_exposure <= 0 or config.max_gross_exposure > 1:
        halt_reasons.append("invalid gross exposure limit")
    if config.max_industry_concentration <= 0 or config.max_industry_concentration > 1:
        halt_reasons.append("invalid industry concentration limit")

    if pit_summary.visible_rows < config.min_visible_rows:
        observe_reasons.append("sample shorter than minimum visible rows")
    if coverage_ratio < config.reject_symbol_coverage_ratio:
        reject_reasons.append("symbol coverage below reject threshold")
    elif coverage_ratio < config.min_symbol_coverage_ratio:
        observe_reasons.append("symbol coverage below observe threshold")
    if config.source_is_fixture:
        observe_reasons.append("fixture data cannot promote")
    if config.require_tradability_status and pit_summary.tradability_status_missing_rows:
        observe_reasons.append("tradability status PIT missing or incomplete")
    if factor_values == 0:
        observe_reasons.append("no factor values generated")
    if signals == 0:
        observe_reasons.append("no signals generated")
    if not ledger_results:
        observe_reasons.append("no ledger backtests generated")
    if eligible_universe_count == 0:
        observe_reasons.append("no research-eligible universe")
    if turnover_ratio > config.max_turnover_ratio:
        reject_reasons.append("turnover exceeds control limit")
    if industry_concentration > config.max_industry_concentration:
        reject_reasons.append("industry concentration exceeds control limit")
    if liquidity_stress > config.max_liquidity_stress:
        reject_reasons.append("liquidity stress exceeds control limit")
    if gross_exposure > config.max_gross_exposure:
        reject_reasons.append("gross exposure exceeds control limit")
    if state_position_limit is not None and gross_exposure > state_position_limit:
        reject_reasons.append("gross exposure exceeds state regime limit")

    max_drawdown = max((result.max_drawdown for result in ledger_results.values()), default=0.0)
    completed_returns = [
        result.total_return
        for result in ledger_results.values()
        if result.trades
    ]
    rejected_orders = sum(1 for result in ledger_results.values() for order in result.orders if order.status == "rejected")
    completed_trades = sum(len(result.trades) for result in ledger_results.values())
    final_equity_min = min((result.final_equity for result in ledger_results.values()), default=0.0)

    if max_drawdown > config.max_drawdown_limit:
        reject_reasons.append("max drawdown exceeds control limit")
    if completed_returns and max(completed_returns) <= 0:
        reject_reasons.append("no completed backtest has positive return")
    if ledger_results and completed_trades == 0:
        observe_reasons.append("ledger backtests produced no completed trades")
    if ledger_results and final_equity_min <= 0:
        halt_reasons.append("ledger equity is non-positive")
    if rejected_orders:
        warnings.append("ledger contains rejected orders from A-share market constraints")
    if pit_summary.suspended_rows or pit_summary.st_rows:
        warnings.append("visible PIT contains suspended or ST rows")
    if pit_summary.limit_up_rows or pit_summary.limit_down_rows:
        warnings.append("visible PIT contains limit-up or limit-down rows")

    evidence_level: EvidenceLevel = "morning_package_evidence"
    if config.source_is_fixture:
        evidence_level = "fixture_smoke"
    elif observe_reasons:
        evidence_level = "pit_backtest"

    if halt_reasons:
        decision: ControlDecision = "halt"
    elif reject_reasons:
        decision = "reject"
    elif observe_reasons or package_decision != "trade":
        decision = "observe"
    else:
        decision = "promote"

    observed_state = {
        "input_rows": pit_summary.input_rows,
        "visible_rows": pit_summary.visible_rows,
        "rejected_future_rows": pit_summary.rejected_future_rows,
        "requested_symbols": tuple(sorted(requested_symbol_set)),
        "visible_symbols": visible_requested_symbols,
        "empty_or_missing_symbols": empty_or_missing_symbols,
        "coverage_ratio": coverage_ratio,
        "requested_symbol_count": len(requested_symbol_set),
        "visible_symbol_count": len(visible_requested_symbols),
        "factor_values": factor_values,
        "signals": signals,
        "backtests": len(ledger_results),
        "completed_trades": completed_trades,
        "rejected_orders": rejected_orders,
        "tradability_status_missing_rows": pit_summary.tradability_status_missing_rows,
        "package_decision": package_decision,
        "position_cap": config.position_cap,
        "regime": regime,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "turnover_ratio": turnover_ratio,
        "industry_concentration": industry_concentration,
        "liquidity_stress": liquidity_stress,
        "eligible_universe_count": eligible_universe_count,
        "state_position_limit": state_position_limit,
    }
    error_terms = {
        "max_drawdown": max_drawdown,
        "max_drawdown_limit": config.max_drawdown_limit,
        "best_completed_return": max(completed_returns) if completed_returns else 0.0,
        "visible_rows_shortfall": float(max(0, config.min_visible_rows - pit_summary.visible_rows)),
        "symbol_coverage_shortfall": float(max(0.0, config.min_symbol_coverage_ratio - coverage_ratio)),
        "min_symbol_coverage_ratio": config.min_symbol_coverage_ratio,
        "reject_symbol_coverage_ratio": config.reject_symbol_coverage_ratio,
        "gross_exposure_limit": config.max_gross_exposure,
        "industry_concentration_limit": config.max_industry_concentration,
        "liquidity_stress_limit": config.max_liquidity_stress,
        "turnover_ratio_limit": config.max_turnover_ratio,
        "reject_reasons": reject_reasons,
        "observe_reasons": observe_reasons,
    }
    invariants = {
        "pit_visible_at_as_of": pit_summary.visible_rows > 0,
        "no_duplicate_events": pit_summary.duplicate_rows == 0,
        "monotonic_event_time": not pit_summary.non_monotonic_symbols,
        "source_update_not_before_available": pit_summary.source_update_before_available_rows == 0,
        "manifest_bound": bool(manifest_hash and dataset_version),
        "position_cap_valid": 0 < config.position_cap <= 1,
        "symbol_coverage_complete": coverage_ratio >= config.min_symbol_coverage_ratio,
        "execution_not_embedded": True,
        "tradability_status_bound": pit_summary.tradability_status_missing_rows == 0,
        "gross_exposure_within_limit": gross_exposure <= config.max_gross_exposure,
        "industry_concentration_within_limit": industry_concentration <= config.max_industry_concentration,
        "liquidity_stress_within_limit": liquidity_stress <= config.max_liquidity_stress,
    }
    objective = {
        "decision": "promote only when PIT, factor, signal, backtest, and morning package evidence pass controls",
        "max_drawdown_limit": config.max_drawdown_limit,
        "min_visible_rows": config.min_visible_rows,
        "min_symbol_coverage_ratio": config.min_symbol_coverage_ratio,
        "reject_symbol_coverage_ratio": config.reject_symbol_coverage_ratio,
        "max_gross_exposure": config.max_gross_exposure,
        "max_turnover_ratio": config.max_turnover_ratio,
        "max_industry_concentration": config.max_industry_concentration,
        "max_liquidity_stress": config.max_liquidity_stress,
    }
    return AShareControlReport(
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


def _true_count(frame: pl.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(frame.filter(pl.col(column).fill_null(False)).height)


def _missing_tradability_status_rows(frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    required = {"is_st", "is_suspended", "limit_up", "limit_down", "listed_days", "is_tradable"}
    if not required.issubset(frame.columns):
        return frame.height
    return int(frame.filter(pl.col("is_tradable").is_null()).height)
