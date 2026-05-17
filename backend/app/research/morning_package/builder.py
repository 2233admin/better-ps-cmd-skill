"""Builder for A-share morning decision packages."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.research.manifest import DatasetVersion, ExperimentManifest

from .models import CandidateSignal, MorningPackage, PackageDecision, RiskBlock, now_utc
from .validator import package_blockers, validate_candidate


def _dataset_version_id(
    manifest: ExperimentManifest | None,
    dataset_version: DatasetVersion | str | None,
) -> str | None:
    if isinstance(dataset_version, DatasetVersion):
        return dataset_version.version
    if isinstance(dataset_version, str):
        return dataset_version
    if manifest and manifest.data_versions:
        return manifest.data_versions[0].version
    return None


def _code_commit(manifest: ExperimentManifest | None, code_commit: str | None) -> str | None:
    return code_commit or (manifest.code_commit if manifest else None)


def _config_hash(manifest: ExperimentManifest | None, config_hash: str | None) -> str | None:
    return config_hash or (manifest.config_hash if manifest else None)


def build_morning_package(
    *,
    package_date: date,
    candidates: tuple[CandidateSignal, ...] | list[CandidateSignal],
    manifest: ExperimentManifest | None = None,
    manifest_hash: str | None = None,
    dataset_version: DatasetVersion | str | None = None,
    code_commit: str | None = None,
    config_hash: str | None = None,
    risk: RiskBlock | None = None,
    audit_context: dict[str, Any] | None = None,
) -> MorningPackage:
    resolved_dataset = _dataset_version_id(manifest, dataset_version)
    resolved_commit = _code_commit(manifest, code_commit)
    resolved_manifest_hash = manifest_hash or (manifest.manifest_hash() if manifest else None)
    resolved_config_hash = _config_hash(manifest, config_hash)
    blockers = package_blockers(resolved_dataset, resolved_commit, resolved_manifest_hash)
    intents = tuple(validate_candidate(candidate) for candidate in tuple(candidates)[:3])

    do_not_trade_reasons = list(blockers)
    if not intents:
        do_not_trade_reasons.append("no candidate opportunities")
    do_not_trade_reasons.extend(
        f"{intent.symbol}: {reason}"
        for intent in intents
        for reason in intent.downgrade_reasons
    )

    if blockers:
        decision = PackageDecision.DO_NOT_TRADE
        intents = tuple(
            intent
            if intent.decision != PackageDecision.TRADE
            else _downgrade_trade_intent(intent, "package audit blockers")
            for intent in intents
        )
    elif any(intent.decision == PackageDecision.TRADE for intent in intents):
        decision = PackageDecision.TRADE
    else:
        decision = PackageDecision.OBSERVE

    audit = {
        "dataset_version": resolved_dataset,
        "code_commit": resolved_commit,
        "manifest_hash": resolved_manifest_hash,
        "config_hash": resolved_config_hash,
        "backtests": [
            {
                "symbol": intent.symbol,
                "window": intent.backtest.window if intent.backtest else None,
                "parameters": intent.backtest.parameters if intent.backtest else {},
                "metrics": intent.backtest.metrics if intent.backtest else {},
                "max_drawdown": intent.backtest.max_drawdown if intent.backtest else None,
            }
            for intent in intents
        ],
        "risk_metrics": audit_context.get("risk_metrics", {}) if audit_context else {},
        "context": audit_context or {},
    }

    return MorningPackage(
        package_date=package_date,
        generated_at=now_utc(),
        decision=decision,
        dataset_version=resolved_dataset,
        code_commit=resolved_commit,
        manifest_hash=resolved_manifest_hash,
        config_hash=resolved_config_hash,
        risk=risk or RiskBlock(data_integrity=("PIT dataset lineage required",)),
        intents=intents,
        do_not_trade_reasons=tuple(do_not_trade_reasons or ("risk policy allows no forced trades",)),
        audit=audit,
    )


def _downgrade_trade_intent(intent, reason: str):
    from dataclasses import replace

    return replace(
        intent,
        decision=PackageDecision.OBSERVE,
        downgrade_reasons=tuple((*intent.downgrade_reasons, reason)),
    )
