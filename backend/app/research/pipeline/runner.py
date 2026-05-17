"""Build the minimum credible A-share research chain."""

from __future__ import annotations

import json
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
)
from app.research.manifest import ExperimentManifest, sha256_json
from app.research.models import Experiment, ExperimentStatus, Frequency, Market, ResearchSignal, SignalSide
from app.research.morning_package import candidates_from_research_output
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
from app.research.pipeline.data_lake import resolve_kline_daily_parquet
from app.research.snapshot import DatasetSnapshotGenerator


@dataclass(frozen=True)
class PipelineConfig:
    package_date: date
    symbols: tuple[str, ...]
    out_dir: Path
    pit_parquet: Path | None = None
    data_root: Path | None = None
    code_commit: str = "manual"
    initial_capital: float = 1_000_000.0
    position_cap: float = 0.05
    failure_condition: str = "Momentum falls below zero or PIT data quality changes."
    random_seed: int = 42
    artifact_level: Literal["summary", "full"] = "summary"


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


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    source_is_fixture = config.pit_parquet is None and config.data_root is None
    pit_path = _resolve_input_parquet(config)
    pit_path, pit_frame = _materialize_pit_input(config, pit_path)
    as_of = datetime.combine(config.package_date, datetime.min.time(), tzinfo=UTC)
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

    runner = ResearchAgentRunner()
    request = ResearchAgentRequest(
        name="ashare_momentum_20d_pipeline",
        symbols=config.symbols,
        start=start,
        end=end,
        as_of=as_of,
        dataset_version=snapshot_result.version,
        dataset=PITDataset.KLINE_DAILY,
        factor_names=("momentum_20d", "volatility_20d"),
        entry_threshold=0.01,
    )
    plan_output = runner.build_plan(request)
    factor_frame = _calculate_factor_frame(pit_frame, request.factor_names, as_of=as_of)
    signal_frame = _calculate_signal_frame(
        factor_frame,
        as_of=as_of,
        entry_threshold=request.entry_threshold,
        exit_threshold=request.exit_threshold,
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
            "strategy": "momentum_20d",
        },
        random_seed=config.random_seed,
    )
    manifest_hash = manifest.manifest_hash()
    _write_json(config.out_dir / "manifest.json", manifest.to_payload() | {"manifest_hash": manifest_hash})
    _write_factor_artifacts(config.out_dir, factor_frame, artifact_level=config.artifact_level)
    _write_signal_artifacts(config.out_dir, signal_frame, artifact_level=config.artifact_level)

    ledger_results = _run_ledger_backtests(config, pit_frame, signal_frame)
    for symbol, result in ledger_results.items():
        write_backtest_artifacts(result, config.out_dir / "backtest" / symbol)
    if ledger_results:
        first_symbol = sorted(ledger_results)[0]
        write_backtest_artifacts(ledger_results[first_symbol], config.out_dir / "backtest")
    _write_scan_results(
        config.out_dir,
        config.symbols,
        pit_frame,
        factor_frame,
        signal_frame,
        ledger_results,
    )

    candidates = candidates_from_research_output(
        output,
        dataset_version=snapshot_result.version,
        manifest_hash=manifest_hash,
        position_caps={symbol: config.position_cap for symbol in config.symbols},
        failure_conditions={symbol: config.failure_condition for symbol in config.symbols},
        ledger_backtests=ledger_results,
        allowed_sides=("buy",),
    )
    package = build_morning_package(
        package_date=config.package_date,
        candidates=candidates,
        manifest=manifest,
        manifest_hash=manifest_hash,
        dataset_version=snapshot_result.version,
        code_commit=config.code_commit,
    )
    morning_dir = config.out_dir / "morning_package"
    write_morning_artifacts(package, morning_dir)
    control_report = evaluate_ashare_control(
        config=AShareControlConfig(
            position_cap=config.position_cap,
            source_is_fixture=source_is_fixture,
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
    )
    _write_control_artifacts(morning_dir, control_report)
    _merge_control_into_audit(morning_dir / "audit.json", control_report)
    return PipelineResult(
        manifest=manifest,
        ledger_backtests=ledger_results,
        morning_package_dir=morning_dir,
        control_report=control_report,
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
        return source_path, frame
    if {"code", "market", "date", "open", "high", "low", "close", "volume", "amount"}.issubset(
        set(frame.columns)
    ):
        normalized = _normalize_legacy_kline(frame, config.symbols)
        path = config.out_dir / "normalized_kline_daily_pit.parquet"
        normalized.write_parquet(path)
        return path, normalized
    return source_path, frame


def _validate_pit(frame: pl.DataFrame, *, as_of: datetime) -> tuple[pl.DataFrame, ASharePITValidationSummary]:
    if "available_at" not in frame.columns:
        raise ValueError("A-share PIT input is missing available_at")
    visible = frame.filter(pl.col("available_at") <= as_of)
    summary = summarize_ashare_pit(frame, visible)
    if visible.is_empty():
        raise ValueError("A-share PIT input has no rows visible at as_of")
    return visible.sort(["symbol", "event_time"] if "symbol" in visible.columns else [_time_column(visible)]), summary


def _run_ledger_backtests(
    config: PipelineConfig,
    pit_frame: pl.DataFrame,
    signal_frame: pl.DataFrame,
) -> dict[str, LedgerBacktestResult]:
    results: dict[str, LedgerBacktestResult] = {}
    buy_signal_dates = _buy_signal_dates_by_symbol(signal_frame)
    backtester = AShareLedgerBacktester(
        AShareBacktestConfig(
            initial_capital=config.initial_capital,
            position_cap=config.position_cap,
        )
    )
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


def _calculate_factor_frame(
    pit_frame: pl.DataFrame,
    factor_names: tuple[str, ...],
    *,
    as_of: datetime,
) -> pl.DataFrame:
    if "symbol" not in pit_frame.columns:
        raise ValueError("A-share factor pipeline requires symbol")
    if "close" not in pit_frame.columns:
        raise ValueError("A-share factor pipeline requires close")
    time_col = _time_column(pit_frame)
    frame = pit_frame.sort(["symbol", time_col])
    expressions = []
    if "momentum_20d" in factor_names:
        expressions.append(
            (pl.col("close") / pl.col("close").shift(20) - 1).over("symbol").alias("momentum_20d")
        )
    if "volatility_20d" in factor_names:
        expressions.append(pl.col("close").pct_change().rolling_std(20).over("symbol").alias("volatility_20d"))
    if "turnover_pressure_20d" in factor_names:
        if "volume" not in frame.columns or "amount" not in frame.columns:
            raise ValueError("turnover_pressure_20d requires volume and amount")
        expressions.append(
            (
                (pl.col("amount") / pl.col("amount").rolling_mean(20))
                + (pl.col("volume") / pl.col("volume").rolling_mean(20))
            )
            .over("symbol")
            .alias("turnover_pressure_20d")
        )
    if not expressions:
        return pl.DataFrame(
            schema={
                "factor": pl.Utf8,
                "symbol": pl.Utf8,
                "timestamp": pl.Datetime(time_zone="UTC"),
                "value": pl.Float64,
                "as_of": pl.Datetime(time_zone="UTC"),
                "metadata": pl.Utf8,
            }
        )
    wide = frame.with_columns(expressions)
    return (
        wide.select("symbol", pl.col(time_col).alias("timestamp"), *factor_names)
        .unpivot(index=["symbol", "timestamp"], on=list(factor_names), variable_name="factor", value_name="value")
        .drop_nulls("value")
        .with_columns(
            pl.lit(as_of).alias("as_of"),
            pl.lit("{}").alias("metadata"),
        )
        .select("factor", "symbol", "timestamp", "value", "as_of", "metadata")
    )


def _calculate_signal_frame(
    factor_frame: pl.DataFrame,
    *,
    as_of: datetime,
    entry_threshold: float,
    exit_threshold: float,
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
    }
    if factor_frame.is_empty():
        return pl.DataFrame(schema=schema)
    momentum = factor_frame.filter(pl.col("factor") == "momentum_20d").select(
        "symbol",
        "timestamp",
        pl.col("value").alias("momentum_20d"),
    )
    if momentum.is_empty():
        return pl.DataFrame(schema=schema)
    confidence = pl.col("momentum_20d").abs() / 0.10
    return (
        momentum.filter((pl.col("momentum_20d") > entry_threshold) | (pl.col("momentum_20d") < exit_threshold))
        .with_columns(
            pl.when(pl.col("momentum_20d") > entry_threshold).then(pl.lit("buy")).otherwise(pl.lit("sell")).alias("side"),
            pl.lit(as_of).alias("as_of"),
            pl.lit("research_agent_momentum_20d").alias("strategy"),
            pl.when(confidence > 1.0).then(1.0).otherwise(confidence).alias("confidence"),
            (pl.lit("momentum_20d=") + pl.col("momentum_20d").round(4).cast(pl.Utf8)).alias("reason"),
            pl.lit("1d").alias("horizon"),
        )
        .select("symbol", "side", "timestamp", "as_of", "strategy", "confidence", "reason", "horizon")
        .sort(["timestamp", "symbol"])
    )


def _research_output_from_frames(
    plan_output: ResearchAgentOutput,
    request: ResearchAgentRequest,
    factor_frame: pl.DataFrame,
    signal_frame: pl.DataFrame,
) -> ResearchAgentOutput:
    experiment = Experiment(
        name=plan_output.experiment.name,
        market=plan_output.experiment.market,
        datasets=plan_output.experiment.datasets,
        factors=plan_output.experiment.factors,
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
    return ResearchAgentOutput(
        factors=plan_output.factors,
        experiment=experiment,
        signals=_candidate_signals(signal_frame, as_of=request.as_of),
        factor_values=(),
    )


def _candidate_signals(signal_frame: pl.DataFrame, *, as_of: datetime, limit: int = 20) -> tuple[ResearchSignal, ...]:
    if signal_frame.is_empty():
        return ()
    rows = (
        signal_frame.filter(pl.col("side") == "buy")
        .sort(["timestamp", "symbol"])
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
        )
        for row in rows
    )


def _buy_signal_dates_by_symbol(signal_frame: pl.DataFrame) -> dict[str, set[date]]:
    if signal_frame.is_empty():
        return {}
    rows = (
        signal_frame.filter(pl.col("side") == "buy")
        .group_by("symbol")
        .agg(pl.col("timestamp"))
        .to_dicts()
    )
    return {str(row["symbol"]): {_date_value(value) for value in row["timestamp"]} for row in rows}


def _write_scan_results(
    out_dir: Path,
    symbols: tuple[str, ...],
    pit_frame: pl.DataFrame,
    factor_frame: pl.DataFrame,
    signal_frame: pl.DataFrame,
    ledger_results: dict[str, LedgerBacktestResult],
) -> None:
    rows = _scan_result_rows(symbols, pit_frame, factor_frame, signal_frame, ledger_results)
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
) -> list[dict[str, Any]]:
    time_col = _time_column(pit_frame)
    latest_factors = _latest_factors_by_symbol(factor_frame)
    signal_counts, buy_counts, sell_counts, latest_signal_by_symbol = _signal_summaries(signal_frame)

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        bars = pit_frame.filter(pl.col("symbol") == symbol) if "symbol" in pit_frame.columns else pit_frame
        latest_bar = bars.sort(time_col).tail(1).to_dicts()[0] if not bars.is_empty() else {}
        result = ledger_results.get(symbol)
        metrics = result.metrics() if result is not None else {}
        latest_signal = latest_signal_by_symbol.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "visible_rows": bars.height,
                "latest_event_time": latest_bar.get(time_col),
                "latest_close": latest_bar.get("close"),
                "momentum_20d": latest_factors.get((symbol, "momentum_20d")),
                "volatility_20d": latest_factors.get((symbol, "volatility_20d")),
                "signals": signal_counts.get(symbol, 0),
                "buy_signals": buy_counts.get(symbol, 0),
                "sell_signals": sell_counts.get(symbol, 0),
                "last_signal_side": latest_signal.get("side", ""),
                "last_signal_at": latest_signal.get("timestamp"),
                "backtest_status": "completed" if result is not None else "missing",
                "completed_trades": len(result.trades) if result is not None else 0,
                "rejected_orders": sum(1 for order in result.orders if order.status == "rejected")
                if result is not None
                else 0,
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
    rows = (
        factor_frame.sort("timestamp")
        .group_by(["symbol", "factor"], maintain_order=True)
        .last()
        .select("symbol", "factor", "value")
        .to_dicts()
    )
    return {(str(row["symbol"]), str(row["factor"])): float(row["value"]) for row in rows}


def _signal_summaries(
    signal_frame: pl.DataFrame,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
    if signal_frame.is_empty():
        return {}, {}, {}, {}
    signal_counts = {
        str(row["symbol"]): int(row["len"])
        for row in signal_frame.group_by("symbol").len().to_dicts()
    }
    side_counts = signal_frame.group_by(["symbol", "side"]).len().to_dicts()
    buy_counts = {
        str(row["symbol"]): int(row["len"])
        for row in side_counts
        if str(row["side"]) == "buy"
    }
    sell_counts = {
        str(row["symbol"]): int(row["len"])
        for row in side_counts
        if str(row["side"]) == "sell"
    }
    latest = {
        str(row["symbol"]): row
        for row in signal_frame.sort("timestamp").group_by("symbol", maintain_order=True).last().to_dicts()
    }
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
                    "available_at": datetime(current.year, current.month, current.day, tzinfo=UTC)
                    + timedelta(hours=16),
                    "source_updated_at": datetime(current.year, current.month, current.day, tzinfo=UTC)
                    + timedelta(hours=17),
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
                }
            )
    path = config.out_dir / "input_fixture.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def _normalize_legacy_kline(frame: pl.DataFrame, symbols: tuple[str, ...]) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    wanted = set(symbols)
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
        available_at = event_time + timedelta(days=1, hours=9, minutes=30)
        rows.append(
            {
                "symbol": symbol,
                "market": "SH" if int(row["market"]) == 1 else "SZ",
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
    suffix = "SH" if int(market) == 1 else "SZ"
    return f"{str(code).zfill(6)}.{suffix}"


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
    _write_json(path, payload)


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
