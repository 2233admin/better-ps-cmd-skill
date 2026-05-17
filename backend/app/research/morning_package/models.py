"""Data contracts for the A-share morning package."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any


class ManualAction(str, Enum):
    PENDING = "pending"
    CONFIRM = "confirm"
    REJECT = "reject"
    OBSERVE = "observe"


class PackageDecision(str, Enum):
    TRADE = "trade"
    OBSERVE = "observe"
    DO_NOT_TRADE = "do_not_trade"


class EvidenceSourceKind(str, Enum):
    DATASET = "dataset"
    BACKTEST = "backtest"
    RISK = "risk"
    MANIFEST = "manifest"
    MANUAL = "manual"
    LLM = "llm"


def now_utc() -> datetime:
    return datetime.now(UTC)


def enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): enum_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [enum_value(item) for item in value]
    return value


@dataclass(frozen=True)
class EvidenceSource:
    kind: EvidenceSourceKind | str
    source_id: str
    description: str
    uri: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            object.__setattr__(self, "kind", EvidenceSourceKind(self.kind))
        if not self.source_id.strip():
            raise ValueError("evidence source_id is required")
        if not self.description.strip():
            raise ValueError("evidence description is required")


@dataclass(frozen=True)
class EvidenceChain:
    sources: tuple[EvidenceSource, ...]
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("evidence chain requires at least one source")

    def authoritative_sources(self) -> tuple[EvidenceSource, ...]:
        return tuple(source for source in self.sources if source.kind != EvidenceSourceKind.LLM)


@dataclass(frozen=True)
class BacktestEvidence:
    window: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    max_drawdown: float | None

    def is_complete(self) -> bool:
        return bool(self.window.strip()) and self.max_drawdown is not None


@dataclass(frozen=True)
class RiskBlock:
    market_risks: tuple[str, ...] = ()
    anomalies: tuple[str, ...] = ()
    data_integrity: tuple[str, ...] = ()
    portfolio_limits: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateSignal:
    symbol: str
    side: str
    thesis: str
    evidence_chain: EvidenceChain | None = None
    backtest: BacktestEvidence | None = None
    position_cap: float | None = None
    failure_condition: str = ""
    auxiliary_notes: str = ""


@dataclass(frozen=True)
class TradingIntent:
    symbol: str
    side: str
    thesis: str
    decision: PackageDecision
    evidence_chain: EvidenceChain | None
    backtest: BacktestEvidence | None
    position_cap: float | None
    failure_condition: str
    manual_action: ManualAction = ManualAction.PENDING
    auxiliary_notes: str = ""
    downgrade_reasons: tuple[str, ...] = ()

    def to_table_row(self) -> dict[str, Any]:
        source_ids: list[str] = []
        if self.evidence_chain:
            source_ids = [
                source.source_id for source in self.evidence_chain.authoritative_sources()
            ]
        return {
            "symbol": self.symbol,
            "side": self.side,
            "decision": self.decision.value,
            "manual_action": self.manual_action.value,
            "thesis": self.thesis,
            "evidence_sources": "|".join(source_ids),
            "backtest_window": self.backtest.window if self.backtest else "",
            "max_drawdown": self.backtest.max_drawdown if self.backtest else None,
            "position_cap": self.position_cap,
            "failure_condition": self.failure_condition,
            "downgrade_reasons": "|".join(self.downgrade_reasons),
            "auxiliary_notes": self.auxiliary_notes,
        }


@dataclass(frozen=True)
class MorningPackage:
    package_date: date
    generated_at: datetime
    decision: PackageDecision
    dataset_version: str | None
    code_commit: str | None
    manifest_hash: str | None
    config_hash: str | None
    risk: RiskBlock
    intents: tuple[TradingIntent, ...]
    do_not_trade_reasons: tuple[str, ...]
    audit: dict[str, Any] = field(default_factory=dict)

    def to_audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["package_date"] = self.package_date.isoformat()
        payload["generated_at"] = self.generated_at.isoformat()
        return enum_value(payload)
