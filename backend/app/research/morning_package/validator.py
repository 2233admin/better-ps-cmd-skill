"""Hard validation and downgrade rules for morning packages."""

from __future__ import annotations

from .models import (
    CandidateSignal,
    EvidenceChain,
    EvidenceSourceKind,
    PackageDecision,
    TradingIntent,
)


def package_blockers(
    dataset_version: str | None,
    code_commit: str | None,
    manifest_hash: str | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not dataset_version:
        blockers.append("missing dataset version")
    if not code_commit:
        blockers.append("missing code commit")
    if not manifest_hash:
        blockers.append("missing manifest hash")
    return tuple(blockers)


def validate_candidate(candidate: CandidateSignal) -> TradingIntent:
    reasons: list[str] = []
    auxiliary_notes = candidate.auxiliary_notes
    evidence_chain = candidate.evidence_chain

    if evidence_chain is None:
        reasons.append("missing evidence chain")
    elif not evidence_chain.authoritative_sources():
        reasons.append("LLM auxiliary text cannot be used as evidence")
        evidence_chain = None
    elif any(source.kind == EvidenceSourceKind.LLM for source in evidence_chain.sources):
        evidence_chain = EvidenceChain(
            sources=evidence_chain.authoritative_sources(),
            summary=evidence_chain.summary,
        )
        auxiliary_notes = _append_note(
            auxiliary_notes,
            "LLM evidence was ignored and moved to auxiliary notes",
        )

    if candidate.backtest is None:
        reasons.append("missing backtest evidence")
    elif not candidate.backtest.window:
        reasons.append("missing backtest window")
    elif candidate.backtest.max_drawdown is None:
        reasons.append("missing max drawdown")

    if candidate.position_cap is None:
        reasons.append("missing position cap")
    if not candidate.failure_condition.strip():
        reasons.append("missing failure condition")
    if not candidate.symbol.strip():
        reasons.append("missing symbol")
    if not candidate.thesis.strip():
        reasons.append("missing thesis")

    decision = PackageDecision.OBSERVE if reasons else PackageDecision.TRADE
    return TradingIntent(
        symbol=candidate.symbol,
        side=candidate.side,
        thesis=candidate.thesis,
        decision=decision,
        evidence_chain=evidence_chain,
        backtest=candidate.backtest,
        position_cap=candidate.position_cap,
        failure_condition=candidate.failure_condition,
        auxiliary_notes=auxiliary_notes,
        downgrade_reasons=tuple(reasons),
    )


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    return f"{existing} | {note}"
