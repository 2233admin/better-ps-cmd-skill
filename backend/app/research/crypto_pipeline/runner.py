"""End-to-end crypto PIT research pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import polars as pl

from app.research.backtest import (
    CryptoBacktestConfig,
    CryptoLedgerBacktester,
    LedgerBacktestResult,
    write_backtest_artifacts,
    write_backtest_tables,
)
from app.research.crypto_data_contract import CryptoPITDataset, validate_crypto_pit_columns
from app.research.crypto_pipeline.control import (
    ControlConfig,
    ControlReport,
    PITValidationSummary,
    evaluate_control,
    summarize_pit,
)
from app.research.crypto_pipeline.factors import (
    CryptoFactorValue,
    CryptoSignal,
    calculate_crypto_factors,
    factor_values,
    generate_crypto_signals,
    ledger_signal_frame,
)
from app.research.crypto_pipeline.reconciliation import reconcile_crypto_paper
from app.research.manifest import (
    ArtifactKind,
    DataArtifact,
    DatasetSnapshot,
    DatasetVersion,
    ExperimentManifest,
    sha256_json,
)
from app.research.models import Frequency, Market
from app.research.snapshot import data_hash, schema_hash


@dataclass(frozen=True)
class CryptoPipelineConfig:
    session_date: date
    inst_ids: tuple[str, ...]
    out_dir: Path
    pit_parquet: Path | None = None
    code_commit: str = "manual"
    market_type: Literal["spot", "swap"] = "spot"
    allow_short: bool = False
    initial_capital: float = 100_000.0
    position_cap: float = 0.10
    leverage: float = 1.0
    random_seed: int = 42

    def __post_init__(self) -> None:
        if not self.inst_ids:
            raise ValueError("crypto pipeline inst_ids are required")
        if self.market_type == "spot" and self.allow_short:
            raise ValueError("spot crypto pipeline cannot allow_short")
        if self.market_type == "swap" and self.leverage <= 0:
            raise ValueError("swap leverage must be positive")


@dataclass(frozen=True)
class CryptoPipelineResult:
    manifest: ExperimentManifest
    ledger_backtests: dict[str, LedgerBacktestResult]
    session_package_dir: Path
    control_report: ControlReport


def run_crypto_pipeline(config: CryptoPipelineConfig) -> CryptoPipelineResult:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    source_is_fixture = config.pit_parquet is None
    source_path = config.pit_parquet or _write_fixture_parquet(config)
    pit_path, pit_frame, has_funding_evidence = _materialize_kline_pit(config, source_path)
    as_of = datetime.combine(config.session_date, datetime.max.time(), tzinfo=UTC)
    pit_frame, pit_summary = _validate_pit(
        pit_frame,
        as_of=as_of,
        has_funding_evidence=has_funding_evidence,
    )
    pit_frame.write_parquet(pit_path)

    dataset_version = _write_snapshot(config, pit_path, pit_frame, as_of)
    factors, signals = _run_factors_and_signals(config, pit_frame, as_of)
    ledger_results = _run_backtests(config, pit_frame, signals)
    paper_reconciliation = reconcile_crypto_paper(
        signals,
        pit_frame,
        position_notional=config.initial_capital * config.position_cap,
        max_notional=config.initial_capital * config.position_cap,
    )

    manifest = ExperimentManifest(
        experiment_id=f"crypto-exp-{uuid4().hex}",
        name="crypto_pit_pipeline_v1",
        code_commit=config.code_commit,
        data_versions=(dataset_version,),
        factor_versions=(
            "funding_annualized:v1",
            "mark_index_basis:v1",
            "perp_spot_basis:v1",
            "open_interest_change:v1",
            "liquidity_score:v1",
            "volatility_regime:v1",
        ),
        config_hash=sha256_json(
            {
                "inst_ids": config.inst_ids,
                "market_type": config.market_type,
                "allow_short": config.allow_short,
                "initial_capital": config.initial_capital,
                "position_cap": config.position_cap,
                "leverage": config.leverage,
            }
        ),
        random_seed=config.random_seed,
    )
    manifest_hash = manifest.manifest_hash()
    _write_json(config.out_dir / "manifest.json", manifest.to_payload() | {"manifest_hash": manifest_hash})
    _write_json(config.out_dir / "factors.json", [asdict(item) for item in factors])
    _write_json(config.out_dir / "factor_snapshot.json", [asdict(item) for item in factors])
    _write_json(config.out_dir / "signals.json", [asdict(item) for item in signals])
    _write_json(config.out_dir / "trade_intents.json", [asdict(item) for item in signals])
    _write_json(config.out_dir / "paper_reconciliation.json", paper_reconciliation.to_payload())
    for inst_id, result in ledger_results.items():
        write_backtest_artifacts(result, config.out_dir / "backtest" / inst_id)
    if ledger_results:
        first_inst = sorted(ledger_results)[0]
        write_backtest_artifacts(ledger_results[first_inst], config.out_dir / "backtest")
        write_backtest_tables(ledger_results, config.out_dir / "portfolio")
    control_report = evaluate_control(
        config=ControlConfig(
            market_type=config.market_type,
            allow_short=config.allow_short,
            leverage=config.leverage,
            source_is_fixture=source_is_fixture,
        ),
        pit_summary=pit_summary,
        factors=factors,
        signals=signals,
        ledger_results=ledger_results,
        manifest_hash=manifest_hash,
        dataset_version=dataset_version.version,
    )
    session_dir = _write_session_package(
        config,
        manifest_hash,
        dataset_version,
        ledger_results,
        signals,
        paper_reconciliation.to_payload(),
        control_report,
    )
    return CryptoPipelineResult(
        manifest=manifest,
        ledger_backtests=ledger_results,
        session_package_dir=session_dir,
        control_report=control_report,
    )


def _materialize_kline_pit(config: CryptoPipelineConfig, source_path: Path) -> tuple[Path, pl.DataFrame, bool]:
    frame = pl.read_parquet(source_path)
    columns = set(frame.columns)
    has_funding_evidence = {"funding_rate", "funding_time"}.issubset(columns)
    if "symbol" in columns and "inst_id" not in columns:
        frame = frame.rename({"symbol": "inst_id"})
    if "amount" in frame.columns and "quote_volume" not in frame.columns:
        frame = frame.rename({"amount": "quote_volume"})
    if "venue" not in frame.columns:
        frame = frame.with_columns(pl.lit("okx").alias("venue"))
    if "market_type" not in frame.columns:
        frame = frame.with_columns(pl.lit(config.market_type).alias("market_type"))
    if "funding_rate" not in frame.columns:
        frame = frame.with_columns(pl.lit(0.0).alias("funding_rate"))
    if "funding_time" not in frame.columns:
        frame = frame.with_columns(pl.col("event_time").alias("funding_time"))
    if "mark_price" not in frame.columns:
        frame = frame.with_columns(pl.col("close").alias("mark_price"))
    if "index_price" not in frame.columns:
        frame = frame.with_columns(pl.col("close").alias("index_price"))
    if "open_interest" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest"))
    if "open_interest_ccy" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest_ccy"))

    kline_check = validate_crypto_pit_columns(CryptoPITDataset.KLINE, frame.columns)
    if not kline_check.passed:
        raise ValueError(f"crypto.kline_pit is missing PIT columns: {', '.join(kline_check.missing_columns)}")
    funding_check = validate_crypto_pit_columns(CryptoPITDataset.FUNDING_RATE, frame.columns)
    if not funding_check.passed:
        raise ValueError(
            f"crypto.funding_rate_pit is missing PIT columns: {', '.join(funding_check.missing_columns)}"
        )
    mark_check = validate_crypto_pit_columns(CryptoPITDataset.MARK_PRICE, frame.columns)
    if not mark_check.passed:
        raise ValueError(f"crypto.mark_price_pit is missing PIT columns: {', '.join(mark_check.missing_columns)}")
    oi_check = validate_crypto_pit_columns(CryptoPITDataset.OPEN_INTEREST, frame.columns)
    if not oi_check.passed:
        raise ValueError(
            f"crypto.open_interest_pit is missing PIT columns: {', '.join(oi_check.missing_columns)}"
        )
    path = source_path if source_path.name.endswith("_pit.parquet") else config.out_dir / "crypto_kline_pit.parquet"
    return path, frame, has_funding_evidence


def _validate_pit(
    frame: pl.DataFrame,
    *,
    as_of: datetime,
    has_funding_evidence: bool,
) -> tuple[pl.DataFrame, PITValidationSummary]:
    visible = frame.filter(pl.col("available_at") <= as_of)
    pit_summary = summarize_pit(
        frame,
        visible,
        has_funding_evidence=has_funding_evidence,
    )
    if visible.is_empty():
        raise ValueError("crypto PIT input has no rows visible at as_of")
    return visible.sort(["inst_id", "event_time"]), pit_summary


def _write_snapshot(
    config: CryptoPipelineConfig,
    pit_path: Path,
    frame: pl.DataFrame,
    as_of: datetime,
) -> DatasetVersion:
    schema_digest = schema_hash(frame)
    data_digest = data_hash(frame)
    artifact = DataArtifact(
        kind=ArtifactKind.PIT_TABLE,
        uri=f"parquet://{pit_path.as_posix()}",
        content_hash=data_digest,
        metadata={
            "schema_hash": schema_digest,
            "row_count": frame.height,
            "dataset": CryptoPITDataset.KLINE.value,
        },
    )
    snapshot = DatasetSnapshot(
        name=CryptoPITDataset.KLINE.value,
        market=Market.CRYPTO,
        frequency=Frequency.MINUTE,
        tier="pit",
        artifact=artifact,
        row_count=frame.height,
        source="crypto_pipeline.parquet",
    )
    version = DatasetVersion(
        name=CryptoPITDataset.KLINE.value,
        market=Market.CRYPTO,
        frequency=Frequency.MINUTE,
        snapshot_id=snapshot.snapshot_id,
        schema_hash=schema_digest,
        data_hash=data_digest,
        as_of=as_of,
        version=config.session_date.isoformat(),
    )
    _write_json(config.out_dir / f"{snapshot.snapshot_id}.snapshot.json", asdict(snapshot))
    _write_json(config.out_dir / f"{version.version}.version.json", asdict(version))
    return version


def _run_factors_and_signals(
    config: CryptoPipelineConfig,
    pit_frame: pl.DataFrame,
    as_of: datetime,
) -> tuple[tuple[CryptoFactorValue, ...], tuple[CryptoSignal, ...]]:
    all_factors: list[CryptoFactorValue] = []
    all_signals: list[CryptoSignal] = []
    for inst_id in config.inst_ids:
        bars = pit_frame.filter(pl.col("inst_id") == inst_id)
        if bars.is_empty():
            continue
        factor_frame = calculate_crypto_factors(bars)
        all_factors.extend(factor_values(factor_frame, inst_id=inst_id, as_of=as_of))
        all_signals.extend(
            generate_crypto_signals(
                factor_frame,
                inst_id=inst_id,
                as_of=as_of,
                market_type=config.market_type,
                allow_short=config.allow_short,
            )
        )
    return tuple(all_factors), tuple(all_signals)


def _run_backtests(
    config: CryptoPipelineConfig,
    pit_frame: pl.DataFrame,
    signals: tuple[CryptoSignal, ...],
) -> dict[str, LedgerBacktestResult]:
    backtester = CryptoLedgerBacktester(
        CryptoBacktestConfig(
            initial_capital=config.initial_capital,
            position_cap=config.position_cap,
            market_type=config.market_type,
            leverage=config.leverage,
            allow_short=config.allow_short,
        )
    )
    results: dict[str, LedgerBacktestResult] = {}
    for inst_id in config.inst_ids:
        bars = pit_frame.filter(pl.col("inst_id") == inst_id)
        if bars.is_empty():
            continue
        inst_signals = tuple(signal for signal in signals if signal.inst_id == inst_id)
        results[inst_id] = backtester.run(
            bars,
            ledger_signal_frame(bars, inst_signals),
            symbol=inst_id,
        )
    return results


def _write_session_package(
    config: CryptoPipelineConfig,
    manifest_hash: str,
    dataset_version: DatasetVersion,
    ledger_results: dict[str, LedgerBacktestResult],
    signals: tuple[CryptoSignal, ...],
    paper_reconciliation: dict,
    control_report: ControlReport,
) -> Path:
    out_dir = config.out_dir / "session_package"
    out_dir.mkdir(parents=True, exist_ok=True)
    assumptions = {
        "market_type": config.market_type,
        "allow_short": config.allow_short,
        "leverage": config.leverage,
        "funding": "funding_rate column applied per bar for swap backtests; zero when absent",
        "execution": "research artifact only; no live trading",
    }
    audit = {
        "manifest_hash": manifest_hash,
        "dataset_version": dataset_version.version,
        "control_decision": control_report.decision,
        "control_report_path": "control_report.json",
        "evidence_level": control_report.evidence_level,
        "assumptions": assumptions,
        "signals": len(signals),
        "paper_reconciliation": paper_reconciliation,
        "backtests": {key: value.metrics() for key, value in ledger_results.items()},
    }
    _write_json(out_dir / "audit.json", audit)
    _write_json(out_dir / "signals.json", [asdict(item) for item in signals])
    _write_json(out_dir / "trade_intents.json", [asdict(item) for item in signals])
    _write_json(out_dir / "paper_reconciliation.json", paper_reconciliation)
    _write_json(out_dir / "control_report.json", asdict(control_report))
    _write_control_markdown(out_dir / "control_report.md", control_report)
    lines = [
        "# Crypto Session Package",
        "",
        f"- manifest_hash: {manifest_hash}",
        f"- dataset_version: {dataset_version.version}",
        f"- control_decision: {control_report.decision}",
        f"- evidence_level: {control_report.evidence_level}",
        f"- market_type: {config.market_type}",
        f"- allow_short: {config.allow_short}",
        f"- leverage: {config.leverage}",
        f"- funding: {assumptions['funding']}",
        "",
        "This package is research evidence only.",
    ]
    (out_dir / "session_package.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_dir


def _write_control_markdown(path: Path, report: ControlReport) -> None:
    lines = [
        "# Crypto Control Report",
        "",
        f"- decision: {report.decision}",
        f"- evidence_level: {report.evidence_level}",
        f"- manifest_hash: {report.manifest_hash}",
        f"- dataset_version: {report.dataset_version}",
        f"- visible_rows: {report.observed_state['visible_rows']}",
        f"- rejected_future_rows: {report.observed_state['rejected_future_rows']}",
        f"- signals: {report.observed_state['signals']}",
        f"- completed_trades: {report.observed_state['completed_trades']}",
        f"- max_drawdown: {report.error_terms['max_drawdown']}",
        "",
        "## Halt Reasons",
        *(f"- {reason}" for reason in report.halt_reasons),
        "",
        "## Warnings",
        *(f"- {warning}" for warning in report.warnings),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_fixture_parquet(config: CryptoPipelineConfig) -> Path:
    rows: list[dict[str, Any]] = []
    for inst_id in config.inst_ids:
        for idx in range(60):
            current = datetime.combine(config.session_date, datetime.min.time(), tzinfo=UTC) - timedelta(
                hours=59 - idx
            )
            direction = -1 if config.market_type == "swap" and config.allow_short else 1
            close = 100.0 + direction * idx * 0.4
            rows.append(
                {
                    "inst_id": inst_id,
                    "venue": "okx",
                    "market_type": config.market_type,
                    "event_time": current,
                    "available_at": current + timedelta(seconds=1),
                    "source_updated_at": current + timedelta(seconds=1),
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000.0 + idx,
                    "quote_volume": (1000.0 + idx) * close,
                    "funding_rate": 0.0001 if config.market_type == "swap" else 0.0,
                    "funding_time": current,
                    "mark_price": close * (1.0002 if config.market_type == "swap" else 1.0),
                    "index_price": close,
                    "spot_close": close * 0.9998,
                    "open_interest": 10_000.0 + idx if config.market_type == "swap" else None,
                    "open_interest_ccy": 10_000.0 + idx if config.market_type == "swap" else None,
                }
            )
    path = config.out_dir / "crypto_input_fixture.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
