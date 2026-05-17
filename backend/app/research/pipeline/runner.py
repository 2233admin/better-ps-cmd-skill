"""Build the minimum credible A-share research chain."""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl

from app.research.agent import ResearchAgentOutput, ResearchAgentRequest, ResearchAgentRunner
from app.research.ashare_data_contract import ASharePITDataset
from app.research.backtest import (
    AShareBacktestConfig,
    AShareLedgerBacktester,
    LedgerBacktestResult,
    write_backtest_artifacts,
    write_backtest_tables,
)
from app.research.manifest import ExperimentManifest
from app.research.models import (
    Experiment,
    ExperimentStatus,
    Frequency,
    Market,
    PortfolioTarget,
    RebalanceIntent,
    ResearchSignal,
    SignalSide,
)
from app.research.morning_package import RiskBlock, candidates_from_research_output
from app.research.morning_package.builder import build_morning_package
from app.research.morning_package.renderers import write_artifacts as write_morning_artifacts
from app.research.pit import PITDataset
from app.research.pipeline.control import (
    AShareControlConfig,
    AShareControlReport,
    ASharePITValidationSummary,
    evaluate_ashare_control,
    summarize_ashare_pit,
)
from app.research.pipeline.data_lake import (
    resolve_adjustment_factor_parquet,
    resolve_kline_daily_parquet,
    resolve_tradability_status_parquet,
    resolve_trading_calendar_parquet,
)
from app.research.snapshot import DatasetSnapshotGenerator


@dataclass(frozen=True)
class PipelineConfig:
    package_date: date
    symbols: tuple[str, ...]
    out_dir: Path
    pit_parquet: Path | None = None
    data_root: Path | None = None
    world_snapshot: Path | None = None
    code_commit: str = "manual"
    initial_capital: float = 1_000_000.0
    position_cap: float = 0.05
    failure_condition: str = "Momentum falls below zero or PIT data quality changes."
    random_seed: int = 42
    artifact_level: Literal["summary", "full"] = "summary"
    rebalance_mode: Literal["daily", "weekly"] = "weekly"
    max_positions: int = 20
    industry_cap: float = 0.25
    min_liquidity_threshold: float = 0.0
    state_aware: bool = True


class ParquetPITStore:
    def __init__(self, frame: pl.DataFrame) -> None:
        self.frame = frame

    def query(self, request) -> pl.DataFrame:
        frame = self.frame
        if "symbol" in frame.columns:
            frame = frame.filter(pl.col("symbol") == request.symbol)
        if "available_at" in frame.columns:
            frame = frame.filter(pl.col("available_at") <= request.as_of)
        time_col = _time_column(frame)
        if request.start is not None:
            frame = frame.filter(pl.col(time_col) >= _date_value(request.start))
        if request.end is not None:
            frame = frame.filter(pl.col(time_col) <= _date_value(request.end))
        frame = frame.sort(time_col)
        if "date" not in frame.columns and time_col == "event_time":
            frame = frame.with_columns(pl.col("event_time").dt.date().alias("date"))
        return frame


@dataclass(frozen=True)
class PipelineResult:
    manifest: ExperimentManifest
    ledger_backtests: dict[str, LedgerBacktestResult]
    morning_package_dir: Path
    control_report: AShareControlReport
    portfolio_targets: tuple[PortfolioTarget, ...] = ()
    rebalance_intent: RebalanceIntent | None = None


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    source_is_fixture = config.pit_parquet is None and config.data_root is None
    pit_path = _resolve_input_parquet(config)
    pit_path, pit_frame = _materialize_pit_input(config, pit_path)
    as_of = datetime(config.package_date.year, config.package_date.month, config.package_date.day, 23, 59, 59, tzinfo=UTC)
    pit_frame, pit_summary = _validate_pit(pit_frame, as_of=as_of)
    pit_path = config.out_dir / "visible_kline_daily_pit.parquet"
    pit_frame.write_parquet(pit_path)
    start = _min_datetime(pit_frame)
    end = _max_datetime(pit_frame)

    snapshot_result = DatasetSnapshotGenerator().from_parquet(
        pit_path,
        dataset=ASharePITDataset.KLINE_DAILY,
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        as_of=as_of,
        version=config.package_date.isoformat(),
        source="pipeline.parquet",
    )
    DatasetSnapshotGenerator().write_json(snapshot_result, config.out_dir)

    market_state = _market_state(config, pit_frame, as_of)
    factor_names = ("momentum_20d", "volatility_20d", "turnover_pressure_20d")
    runner = ResearchAgentRunner()
    request = ResearchAgentRequest(
        name="ashare_state_aware_cross_section_pipeline",
        symbols=config.symbols,
        start=start,
        end=end,
        as_of=as_of,
        dataset_version=snapshot_result.version,
        dataset=PITDataset.KLINE_DAILY,
        factor_names=factor_names,
        entry_threshold=float(market_state["entry_threshold"]),
        exit_threshold=float(market_state["exit_threshold"]),
    )
    plan_output = runner.build_plan(request)
    factor_frame = _calculate_factor_frame(pit_frame, request.factor_names, as_of=as_of)
    target_frame = _build_portfolio_target_frame(config, pit_frame, factor_frame, market_state)
    signal_frame = _calculate_signal_frame(
        factor_frame,
        target_frame,
        as_of=as_of,
        entry_threshold=request.entry_threshold,
        exit_threshold=request.exit_threshold,
        state_tag=str(market_state["regime"]),
    )
    output = _research_output_from_frames(plan_output, request, factor_frame, signal_frame)
    manifest = runner.promote_to_manifest(
        output,
        code_commit=config.code_commit,
        data_versions=(snapshot_result.version,),
        config={
            "symbols": config.symbols,
            "position_cap": config.position_cap,
            "initial_capital": config.initial_capital,
            "strategy": "state_aware_cross_section",
            "rebalance_mode": config.rebalance_mode,
            "max_positions": config.max_positions,
            "industry_cap": config.industry_cap,
            "market_regime": market_state["regime"],
        },
        random_seed=config.random_seed,
    )
    manifest_hash = manifest.manifest_hash()
    _write_json(config.out_dir / "manifest.json", manifest.to_payload() | {"manifest_hash": manifest_hash})
    _write_factor_artifacts(config.out_dir, factor_frame, artifact_level=config.artifact_level)
    _write_signal_artifacts(config.out_dir, signal_frame, artifact_level=config.artifact_level)
    portfolio_targets = _portfolio_targets(target_frame, state_tag=str(market_state["regime"]))
    _write_portfolio_artifacts(config.out_dir, target_frame, market_state)

    ledger_results = _run_ledger_backtests(config, pit_frame, signal_frame)
    if config.artifact_level == "full":
        for symbol, result in ledger_results.items():
            write_backtest_artifacts(result, config.out_dir / "backtest" / symbol)
    if ledger_results:
        first_symbol = sorted(ledger_results)[0]
        write_backtest_artifacts(ledger_results[first_symbol], config.out_dir / "backtest")
    write_backtest_tables(ledger_results, config.out_dir)
    _write_scan_results(config.out_dir, config.symbols, pit_frame, factor_frame, signal_frame, ledger_results, target_frame)

    risk = RiskBlock(
        market_risks=(f"regime={market_state['regime']}", f"risk_score={float(market_state['risk_score']):.4f}"),
        data_integrity=("PIT dataset lineage required",),
        portfolio_limits=(
            f"position_cap={config.position_cap}",
            f"max_positions={config.max_positions}",
            f"industry_cap={config.industry_cap}",
        ),
    )
    candidates = candidates_from_research_output(
        output,
        dataset_version=snapshot_result.version,
        manifest_hash=manifest_hash,
        position_caps={target.symbol: target.target_weight for target in portfolio_targets},
        failure_conditions={symbol: config.failure_condition for symbol in config.symbols},
        ledger_backtests=ledger_results,
        allowed_sides=("buy",),
        max_candidates=min(3, config.max_positions),
    )
    package = build_morning_package(
        package_date=config.package_date,
        candidates=candidates,
        manifest=manifest,
        manifest_hash=manifest_hash,
        dataset_version=snapshot_result.version,
        code_commit=config.code_commit,
        risk=risk,
        audit_context={
            "market_state": market_state,
            "portfolio_targets": [asdict(target) for target in portfolio_targets],
        },
    )
    morning_dir = config.out_dir / "morning_package"
    write_morning_artifacts(package, morning_dir)

    portfolio_metrics = _portfolio_metrics(target_frame, config)
    control_report = evaluate_ashare_control(
        config=AShareControlConfig(
            position_cap=config.position_cap,
            source_is_fixture=source_is_fixture,
            max_industry_concentration=config.industry_cap,
            max_gross_exposure=1.0,
        ),
        pit_summary=pit_summary,
        requested_symbols=config.symbols,
        visible_symbols=_visible_symbols(pit_frame),
        factor_values=factor_frame.height,
        signals=signal_frame.height,
        ledger_results=ledger_results,
        manifest_hash=manifest_hash,
        dataset_version=snapshot_result.version.version,
        package_decision=package.decision.value,
        regime=str(market_state["regime"]),
        gross_exposure=portfolio_metrics["gross_exposure"],
        net_exposure=portfolio_metrics["net_exposure"],
        turnover_ratio=portfolio_metrics["turnover_ratio"],
        industry_concentration=portfolio_metrics["industry_concentration"],
        liquidity_stress=portfolio_metrics["liquidity_stress"],
        eligible_universe_count=int(market_state["eligible_universe_count"]),
        state_position_limit=float(market_state["position_scale"]),
    )
    _write_control_artifacts(morning_dir, control_report)
    _merge_control_into_audit(morning_dir / "audit.json", control_report)
    attribution = _attribution_payload(market_state, target_frame, ledger_results, control_report)
    _write_json(config.out_dir / "attribution.json", attribution)
    intent = _rebalance_intent(
        as_of=as_of,
        targets=portfolio_targets,
        control_report=control_report,
        dataset_version=snapshot_result.version.version,
        manifest_hash=manifest_hash,
        rebalance_mode=config.rebalance_mode,
    )
    _write_json(config.out_dir / "rebalance_intent.json", asdict(intent))
    return PipelineResult(
        manifest=manifest,
        ledger_backtests=ledger_results,
        morning_package_dir=morning_dir,
        control_report=control_report,
        portfolio_targets=portfolio_targets,
        rebalance_intent=intent,
    )


def _resolve_input_parquet(config: PipelineConfig) -> Path:
    if config.pit_parquet is not None:
        return config.pit_parquet
    if config.data_root is not None:
        return resolve_kline_daily_parquet(config.data_root, config.symbols)
    return _write_fixture_parquet(config)


def _materialize_pit_input(config: PipelineConfig, source_path: Path) -> tuple[Path, pl.DataFrame]:
    frame = pl.read_parquet(source_path)
    if {"symbol", "event_time", "available_at", "source_updated_at"}.issubset(set(frame.columns)):
        return source_path, _merge_optional_pit_layers(config, frame)
    if {"code", "market", "date", "open", "high", "low", "close", "volume", "amount"}.issubset(
        set(frame.columns)
    ):
        calendar = _load_trading_calendar(config.data_root) if config.data_root is not None else None
        normalized = _normalize_legacy_kline(frame, config.symbols, calendar=calendar)
        path = config.out_dir / "normalized_kline_daily_pit.parquet"
        normalized.write_parquet(path)
        return path, _merge_optional_pit_layers(config, normalized)
    return source_path, frame


def _validate_pit(frame: pl.DataFrame, *, as_of: datetime) -> tuple[pl.DataFrame, ASharePITValidationSummary]:
    if "available_at" not in frame.columns:
        raise ValueError("A-share PIT input is missing available_at")
    visible = frame.filter(pl.col("available_at") <= as_of)
    summary = summarize_ashare_pit(frame, visible)
    if visible.is_empty():
        raise ValueError("A-share PIT input has no rows visible at as_of")
    return visible.sort(["symbol", "event_time"] if "symbol" in visible.columns else [_time_column(visible)]), summary


def _market_state(config: PipelineConfig, pit_frame: pl.DataFrame, as_of: datetime) -> dict[str, Any]:
    if config.world_snapshot is not None:
        state_path = config.world_snapshot.with_name("market_state.json")
        if state_path.exists():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            return _apply_state_override(config, payload)
        snapshot = pl.read_parquet(config.world_snapshot)
        if not snapshot.is_empty() and {"market_state", "risk_score"}.issubset(snapshot.columns):
            payload = {
                "as_of": as_of.isoformat(),
                "regime": str(snapshot["market_state"].drop_nulls().to_list()[0] or "neutral"),
                "risk_score": float(snapshot["risk_score"].drop_nulls().to_list()[0] or 0.5),
                "visible_symbol_count": snapshot.height,
                "eligible_universe_count": snapshot.filter(pl.col("research_eligible")).height
                if "research_eligible" in snapshot.columns
                else snapshot.height,
            }
            return _apply_state_override(config, payload)

    breadth = _breadth_up_ratio(pit_frame)
    volatility = _median_volatility_20d(pit_frame)
    tradable_ratio = _column_ratio(pit_frame, "is_tradable", default=True)
    limit_hit_ratio = _boolean_ratio(pit_frame, ("limit_up", "limit_down"))
    risk_score = min(max((1.0 - breadth) * 0.45 + min(volatility / 0.08, 1.0) * 0.25 + (1.0 - tradable_ratio) * 0.15 + limit_hit_ratio * 0.15, 0.0), 1.0)
    regime, position_scale, entry_threshold, exit_threshold = _regime_policy(risk_score, breadth, tradable_ratio)
    payload = {
        "as_of": as_of.isoformat(),
        "regime": regime,
        "risk_score": risk_score,
        "position_scale": position_scale,
        "entry_threshold": entry_threshold,
        "exit_threshold": exit_threshold,
        "breadth_up_ratio": breadth,
        "tradable_ratio": tradable_ratio,
        "research_eligible_ratio": tradable_ratio,
        "limit_hit_ratio": limit_hit_ratio,
        "median_volatility_20d": volatility,
        "median_amount": float(pit_frame["amount"].median()) if "amount" in pit_frame.columns and pit_frame.height else 0.0,
        "visible_symbol_count": len(_visible_symbols(pit_frame)),
        "eligible_universe_count": len(_visible_symbols(_eligible_frame(pit_frame, min_liquidity=config.min_liquidity_threshold))),
        "suggested_rebalance_mode": config.rebalance_mode,
    }
    return _apply_state_override(config, payload)


def _apply_state_override(config: PipelineConfig, payload: dict[str, Any]) -> dict[str, Any]:
    if not config.state_aware:
        payload = dict(payload) | {
            "regime": "neutral",
            "risk_score": 0.5,
            "position_scale": 1.0,
            "entry_threshold": 0.01,
            "exit_threshold": -0.02,
        }
    payload.setdefault("position_scale", 0.65)
    payload.setdefault("entry_threshold", 0.015)
    payload.setdefault("exit_threshold", -0.02)
    payload.setdefault("eligible_universe_count", 0)
    payload.setdefault("suggested_rebalance_mode", config.rebalance_mode)
    return payload


def _regime_policy(risk_score: float, breadth: float, eligible_ratio: float) -> tuple[str, float, float, float]:
    if risk_score >= 0.65 or breadth < 0.40 or eligible_ratio < 0.70:
        return "risk_off", 0.35, 0.03, -0.01
    if risk_score <= 0.35 and breadth >= 0.55 and eligible_ratio >= 0.85:
        return "risk_on", 1.0, 0.01, -0.03
    return "neutral", 0.65, 0.015, -0.02


def _run_ledger_backtests(config: PipelineConfig, pit_frame: pl.DataFrame, signal_frame: pl.DataFrame) -> dict[str, LedgerBacktestResult]:
    results: dict[str, LedgerBacktestResult] = {}
    buy_signal_dates = _buy_signal_dates_by_symbol(signal_frame)
    backtester = AShareLedgerBacktester(AShareBacktestConfig(initial_capital=config.initial_capital, position_cap=config.position_cap))
    for symbol in config.symbols:
        bars = pit_frame.filter(pl.col("symbol") == symbol) if "symbol" in pit_frame.columns else pit_frame
        if bars.is_empty():
            continue
        ledger_signals = _ledger_signal_frame(bars, buy_signal_dates.get(symbol, set()))
        results[symbol] = backtester.run(bars, ledger_signals, symbol=symbol)
    return results


def _ledger_signal_frame(bars: pl.DataFrame, signal_dates: set[date]) -> pl.DataFrame:
    time_col = _time_column(bars)
    dates = bars.sort(time_col)[time_col].to_list()
    values = [0] * len(dates)
    if signal_dates and values:
        for idx, value in enumerate(dates):
            if _date_value(value) in signal_dates:
                values[idx] = 1
                break
        if len(values) > 1 and any(value == 1 for value in values):
            values[-1] = -1
    return pl.DataFrame({time_col: dates, "signal": values})


def _calculate_factor_frame(pit_frame: pl.DataFrame, factor_names: tuple[str, ...], *, as_of: datetime) -> pl.DataFrame:
    if "symbol" not in pit_frame.columns:
        raise ValueError("A-share factor pipeline requires symbol")
    if "close" not in pit_frame.columns:
        raise ValueError("A-share factor pipeline requires close")
    time_col = _time_column(pit_frame)
    frame = pit_frame.sort(["symbol", time_col])
    price_column = "adjusted_close" if "adjusted_close" in frame.columns else "close"
    expressions = []
    if "momentum_20d" in factor_names:
        expressions.append((pl.col(price_column) / pl.col(price_column).shift(20) - 1).over("symbol").alias("momentum_20d"))
    if "volatility_20d" in factor_names:
        expressions.append(pl.col(price_column).pct_change().rolling_std(20).over("symbol").alias("volatility_20d"))
    if "turnover_pressure_20d" in factor_names:
        if "volume" not in frame.columns or "amount" not in frame.columns:
            raise ValueError("turnover_pressure_20d requires volume and amount")
        expressions.append(((pl.col("amount") / pl.col("amount").rolling_mean(20)) + (pl.col("volume") / pl.col("volume").rolling_mean(20))).over("symbol").alias("turnover_pressure_20d"))
    if not expressions:
        return pl.DataFrame(schema={"factor": pl.Utf8, "symbol": pl.Utf8, "timestamp": pl.Datetime(time_zone="UTC"), "value": pl.Float64, "as_of": pl.Datetime(time_zone="UTC"), "metadata": pl.Utf8})
    wide = frame.with_columns(expressions)
    return (
        wide.select("symbol", pl.col(time_col).alias("timestamp"), *factor_names)
        .unpivot(index=["symbol", "timestamp"], on=list(factor_names), variable_name="factor", value_name="value")
        .drop_nulls("value")
        .with_columns(pl.lit(as_of).alias("as_of"), pl.lit("{}").alias("metadata"))
        .select("factor", "symbol", "timestamp", "value", "as_of", "metadata")
    )


def _build_portfolio_target_frame(config: PipelineConfig, pit_frame: pl.DataFrame, factor_frame: pl.DataFrame, market_state: dict[str, Any]) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8,
        "rank": pl.Int64,
        "score": pl.Float64,
        "target_weight": pl.Float64,
        "state_tag": pl.Utf8,
        "industry": pl.Utf8,
        "eligibility_reason": pl.Utf8,
        "latest_event_time": pl.Datetime(time_zone="UTC"),
        "latest_close": pl.Float64,
        "amount": pl.Float64,
    }
    if factor_frame.is_empty() or pit_frame.is_empty():
        return pl.DataFrame(schema=schema)
    time_col = _time_column(pit_frame)
    latest = (
        pit_frame.sort(["symbol", time_col])
        .group_by("symbol", maintain_order=True)
        .last()
        .rename({time_col: "latest_event_time", "close": "latest_close"})
    )
    visible_counts = pit_frame.group_by("symbol").len().rename({"len": "visible_rows"})
    factors = (
        factor_frame.sort("timestamp")
        .group_by(["symbol", "factor"], maintain_order=True)
        .last()
        .select("symbol", "factor", "value")
        .pivot(index="symbol", on="factor", values="value")
    )
    frame = latest.join(visible_counts, on="symbol", how="left").join(factors, on="symbol", how="left")
    frame = _with_default_columns(
        frame,
        {
            "is_st": False,
            "is_suspended": False,
            "limit_up": False,
            "limit_down": False,
            "is_tradable": True,
            "listed_days": 9999,
            "reason": "eligible",
            "industry": "",
            "momentum_20d": 0.0,
            "volatility_20d": 0.0,
            "turnover_pressure_20d": 0.0,
        },
    )
    amount_ok = pl.col("amount").fill_null(0.0) >= config.min_liquidity_threshold
    eligible = (
        (pl.col("visible_rows") >= 21)
        & (~pl.col("is_st").fill_null(False))
        & (~pl.col("is_suspended").fill_null(False))
        & (~pl.col("limit_up").fill_null(False))
        & pl.col("is_tradable").fill_null(True)
        & (pl.col("listed_days").fill_null(9999) >= 21)
        & amount_ok
    )
    entry_threshold = float(market_state["entry_threshold"])
    scored = frame.with_columns(
        (
            pl.col("momentum_20d").fill_null(0.0)
            - pl.col("volatility_20d").fill_null(0.0) * 0.5
            + (pl.col("turnover_pressure_20d").fill_null(0.0) * 0.01)
        ).alias("score"),
        eligible.alias("_eligible"),
        pl.when(eligible)
        .then(pl.lit("eligible"))
        .when(pl.col("visible_rows") < 21)
        .then(pl.lit("insufficient visible rows"))
        .when(pl.col("is_st").fill_null(False))
        .then(pl.lit("st_stock"))
        .when(pl.col("is_suspended").fill_null(False))
        .then(pl.lit("suspended"))
        .when(pl.col("limit_up").fill_null(False))
        .then(pl.lit("limit_up"))
        .when(~pl.col("is_tradable").fill_null(True))
        .then(pl.col("reason").fill_null("not_tradable"))
        .when(~amount_ok)
        .then(pl.lit("liquidity below threshold"))
        .otherwise(pl.lit("not eligible"))
        .alias("eligibility_reason"),
        pl.when(pl.col("industry").cast(pl.Utf8).str.len_chars() > 0).then(pl.col("industry")).otherwise(pl.col("symbol")).alias("industry"),
    )
    candidates = (
        scored.filter(pl.col("_eligible") & (pl.col("momentum_20d").fill_null(0.0) > entry_threshold))
        .sort(["score", "symbol"], descending=[True, False])
        .head(max(config.max_positions, 0))
        .with_row_index("rank", offset=1)
    )
    if candidates.is_empty():
        return pl.DataFrame(schema=schema)
    total_exposure = min(float(market_state["position_scale"]), config.max_positions * config.position_cap, 1.0)
    target_weight = min(config.position_cap, total_exposure / candidates.height) if candidates.height else 0.0
    return (
        candidates.with_columns(
            pl.lit(target_weight).alias("target_weight"),
            pl.lit(str(market_state["regime"])).alias("state_tag"),
        )
        .select(list(schema))
        .sort("rank")
    )


def _calculate_signal_frame(
    factor_frame: pl.DataFrame,
    target_frame: pl.DataFrame,
    *,
    as_of: datetime,
    entry_threshold: float,
    exit_threshold: float,
    state_tag: str,
) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8,
        "side": pl.Utf8,
        "timestamp": pl.Datetime(time_zone="UTC"),
        "as_of": pl.Datetime(time_zone="UTC"),
        "strategy": pl.Utf8,
        "confidence": pl.Float64,
        "reason": pl.Utf8,
        "horizon": pl.Utf8,
        "state_tag": pl.Utf8,
        "score": pl.Float64,
        "target_weight": pl.Float64,
    }
    if factor_frame.is_empty() or target_frame.is_empty():
        return pl.DataFrame(schema=schema)
    momentum = (
        factor_frame.filter(pl.col("factor") == "momentum_20d")
        .sort("timestamp")
        .select("symbol", pl.col("timestamp"), pl.col("value").alias("momentum_20d"))
    )
    frame = (
        momentum.filter(pl.col("momentum_20d").fill_null(0.0) > entry_threshold)
        .join(target_frame.select("symbol", "score", "target_weight", "state_tag"), on="symbol", how="inner")
        .sort(["timestamp", "symbol"])
        .group_by("symbol", maintain_order=True)
        .first()
    )
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    confidence = pl.col("score").abs() / 0.10
    return (
        frame.with_columns(
            pl.lit("buy").alias("side"),
            pl.lit(as_of).alias("as_of"),
            pl.lit("state_aware_cross_section").alias("strategy"),
            pl.when(confidence > 1.0).then(1.0).otherwise(confidence).alias("confidence"),
            (
                pl.lit("state=")
                + pl.lit(state_tag)
                + pl.lit("; score=")
                + pl.col("score").round(4).cast(pl.Utf8)
                + pl.lit("; momentum_20d=")
                + pl.col("momentum_20d").round(4).cast(pl.Utf8)
            ).alias("reason"),
            pl.lit("1d").alias("horizon"),
        )
        .select(list(schema))
        .sort(["timestamp", "symbol"])
    )


def _research_output_from_frames(plan_output: ResearchAgentOutput, request: ResearchAgentRequest, factor_frame: pl.DataFrame, signal_frame: pl.DataFrame) -> ResearchAgentOutput:
    experiment = Experiment(
        name=plan_output.experiment.name,
        market=plan_output.experiment.market,
        datasets=plan_output.experiment.datasets,
        factors=plan_output.factors,
        start=plan_output.experiment.start,
        end=plan_output.experiment.end,
        as_of=plan_output.experiment.as_of,
        id=plan_output.experiment.id,
        status=ExperimentStatus.COMPLETED,
        hypothesis=plan_output.experiment.hypothesis,
        metrics={
            "requested_symbols": float(len(request.symbols)),
            "symbols_scanned": float(factor_frame.select("symbol").unique().height if not factor_frame.is_empty() else 0),
            "factor_values": float(factor_frame.height),
            "signals_generated": float(signal_frame.height),
        },
    )
    return ResearchAgentOutput(factors=plan_output.factors, experiment=experiment, signals=_candidate_signals(signal_frame, as_of=request.as_of), factor_values=())


def _candidate_signals(signal_frame: pl.DataFrame, *, as_of: datetime, limit: int = 20) -> tuple[ResearchSignal, ...]:
    if signal_frame.is_empty():
        return ()
    rows = (
        signal_frame.filter(pl.col("side") == "buy")
        .sort(["score", "timestamp", "symbol"], descending=[True, False, False])
        .group_by("symbol", maintain_order=True)
        .first()
        .head(limit)
        .to_dicts()
    )
    return tuple(
        ResearchSignal(
            symbol=str(row["symbol"]),
            side=SignalSide.BUY,
            timestamp=_as_utc_datetime(row["timestamp"]),
            as_of=as_of,
            strategy=str(row["strategy"]),
            confidence=float(row["confidence"]),
            reason=str(row["reason"]),
            horizon=str(row["horizon"]),
            state_tag=str(row.get("state_tag") or ""),
            score=float(row["score"]) if row.get("score") is not None else None,
        )
        for row in rows
    )


def _buy_signal_dates_by_symbol(signal_frame: pl.DataFrame) -> dict[str, set[date]]:
    if signal_frame.is_empty():
        return {}
    rows = signal_frame.filter(pl.col("side") == "buy").group_by("symbol").agg(pl.col("timestamp")).to_dicts()
    return {str(row["symbol"]): {_date_value(value) for value in row["timestamp"]} for row in rows}


def _write_scan_results(
    out_dir: Path,
    symbols: tuple[str, ...],
    pit_frame: pl.DataFrame,
    factor_frame: pl.DataFrame,
    signal_frame: pl.DataFrame,
    ledger_results: dict[str, LedgerBacktestResult],
    target_frame: pl.DataFrame,
) -> None:
    rows = _scan_result_rows(symbols, pit_frame, factor_frame, signal_frame, ledger_results, target_frame)
    _write_json(out_dir / "scan_results.json", rows)
    if rows:
        frame = pl.DataFrame(rows)
        frame.write_csv(out_dir / "scan_results.csv")
        frame.write_parquet(out_dir / "scan_results.parquet")


def _scan_result_rows(
    symbols: tuple[str, ...],
    pit_frame: pl.DataFrame,
    factor_frame: pl.DataFrame,
    signal_frame: pl.DataFrame,
    ledger_results: dict[str, LedgerBacktestResult],
    target_frame: pl.DataFrame,
) -> list[dict[str, Any]]:
    time_col = _time_column(pit_frame)
    latest_factors = _latest_factors_by_symbol(factor_frame)
    signal_counts, buy_counts, sell_counts, latest_signal_by_symbol = _signal_summaries(signal_frame)
    targets = {str(row["symbol"]): row for row in target_frame.to_dicts()} if not target_frame.is_empty() else {}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        bars = pit_frame.filter(pl.col("symbol") == symbol) if "symbol" in pit_frame.columns else pit_frame
        latest_bar = bars.sort(time_col).tail(1).to_dicts()[0] if not bars.is_empty() else {}
        result = ledger_results.get(symbol)
        metrics = result.metrics() if result is not None else {}
        latest_signal = latest_signal_by_symbol.get(symbol, {})
        target = targets.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "visible_rows": bars.height,
                "latest_event_time": latest_bar.get(time_col),
                "latest_close": latest_bar.get("close"),
                "momentum_20d": latest_factors.get((symbol, "momentum_20d")),
                "volatility_20d": latest_factors.get((symbol, "volatility_20d")),
                "turnover_pressure_20d": latest_factors.get((symbol, "turnover_pressure_20d")),
                "score": target.get("score"),
                "target_weight": target.get("target_weight"),
                "state_tag": target.get("state_tag", ""),
                "eligibility_reason": target.get("eligibility_reason", "not selected"),
                "signals": signal_counts.get(symbol, 0),
                "buy_signals": buy_counts.get(symbol, 0),
                "sell_signals": sell_counts.get(symbol, 0),
                "last_signal_side": latest_signal.get("side", ""),
                "last_signal_at": latest_signal.get("timestamp"),
                "backtest_status": "completed" if result is not None else "missing",
                "completed_trades": len(result.trades) if result is not None else 0,
                "rejected_orders": sum(1 for order in result.orders if order.status == "rejected") if result is not None else 0,
                "total_return": metrics.get("total_return"),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
                "turnover": metrics.get("turnover"),
                "final_equity": metrics.get("final_equity"),
            }
        )
    return rows


def _latest_factors_by_symbol(factor_frame: pl.DataFrame) -> dict[tuple[str, str], float]:
    if factor_frame.is_empty():
        return {}
    rows = factor_frame.sort("timestamp").group_by(["symbol", "factor"], maintain_order=True).last().select("symbol", "factor", "value").to_dicts()
    return {(str(row["symbol"]), str(row["factor"])): float(row["value"]) for row in rows}


def _signal_summaries(signal_frame: pl.DataFrame) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
    if signal_frame.is_empty():
        return {}, {}, {}, {}
    signal_counts = {str(row["symbol"]): int(row["len"]) for row in signal_frame.group_by("symbol").len().to_dicts()}
    side_counts = signal_frame.group_by(["symbol", "side"]).len().to_dicts()
    buy_counts = {str(row["symbol"]): int(row["len"]) for row in side_counts if str(row["side"]) == "buy"}
    sell_counts = {str(row["symbol"]): int(row["len"]) for row in side_counts if str(row["side"]) == "sell"}
    latest = {str(row["symbol"]): row for row in signal_frame.sort("timestamp").group_by("symbol", maintain_order=True).last().to_dicts()}
    return signal_counts, buy_counts, sell_counts, latest


def _visible_symbols(pit_frame: pl.DataFrame) -> tuple[str, ...]:
    if "symbol" not in pit_frame.columns:
        return ()
    return tuple(sorted(str(symbol) for symbol in pit_frame["symbol"].unique().to_list()))


def _write_fixture_parquet(config: PipelineConfig) -> Path:
    rows: list[dict[str, Any]] = []
    for symbol in config.symbols:
        for idx in range(45):
            current = config.package_date - timedelta(days=44 - idx)
            close = 10.0 + idx * 0.08
            rows.append(
                {
                    "symbol": symbol,
                    "market": "SH" if symbol.startswith("6") else "SZ",
                    "event_time": datetime(current.year, current.month, current.day, tzinfo=UTC),
                    "available_at": datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(hours=16),
                    "source_updated_at": datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(hours=17),
                    "open": close - 0.03,
                    "high": close + 0.05,
                    "low": close - 0.06,
                    "close": close,
                    "volume": 1_000_000 + idx * 1000,
                    "amount": (1_000_000 + idx * 1000) * close,
                    "is_suspended": False,
                    "is_st": False,
                    "limit_up": False,
                    "limit_down": False,
                    "listed_days": idx + 1,
                    "is_tradable": True,
                    "reason": "fixture tradable",
                }
            )
    path = config.out_dir / "input_fixture.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def _load_trading_calendar(data_root: Path) -> pl.DataFrame | None:
    calendar_path = resolve_trading_calendar_parquet(data_root)
    if calendar_path is None:
        return None
    calendar = pl.read_parquet(calendar_path)
    if "is_trading_day" not in calendar.columns or "date" not in calendar.columns or "market" not in calendar.columns:
        return None
    return (
        calendar.filter(pl.col("is_trading_day"))
        .select(pl.col("market").cast(pl.Utf8), pl.col("date"))
        .sort(["market", "date"])
    )


def _normalize_legacy_kline(
    frame: pl.DataFrame,
    symbols: tuple[str, ...],
    calendar: pl.DataFrame | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    wanted = set(symbols)
    market_trading_days: dict[str, list[date]] = {}
    if calendar is not None:
        for market_value in calendar["market"].unique().to_list():
            key = str(market_value)
            market_trading_days[key] = [
                _date_value(value)
                for value in calendar.filter(pl.col("market") == market_value)["date"].to_list()
            ]
    for row in frame.iter_rows(named=True):
        symbol = _legacy_symbol(row["code"], row["market"])
        if wanted and symbol not in wanted:
            continue
        current_date = _date_value(row["date"])
        event_time = datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            tzinfo=UTC,
        )
        market_label = "SH" if int(row["market"]) == 1 else "SZ"
        available_at = _conservative_available_at(event_time)
        trading_days = market_trading_days.get(market_label)
        if trading_days:
            idx = bisect_right(trading_days, current_date)
            if idx < len(trading_days):
                next_td = trading_days[idx]
                available_at = datetime(
                    next_td.year,
                    next_td.month,
                    next_td.day,
                    9,
                    30,
                    tzinfo=UTC,
                )
        rows.append(
            {
                "symbol": symbol,
                "market": market_label,
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": available_at,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "amount": float(row["amount"]),
            }
        )
    if not rows:
        raise ValueError(f"legacy kline input contains none of the requested symbols: {symbols}")
    return pl.DataFrame(rows)


def _legacy_symbol(code: object, market: object) -> str:
    suffix = "SH" if str(market).lower() in {"1", "sh"} else "SZ"
    return f"{str(code).zfill(6)}.{suffix}"


def _conservative_available_at(event_time: datetime) -> datetime:
    return event_time + timedelta(days=1, hours=9, minutes=30)


def _merge_optional_pit_layers(config: PipelineConfig, frame: pl.DataFrame) -> pl.DataFrame:
    if config.data_root is None:
        return _with_adjusted_prices(frame, None)
    status_path = resolve_tradability_status_parquet(config.data_root)
    if status_path is not None:
        frame = _merge_latest_by_symbol_event(frame, pl.read_parquet(status_path), _TRADABILITY_COLUMNS)
    adjustment_path = resolve_adjustment_factor_parquet(config.data_root)
    return _with_adjusted_prices(frame, adjustment_path)


_TRADABILITY_COLUMNS = ("is_st", "is_suspended", "limit_up", "limit_down", "listed_days", "is_tradable", "reason")


def _merge_latest_by_symbol_event(frame: pl.DataFrame, pit_layer: pl.DataFrame, value_columns: tuple[str, ...]) -> pl.DataFrame:
    if pit_layer.is_empty():
        return frame
    wanted = ["symbol", "event_time", "available_at", *value_columns]
    layer = pit_layer.select([column for column in wanted if column in pit_layer.columns]).rename({"available_at": "status_available_at"})
    joined = frame.join_asof(layer.sort(["symbol", "event_time"]), on="event_time", by="symbol", strategy="backward", check_sortedness=False)
    if "status_available_at" in joined.columns:
        joined = joined.with_columns(
            pl.when(pl.col("status_available_at") <= pl.col("available_at")).then(pl.col(column)).otherwise(None).alias(column)
            for column in value_columns
            if column in joined.columns
        ).drop("status_available_at")
    return joined


def _with_adjusted_prices(frame: pl.DataFrame, adjustment_path: Path | None) -> pl.DataFrame:
    if adjustment_path is None:
        return frame
    factors = pl.read_parquet(adjustment_path)
    if factors.is_empty():
        return frame
    layer = factors.select([column for column in ("symbol", "event_time", "available_at", "factor") if column in factors.columns]).rename({"available_at": "factor_available_at", "factor": "adjustment_factor"})
    joined = frame.join_asof(layer.sort(["symbol", "event_time"]), on="event_time", by="symbol", strategy="backward", check_sortedness=False)
    if "adjustment_factor" not in joined.columns:
        return frame
    return (
        joined.with_columns(pl.when(pl.col("factor_available_at") <= pl.col("available_at")).then(pl.col("adjustment_factor")).otherwise(None).fill_null(1.0).alias("adjustment_factor"))
        .with_columns((pl.col("close") * pl.col("adjustment_factor")).alias("adjusted_close"), pl.col("close").alias("raw_close"))
        .drop("factor_available_at")
    )


def _with_default_columns(frame: pl.DataFrame, defaults: dict[str, Any]) -> pl.DataFrame:
    expressions = []
    for column, value in defaults.items():
        if column not in frame.columns:
            expressions.append(pl.lit(value).alias(column))
    return frame.with_columns(expressions) if expressions else frame


def _eligible_frame(frame: pl.DataFrame, *, min_liquidity: float) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    frame = _with_default_columns(frame, {"is_st": False, "is_suspended": False, "limit_up": False, "is_tradable": True, "listed_days": 9999, "amount": 0.0})
    return frame.filter(
        (~pl.col("is_st").fill_null(False))
        & (~pl.col("is_suspended").fill_null(False))
        & (~pl.col("limit_up").fill_null(False))
        & pl.col("is_tradable").fill_null(True)
        & (pl.col("listed_days").fill_null(9999) >= 21)
        & (pl.col("amount").fill_null(0.0) >= min_liquidity)
    )


def _portfolio_targets(target_frame: pl.DataFrame, *, state_tag: str) -> tuple[PortfolioTarget, ...]:
    if target_frame.is_empty():
        return ()
    return tuple(
        PortfolioTarget(
            symbol=str(row["symbol"]),
            target_weight=float(row["target_weight"]),
            score=float(row["score"]),
            state_tag=str(row.get("state_tag") or state_tag),
            eligibility_reason=str(row["eligibility_reason"]),
            industry=str(row.get("industry") or ""),
            rank=int(row["rank"]),
        )
        for row in target_frame.to_dicts()
    )


def _portfolio_metrics(target_frame: pl.DataFrame, config: PipelineConfig) -> dict[str, float]:
    if target_frame.is_empty():
        return {"gross_exposure": 0.0, "net_exposure": 0.0, "turnover_ratio": 0.0, "industry_concentration": 0.0, "liquidity_stress": 0.0}
    gross = float(target_frame["target_weight"].sum())
    industry_concentration = 0.0
    if "industry" in target_frame.columns and target_frame["industry"].n_unique() < target_frame.height:
        industry = target_frame.group_by("industry").agg(pl.col("target_weight").sum().alias("weight"))
        industry_concentration = float(industry["weight"].max() or 0.0)
    liquidity_stress = 0.0
    if "amount" in target_frame.columns and target_frame.height:
        stress = target_frame.with_columns((pl.col("target_weight") * config.initial_capital / pl.col("amount").clip(lower_bound=1.0)).alias("stress"))
        liquidity_stress = float(stress["stress"].max() or 0.0)
    return {
        "gross_exposure": gross,
        "net_exposure": gross,
        "turnover_ratio": gross,
        "industry_concentration": industry_concentration,
        "liquidity_stress": liquidity_stress,
    }


def _write_portfolio_artifacts(out_dir: Path, target_frame: pl.DataFrame, market_state: dict[str, Any]) -> None:
    if not target_frame.is_empty():
        target_frame.write_parquet(out_dir / "portfolio_targets.parquet")
        _write_json(out_dir / "portfolio_targets.json", target_frame.to_dicts())
    else:
        pl.DataFrame(schema={"symbol": pl.Utf8, "target_weight": pl.Float64, "score": pl.Float64}).write_parquet(out_dir / "portfolio_targets.parquet")
        _write_json(out_dir / "portfolio_targets.json", [])
    _write_json(out_dir / "regime_summary.json", market_state)


def _rebalance_intent(*, as_of: datetime, targets: tuple[PortfolioTarget, ...], control_report: AShareControlReport, dataset_version: str, manifest_hash: str, rebalance_mode: str) -> RebalanceIntent:
    executable = control_report.decision == "promote"
    risk_flags = tuple((*control_report.halt_reasons, *control_report.warnings, *control_report.error_terms.get("reject_reasons", ()), *control_report.error_terms.get("observe_reasons", ())))
    return RebalanceIntent(
        as_of=as_of,
        targets=targets if executable else (),
        control_decision=control_report.decision,
        risk_flags=risk_flags,
        dataset_version=dataset_version,
        manifest_hash=manifest_hash,
        rebalance_mode=rebalance_mode,
        executable=executable,
    )


def _attribution_payload(market_state: dict[str, Any], target_frame: pl.DataFrame, ledger_results: dict[str, LedgerBacktestResult], control_report: AShareControlReport) -> dict[str, Any]:
    target_symbols = set(str(symbol) for symbol in target_frame["symbol"].to_list()) if not target_frame.is_empty() else set()
    blocked = [] if target_frame.is_empty() else target_frame.filter(pl.col("eligibility_reason") != "eligible").select(["symbol", "eligibility_reason"]).to_dicts()
    return {
        "market_state": market_state,
        "portfolio": {
            "target_count": len(target_symbols),
            "gross_exposure": control_report.observed_state.get("gross_exposure", 0.0),
            "industry_concentration": control_report.observed_state.get("industry_concentration", 0.0),
        },
        "backtest": {
            "symbols": sorted(ledger_results),
            "best_return": max((result.total_return for result in ledger_results.values()), default=0.0),
            "worst_drawdown": max((result.max_drawdown for result in ledger_results.values()), default=0.0),
            "completed_trades": sum(len(result.trades) for result in ledger_results.values()),
        },
        "blocked_candidates": blocked,
        "control_decision": control_report.decision,
        "failure_reasons": list(control_report.halt_reasons) + list(control_report.error_terms.get("reject_reasons", ())) + list(control_report.error_terms.get("observe_reasons", ())),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")


def _write_factor_artifacts(out_dir: Path, factor_frame: pl.DataFrame, *, artifact_level: str) -> None:
    factor_frame.write_parquet(out_dir / "factors.parquet")
    if artifact_level == "full":
        _write_json(out_dir / "factors.json", factor_frame.to_dicts())


def _write_signal_artifacts(out_dir: Path, signal_frame: pl.DataFrame, *, artifact_level: str) -> None:
    signal_frame.write_parquet(out_dir / "signals.parquet")
    if artifact_level == "full":
        _write_json(out_dir / "signals.json", signal_frame.to_dicts())


def _write_control_artifacts(path: Path, report: AShareControlReport) -> None:
    _write_json(path / "control_report.json", asdict(report))
    lines = [
        "# A-Share Control Report",
        "",
        f"- decision: {report.decision}",
        f"- evidence_level: {report.evidence_level}",
        f"- manifest_hash: {report.manifest_hash}",
        f"- dataset_version: {report.dataset_version}",
        f"- regime: {report.observed_state.get('regime', '')}",
        f"- gross_exposure: {report.observed_state.get('gross_exposure', 0.0)}",
        f"- visible_rows: {report.observed_state['visible_rows']}",
        f"- rejected_future_rows: {report.observed_state['rejected_future_rows']}",
        f"- coverage_ratio: {report.observed_state['coverage_ratio']}",
        f"- requested_symbol_count: {report.observed_state['requested_symbol_count']}",
        f"- visible_symbol_count: {report.observed_state['visible_symbol_count']}",
        f"- empty_or_missing_symbol_count: {len(report.observed_state['empty_or_missing_symbols'])}",
        f"- signals: {report.observed_state['signals']}",
        f"- completed_trades: {report.observed_state['completed_trades']}",
        f"- package_decision: {report.observed_state['package_decision']}",
        "",
        "## Halt Reasons",
        *(f"- {reason}" for reason in report.halt_reasons),
        "",
        "## Warnings",
        *(f"- {warning}" for warning in report.warnings),
    ]
    (path / "control_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _merge_control_into_audit(path: Path, report: AShareControlReport) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["control_decision"] = report.decision
    payload["control_report_path"] = "control_report.json"
    payload["evidence_level"] = report.evidence_level
    payload["observed_state"] = report.observed_state
    _write_json(path, payload)


def _breadth_up_ratio(frame: pl.DataFrame) -> float:
    if frame.is_empty() or not {"symbol", "event_time", "close"}.issubset(frame.columns):
        return 0.0
    pairs = frame.sort(["symbol", "event_time"]).group_by("symbol", maintain_order=True).tail(2).group_by("symbol").agg(pl.col("close").last().alias("latest_close"), pl.col("close").first().alias("previous_close"))
    if pairs.is_empty():
        return 0.0
    return float(pairs.select((pl.col("latest_close") > pl.col("previous_close")).cast(pl.Float64).mean()).item() or 0.0)


def _median_volatility_20d(frame: pl.DataFrame) -> float:
    if frame.is_empty() or not {"symbol", "event_time", "close"}.issubset(frame.columns):
        return 0.0
    vol = frame.sort(["symbol", "event_time"]).with_columns(pl.col("close").pct_change().rolling_std(20).over("symbol").alias("volatility_20d")).group_by("symbol", maintain_order=True).last()
    series = vol["volatility_20d"].drop_nulls()
    return float(series.median()) if series.len() else 0.0


def _boolean_ratio(frame: pl.DataFrame, columns: tuple[str, ...]) -> float:
    if frame.is_empty():
        return 0.0
    available = [column for column in columns if column in frame.columns]
    if not available:
        return 0.0
    expr = None
    for column in available:
        current = pl.col(column).fill_null(False)
        expr = current if expr is None else (expr | current)
    assert expr is not None
    return float(frame.select(expr.cast(pl.Float64).mean()).item() or 0.0)


def _column_ratio(frame: pl.DataFrame, column: str, *, default: bool) -> float:
    if frame.is_empty():
        return 0.0
    if column not in frame.columns:
        return 1.0 if default else 0.0
    return float(frame.select(pl.col(column).fill_null(default).cast(pl.Float64).mean()).item() or 0.0)


def _time_column(frame: pl.DataFrame) -> str:
    for column in ("event_time", "datetime", "date"):
        if column in frame.columns:
            return column
    raise ValueError("frame must include event_time, datetime, or date")


def _date_value(value: object):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


def _as_utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise ValueError(f"unsupported datetime value: {value!r}")


def _min_datetime(frame: pl.DataFrame) -> datetime:
    value = frame[_time_column(frame)].min()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise ValueError(f"unsupported start value: {value!r}")


def _max_datetime(frame: pl.DataFrame) -> datetime:
    value = frame[_time_column(frame)].max()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise ValueError(f"unsupported end value: {value!r}")
