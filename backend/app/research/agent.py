"""Controlled research-agent runner.

This module is the local boundary for RD-Agent/AgentQuant-style research loops.
It may create factor definitions, experiment plans, and research signals, but it
must stay broker-neutral and PIT-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from .manifest import DatasetVersion, ExperimentManifest, sha256_json
from .models import (
    DatasetRef,
    Experiment,
    ExperimentStatus,
    FactorDefinition,
    FactorKind,
    FactorValue,
    Frequency,
    Market,
    ResearchSignal,
    SignalSide,
)
from .pit import PITDataset, PointInTimeQuery, PointInTimeStore


@dataclass(frozen=True)
class CandidateFactorSpec:
    name: str
    kind: FactorKind
    inputs: tuple[str, ...]
    description: str


BUILTIN_FACTOR_SPECS: dict[str, CandidateFactorSpec] = {
    "momentum_20d": CandidateFactorSpec(
        name="momentum_20d",
        kind=FactorKind.PRICE_VOLUME,
        inputs=("close",),
        description="20 trading day close-to-close momentum.",
    ),
    "volatility_20d": CandidateFactorSpec(
        name="volatility_20d",
        kind=FactorKind.PRICE_VOLUME,
        inputs=("close",),
        description="20 trading day realized close return volatility.",
    ),
    "turnover_pressure_20d": CandidateFactorSpec(
        name="turnover_pressure_20d",
        kind=FactorKind.PRICE_VOLUME,
        inputs=("volume", "amount", "close"),
        description="20 trading day volume and amount pressure proxy.",
    ),
}


@dataclass(frozen=True)
class ResearchAgentRequest:
    name: str
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    as_of: datetime
    dataset_version: DatasetVersion
    dataset: PITDataset = PITDataset.KLINE_DAILY
    market: Market = Market.ASHARE
    frequency: Frequency = Frequency.DAILY
    factor_names: tuple[str, ...] = ("momentum_20d", "volatility_20d")
    hypothesis: str = ""
    initial_capital: float = 1_000_000
    entry_threshold: float = 0.02
    exit_threshold: float = -0.02

    def __post_init__(self) -> None:
        if self.market != Market.ASHARE:
            raise ValueError("ResearchAgentRunner currently supports ashare only")
        if not self.symbols:
            raise ValueError("research agent symbols are required")
        if any(not symbol.strip() for symbol in self.symbols):
            raise ValueError("research agent symbols cannot be blank")
        if not self.factor_names:
            raise ValueError("research agent factor names are required")
        if self.initial_capital <= 0:
            raise ValueError("initial capital must be positive")
        if self.dataset_version.name != self.dataset.ashare_contract_dataset().value:
            raise ValueError("research agent dataset_version must match PIT dataset")
        if self.dataset_version.market != self.market:
            raise ValueError("research agent dataset_version market mismatch")
        if self.dataset_version.frequency != self.frequency:
            raise ValueError("research agent dataset_version frequency mismatch")
        if self.dataset_version.version.lower() == "latest":
            raise ValueError("research agent dataset_version cannot be latest")


@dataclass(frozen=True)
class ResearchAgentOutput:
    factors: tuple[FactorDefinition, ...]
    experiment: Experiment
    signals: tuple[ResearchSignal, ...] = ()
    factor_values: tuple[FactorValue, ...] = ()


class ResearchAgentRunner:
    """Build a PIT-safe research plan without running execution or backtest adapters."""

    def __init__(
        self,
        factor_specs: dict[str, CandidateFactorSpec] | None = None,
    ) -> None:
        self.factor_specs = factor_specs or BUILTIN_FACTOR_SPECS

    def build_plan(self, request: ResearchAgentRequest) -> ResearchAgentOutput:
        request.dataset.ashare_contract_dataset()
        factors = tuple(
            self._build_factor(name, request.market, request.frequency)
            for name in request.factor_names
        )
        dataset = DatasetRef(
            name=request.dataset.value,
            market=request.market,
            frequency=request.frequency,
            source="duckdb.pit",
            as_of=request.as_of,
            version=request.dataset_version.version,
        )
        experiment = Experiment(
            name=request.name,
            market=request.market,
            datasets=(dataset,),
            factors=factors,
            start=request.start,
            end=request.end,
            as_of=request.as_of,
            hypothesis=request.hypothesis,
        )
        return ResearchAgentOutput(factors=factors, experiment=experiment)

    def run(
        self,
        request: ResearchAgentRequest,
        pit_store: PointInTimeStore,
    ) -> ResearchAgentOutput:
        plan = self.build_plan(request)
        factor_values: list[FactorValue] = []
        research_signals: list[ResearchSignal] = []
        symbols_scanned = 0

        for symbol in request.symbols:
            bars = pit_store.query(
                PointInTimeQuery(
                    dataset=request.dataset,
                    market=request.market,
                    symbol=symbol,
                    as_of=request.as_of,
                    start=request.start,
                    end=request.end,
                    frequency=request.frequency,
                )
            )
            if bars.is_empty():
                continue
            symbols_scanned += 1

            factor_frame = self._calculate_factors(bars, request.factor_names)
            factor_values.extend(
                self._factor_values(symbol, request.as_of, factor_frame, request.factor_names)
            )
            symbol_signals = self._generate_signals(
                symbol=symbol,
                as_of=request.as_of,
                factor_frame=factor_frame,
                entry_threshold=request.entry_threshold,
                exit_threshold=request.exit_threshold,
            )
            research_signals.extend(symbol_signals)

        experiment = Experiment(
            name=plan.experiment.name,
            market=plan.experiment.market,
            datasets=plan.experiment.datasets,
            factors=plan.experiment.factors,
            start=plan.experiment.start,
            end=plan.experiment.end,
            as_of=plan.experiment.as_of,
            id=plan.experiment.id,
            status=ExperimentStatus.COMPLETED,
            hypothesis=plan.experiment.hypothesis,
            metrics=self._aggregate_metrics(
                requested_symbols=len(request.symbols),
                symbols_scanned=symbols_scanned,
                factor_values=len(factor_values),
                signals=len(research_signals),
            ),
        )
        return ResearchAgentOutput(
            factors=plan.factors,
            experiment=experiment,
            signals=tuple(research_signals),
            factor_values=tuple(factor_values),
        )

    def promote_to_manifest(
        self,
        output: ResearchAgentOutput,
        *,
        code_commit: str,
        data_versions: tuple[DatasetVersion, ...],
        config: dict[str, Any],
        random_seed: int,
    ) -> ExperimentManifest:
        return ExperimentManifest(
            experiment_id=output.experiment.id,
            name=output.experiment.name,
            code_commit=code_commit,
            data_versions=data_versions,
            factor_versions=tuple(f"{factor.name}:v1" for factor in output.factors),
            config_hash=sha256_json(config),
            random_seed=random_seed,
        )

    def _build_factor(
        self,
        name: str,
        market: Market,
        frequency: Frequency,
    ) -> FactorDefinition:
        try:
            spec = self.factor_specs[name]
        except KeyError as exc:
            raise ValueError(f"unsupported research agent factor: {name}") from exc
        return FactorDefinition(
            name=spec.name,
            kind=spec.kind,
            market=market,
            frequency=frequency,
            inputs=spec.inputs,
            owner="research_agent",
            description=spec.description,
        )

    def _calculate_factors(
        self,
        bars: pl.DataFrame,
        factor_names: tuple[str, ...],
    ) -> pl.DataFrame:
        if "close" not in bars.columns:
            raise ValueError("research agent bars must include close")

        frame = bars.sort(self._time_column(bars))
        expressions = []
        if "momentum_20d" in factor_names:
            expressions.append(
                (pl.col("close") / pl.col("close").shift(20) - 1).alias("momentum_20d")
            )
        if "volatility_20d" in factor_names:
            expressions.append(pl.col("close").pct_change().rolling_std(20).alias("volatility_20d"))
        if "turnover_pressure_20d" in factor_names:
            if "volume" not in frame.columns or "amount" not in frame.columns:
                raise ValueError("turnover_pressure_20d requires volume and amount")
            expressions.append(
                (
                    (pl.col("amount") / pl.col("amount").rolling_mean(20))
                    + (pl.col("volume") / pl.col("volume").rolling_mean(20))
                ).alias("turnover_pressure_20d")
            )
        return frame.with_columns(expressions) if expressions else frame

    def _factor_values(
        self,
        symbol: str,
        as_of: datetime,
        factor_frame: pl.DataFrame,
        factor_names: tuple[str, ...],
    ) -> tuple[FactorValue, ...]:
        time_col = self._time_column(factor_frame)
        values: list[FactorValue] = []
        for row in factor_frame.iter_rows(named=True):
            timestamp = self._as_datetime(row[time_col], as_of)
            for factor_name in factor_names:
                value = row.get(factor_name)
                if value is None:
                    continue
                values.append(
                    FactorValue(
                        factor=factor_name,
                        symbol=symbol,
                        timestamp=timestamp,
                        value=float(value),
                        as_of=as_of,
                    )
                )
        return tuple(values)

    def _generate_signals(
        self,
        symbol: str,
        as_of: datetime,
        factor_frame: pl.DataFrame,
        entry_threshold: float,
        exit_threshold: float,
    ) -> tuple[ResearchSignal, ...]:
        time_col = self._time_column(factor_frame)
        research_signals: list[ResearchSignal] = []

        for row in factor_frame.iter_rows(named=True):
            momentum = row.get("momentum_20d")
            side = SignalSide.HOLD
            if momentum is not None:
                if float(momentum) > entry_threshold:
                    side = SignalSide.BUY
                elif float(momentum) < exit_threshold:
                    side = SignalSide.SELL
            if side != SignalSide.HOLD:
                timestamp = self._as_datetime(row[time_col], as_of)
                research_signals.append(
                    ResearchSignal(
                        symbol=symbol,
                        side=side,
                        timestamp=timestamp,
                        as_of=as_of,
                        strategy="research_agent_momentum_20d",
                        confidence=min(1.0, abs(float(momentum or 0.0)) / 0.10),
                        reason=f"momentum_20d={float(momentum):.4f}",
                    )
                )

        return tuple(research_signals)

    def _aggregate_metrics(
        self,
        *,
        requested_symbols: int,
        symbols_scanned: int,
        factor_values: int,
        signals: int,
    ) -> dict[str, float]:
        return {
            "requested_symbols": float(requested_symbols),
            "symbols_scanned": float(symbols_scanned),
            "factor_values": float(factor_values),
            "signals_generated": float(signals),
        }

    def _time_column(self, frame: pl.DataFrame) -> str:
        if "datetime" in frame.columns:
            return "datetime"
        if "date" in frame.columns:
            return "date"
        if "event_time" in frame.columns:
            return "event_time"
        raise ValueError("research agent data must include date, datetime, or event_time")

    def _as_datetime(self, value: object, as_of: datetime) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value
            return value.replace(tzinfo=as_of.tzinfo or UTC)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=as_of.tzinfo or UTC)
        raise ValueError(f"unsupported timestamp value: {value!r}")
