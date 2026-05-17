"""Converters from active research outputs into morning-package candidates."""

from __future__ import annotations

from app.research.agent import ResearchAgentOutput
from app.research.backtest import LedgerBacktestResult
from app.research.manifest import DatasetVersion

from .models import (
    BacktestEvidence,
    CandidateSignal,
    EvidenceChain,
    EvidenceSource,
)


def candidates_from_research_output(
    output: ResearchAgentOutput,
    *,
    dataset_version: DatasetVersion,
    manifest_hash: str,
    position_caps: dict[str, float] | None = None,
    failure_conditions: dict[str, str] | None = None,
    ledger_backtests: dict[str, LedgerBacktestResult] | None = None,
    allowed_sides: tuple[str, ...] | None = None,
    max_candidates: int = 3,
) -> tuple[CandidateSignal, ...]:
    """Convert broker-neutral research signals into morning-package candidates."""

    position_caps = position_caps or {}
    failure_conditions = failure_conditions or {}
    ledger_backtests = ledger_backtests or {}
    allowed = set(allowed_sides) if allowed_sides else None
    candidates: list[CandidateSignal] = []
    seen_symbols: set[str] = set()

    for signal in output.signals:
        if len(candidates) >= max_candidates:
            break
        if allowed is not None and signal.side.value not in allowed:
            continue
        if signal.symbol in seen_symbols:
            continue
        seen_symbols.add(signal.symbol)
        backtest = ledger_backtests.get(signal.symbol)
        candidates.append(
            CandidateSignal(
                symbol=signal.symbol,
                side=signal.side.value,
                thesis=f"{signal.strategy}: {signal.reason}",
                evidence_chain=EvidenceChain(
                    sources=(
                        EvidenceSource(
                            kind="dataset",
                            source_id=f"{dataset_version.name}:{dataset_version.version}",
                            description="PIT dataset version used by research runner.",
                        ),
                        EvidenceSource(
                            kind="manifest",
                            source_id=manifest_hash,
                            description="Immutable experiment manifest hash.",
                        ),
                        EvidenceSource(
                            kind="backtest",
                            source_id=f"{output.experiment.id}:{signal.symbol}",
                            description="Ledger backtest result for symbol.",
                        ),
                    ),
                    summary=signal.reason,
                ),
                backtest=_backtest_evidence(output, signal.symbol, backtest),
                position_cap=position_caps.get(signal.symbol),
                failure_condition=failure_conditions.get(signal.symbol, ""),
                auxiliary_notes=_auxiliary_notes(signal, backtest),
            )
        )
    return tuple(candidates)


def _auxiliary_notes(signal, backtest: LedgerBacktestResult | None) -> str:
    parts = [
        f"confidence={signal.confidence:.4f}",
        f"horizon={signal.horizon}",
    ]
    if signal.state_tag:
        parts.append(f"state={signal.state_tag}")
    if signal.score is not None:
        parts.append(f"score={signal.score:.4f}")
    if backtest is not None:
        parts.append(f"trade_count={len(backtest.trades)}")
    return "; ".join(parts)


def _backtest_evidence(
    output: ResearchAgentOutput,
    symbol: str,
    backtest: LedgerBacktestResult | None,
) -> BacktestEvidence | None:
    if backtest is None:
        return None
    return BacktestEvidence(
        window=backtest.window,
        parameters={
            **backtest.parameters,
            "experiment_id": output.experiment.id,
            "symbol": symbol,
            "factors": [factor.name for factor in output.factors],
        },
        metrics=backtest.metrics(),
        max_drawdown=backtest.max_drawdown,
    )
