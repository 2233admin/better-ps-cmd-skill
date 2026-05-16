"""Core research contracts for factors, experiments, and signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class Market(str, Enum):
    ASHARE = "ashare"
    CRYPTO = "crypto"


class Frequency(str, Enum):
    TICK = "tick"
    MINUTE = "minute"
    DAILY = "daily"


class FactorKind(str, Enum):
    PRICE_VOLUME = "price_volume"
    FUNDAMENTAL = "fundamental"
    MACRO = "macro"
    ALTERNATIVE = "alternative"
    HMC_STATE = "hmc_state"


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SignalSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DatasetRef:
    name: str
    market: Market
    frequency: Frequency
    source: str
    as_of: datetime | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dataset name is required")
        if not self.source.strip():
            raise ValueError("dataset source is required")


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    kind: FactorKind
    market: Market
    frequency: Frequency
    inputs: tuple[str, ...]
    owner: str = "research"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("factor name is required")
        if not self.inputs:
            raise ValueError("factor inputs are required")


@dataclass(frozen=True)
class FactorValue:
    factor: str
    symbol: str
    timestamp: datetime
    value: float
    as_of: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.as_of < self.timestamp:
            raise ValueError("as_of cannot be earlier than factor timestamp")


@dataclass(frozen=True)
class ResearchSignal:
    symbol: str
    side: SignalSide
    timestamp: datetime
    as_of: datetime
    strategy: str
    confidence: float
    reason: str
    horizon: str = "1d"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.as_of < self.timestamp:
            raise ValueError("as_of cannot be earlier than signal timestamp")


@dataclass(frozen=True)
class Experiment:
    name: str
    market: Market
    datasets: tuple[DatasetRef, ...]
    factors: tuple[FactorDefinition, ...]
    start: datetime
    end: datetime
    as_of: datetime
    id: str = field(default_factory=lambda: f"exp-{uuid4().hex}")
    status: ExperimentStatus = ExperimentStatus.DRAFT
    hypothesis: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("experiment name is required")
        if self.end < self.start:
            raise ValueError("experiment end cannot be earlier than start")
        if self.as_of < self.end:
            raise ValueError("experiment as_of cannot be earlier than end")
        if not self.datasets:
            raise ValueError("experiment datasets are required")


@dataclass(frozen=True)
class HMCStateEstimate:
    symbol: str
    timestamp: datetime
    as_of: datetime
    regime: str
    risk_score: float
    posterior: dict[str, float]
    model_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.risk_score <= 1.0:
            raise ValueError("risk_score must be between 0 and 1")
        if self.as_of < self.timestamp:
            raise ValueError("as_of cannot be earlier than state timestamp")
