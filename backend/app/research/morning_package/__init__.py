"""A-share morning decision package contracts and renderers."""

from .builder import build_morning_package
from .converters import candidates_from_research_output
from .models import (
    BacktestEvidence,
    CandidateSignal,
    EvidenceChain,
    EvidenceSource,
    ManualAction,
    MorningPackage,
    PackageDecision,
    RiskBlock,
    TradingIntent,
)

__all__ = [
    "BacktestEvidence",
    "CandidateSignal",
    "EvidenceChain",
    "EvidenceSource",
    "ManualAction",
    "MorningPackage",
    "PackageDecision",
    "RiskBlock",
    "TradingIntent",
    "build_morning_package",
    "candidates_from_research_output",
]
