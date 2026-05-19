"""Canonical contracts for the shared factor core.

The factor core should not care whether the upstream market is A-share or
crypto. Market adapters are responsible for mapping their local schema into the
canonical panel and factor-frame shapes defined here.
"""

from __future__ import annotations

from dataclasses import dataclass


CORE_PANEL_REQUIRED_COLUMNS = frozenset(
    {
        "asset_id",
        "market",
        "event_time",
        "available_at",
        "close",
    }
)

CORE_PANEL_RECOMMENDED_COLUMNS = frozenset(
    {
        "open",
        "high",
        "low",
        "volume",
        "notional",
        "source_updated_at",
    }
)

FACTOR_FRAME_REQUIRED_COLUMNS = frozenset(
    {
        "factor",
        "asset_id",
        "market",
        "event_time",
        "available_at",
        "value",
    }
)


@dataclass(frozen=True)
class ContractCheck:
    passed: bool
    missing_columns: tuple[str, ...] = ()


def validate_core_panel_columns(columns: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> ContractCheck:
    observed = set(columns)
    missing = tuple(sorted(CORE_PANEL_REQUIRED_COLUMNS - observed))
    return ContractCheck(passed=not missing, missing_columns=missing)


def validate_factor_frame_columns(columns: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> ContractCheck:
    observed = set(columns)
    missing = tuple(sorted(FACTOR_FRAME_REQUIRED_COLUMNS - observed))
    return ContractCheck(passed=not missing, missing_columns=missing)
