"""A-share data and PIT dataset contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DatasetTier(str, Enum):
    RAW = "raw"
    NORMALIZED = "normalized"
    PIT = "pit"


class ASharePITDataset(str, Enum):
    KLINE_DAILY = "ashare.kline_daily_pit"
    KLINE_MINUTE = "ashare.kline_minute_pit"
    INSTRUMENT_STATUS = "ashare.instrument_status_pit"
    ADJUSTMENT_FACTOR = "ashare.adjustment_factor_pit"


class AShareBlockedDataset(str, Enum):
    FINANCIAL_STATEMENTS = "financial_statements"
    FUNDAMENTALS = "fundamentals"
    INDEX_CONSTITUENTS = "index_constituents"
    INDUSTRY_CLASSIFICATION = "industry_classification"
    ST_STATUS = "st_status"
    SUSPENSION_STATUS = "suspension_status"
    LIMIT_STATUS = "limit_status"
    CORPORATE_ACTIONS = "corporate_actions"
    POLICY_NEWS_MACRO_RELEASES = "policy_news_macro_releases"
    NORTHBOUND_FLOW_WITHOUT_RELEASE_TIME = "northbound_flow_without_release_time"


MANDATORY_TIME_COLUMNS = frozenset(
    {
        "event_time",
        "available_at",
        "source_updated_at",
    }
)

PRICE_BAR_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }
)

INSTRUMENT_STATUS_COLUMNS = frozenset(
    {
        "symbol",
        "event_time",
        "available_at",
        "source_updated_at",
        "is_trading",
        "is_st",
        "is_suspended",
        "limit_up",
        "limit_down",
    }
)

ADJUSTMENT_FACTOR_COLUMNS = frozenset(
    {
        "symbol",
        "event_time",
        "available_at",
        "source_updated_at",
        "adjustment_type",
        "factor",
    }
)

REQUIRED_COLUMNS_BY_DATASET = {
    ASharePITDataset.KLINE_DAILY: PRICE_BAR_COLUMNS,
    ASharePITDataset.KLINE_MINUTE: PRICE_BAR_COLUMNS,
    ASharePITDataset.INSTRUMENT_STATUS: INSTRUMENT_STATUS_COLUMNS,
    ASharePITDataset.ADJUSTMENT_FACTOR: ADJUSTMENT_FACTOR_COLUMNS,
}


@dataclass(frozen=True)
class DatasetContractCheck:
    dataset: ASharePITDataset
    passed: bool
    missing_columns: tuple[str, ...] = ()


def validate_pit_columns(
    dataset: ASharePITDataset,
    columns: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> DatasetContractCheck:
    """Validate a PIT dataset's minimum schema."""

    observed = set(columns)
    required = REQUIRED_COLUMNS_BY_DATASET[dataset]
    missing = tuple(sorted(required - observed))
    return DatasetContractCheck(
        dataset=dataset,
        passed=not missing,
        missing_columns=missing,
    )


def require_pit_dataset(dataset: str) -> ASharePITDataset:
    """Return a known PIT dataset or reject unsafe/nonexistent names."""

    try:
        return ASharePITDataset(dataset)
    except ValueError as exc:
        blocked = {item.value for item in AShareBlockedDataset}
        if dataset in blocked:
            raise ValueError(
                f"{dataset} is blocked until explicit available_at metadata exists"
            ) from exc
        raise ValueError(f"unsupported A-share PIT dataset: {dataset}") from exc


def is_research_eligible(tier: DatasetTier) -> bool:
    return tier == DatasetTier.PIT
