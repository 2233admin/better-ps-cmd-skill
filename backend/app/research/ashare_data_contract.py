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
    TICK_TRADE = "ashare.tick_trade_pit"
    TRADABILITY_STATUS = "ashare.tradability_status_pit"
    INSTRUMENT_STATUS = "ashare.instrument_status_pit"
    ADJUSTMENT_FACTOR = "ashare.adjustment_factor_pit"
    INDEX_DAILY = "ashare.index_daily_pit"
    MARKET_CAP_DAILY = "ashare.market_cap_daily_pit"
    INDUSTRY_DAILY = "ashare.industry_daily_pit"
    SHARE_FLOAT_EVENT = "ashare.share_float_event_pit"


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

TRADABILITY_STATUS_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "is_st",
        "is_suspended",
        "limit_up",
        "limit_down",
        "listed_days",
        "is_tradable",
        "reason",
    }
)

ADJUSTMENT_FACTOR_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "adjustment_type",
        "factor",
    }
)

MARKET_CAP_DAILY_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    }
)

INDUSTRY_DAILY_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "industry",
        "area",
    }
)

SHARE_FLOAT_EVENT_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "ann_date",
        "float_share",
        "float_ratio",
        "holder_name",
        "share_type",
    }
)

TICK_TRADE_COLUMNS = frozenset(
    {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "price",
        "volume",
        "amount",
        "trade_id",
        "side",
    }
)

REQUIRED_COLUMNS_BY_DATASET = {
    ASharePITDataset.KLINE_DAILY: PRICE_BAR_COLUMNS,
    ASharePITDataset.KLINE_MINUTE: PRICE_BAR_COLUMNS,
    ASharePITDataset.TICK_TRADE: TICK_TRADE_COLUMNS,
    ASharePITDataset.TRADABILITY_STATUS: TRADABILITY_STATUS_COLUMNS,
    ASharePITDataset.INSTRUMENT_STATUS: TRADABILITY_STATUS_COLUMNS,
    ASharePITDataset.ADJUSTMENT_FACTOR: ADJUSTMENT_FACTOR_COLUMNS,
    ASharePITDataset.INDEX_DAILY: PRICE_BAR_COLUMNS,
    ASharePITDataset.MARKET_CAP_DAILY: MARKET_CAP_DAILY_COLUMNS,
    ASharePITDataset.INDUSTRY_DAILY: INDUSTRY_DAILY_COLUMNS,
    ASharePITDataset.SHARE_FLOAT_EVENT: SHARE_FLOAT_EVENT_COLUMNS,
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
