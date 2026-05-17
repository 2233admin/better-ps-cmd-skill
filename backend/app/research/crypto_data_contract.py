"""Crypto data and PIT dataset contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ashare_data_contract import DatasetTier


class CryptoPITDataset(str, Enum):
    KLINE = "crypto.kline_pit"
    FUNDING_RATE = "crypto.funding_rate_pit"
    OPEN_INTEREST = "crypto.open_interest_pit"
    MARK_PRICE = "crypto.mark_price_pit"
    INDEX_PRICE = "crypto.index_price_pit"


class CryptoMarketType(str, Enum):
    SPOT = "spot"
    SWAP = "swap"


MANDATORY_TIME_COLUMNS = frozenset(
    {
        "event_time",
        "available_at",
        "source_updated_at",
    }
)

KLINE_COLUMNS = frozenset(
    {
        "inst_id",
        "venue",
        "market_type",
        "event_time",
        "available_at",
        "source_updated_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    }
)

FUNDING_RATE_COLUMNS = frozenset(
    {
        "inst_id",
        "venue",
        "market_type",
        "event_time",
        "available_at",
        "source_updated_at",
        "funding_rate",
        "funding_time",
    }
)

OPEN_INTEREST_COLUMNS = frozenset(
    {
        "inst_id",
        "venue",
        "market_type",
        "event_time",
        "available_at",
        "source_updated_at",
        "open_interest",
        "open_interest_ccy",
    }
)

MARK_PRICE_COLUMNS = frozenset(
    {
        "inst_id",
        "venue",
        "market_type",
        "event_time",
        "available_at",
        "source_updated_at",
        "mark_price",
        "index_price",
    }
)

INDEX_PRICE_COLUMNS = frozenset(
    {
        "inst_id",
        "venue",
        "market_type",
        "event_time",
        "available_at",
        "source_updated_at",
        "index_price",
    }
)

REQUIRED_COLUMNS_BY_DATASET = {
    CryptoPITDataset.KLINE: KLINE_COLUMNS,
    CryptoPITDataset.FUNDING_RATE: FUNDING_RATE_COLUMNS,
    CryptoPITDataset.OPEN_INTEREST: OPEN_INTEREST_COLUMNS,
    CryptoPITDataset.MARK_PRICE: MARK_PRICE_COLUMNS,
    CryptoPITDataset.INDEX_PRICE: INDEX_PRICE_COLUMNS,
}


@dataclass(frozen=True)
class CryptoDatasetContractCheck:
    dataset: CryptoPITDataset
    passed: bool
    missing_columns: tuple[str, ...] = ()


def validate_crypto_pit_columns(
    dataset: CryptoPITDataset,
    columns: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> CryptoDatasetContractCheck:
    observed = set(columns)
    required = REQUIRED_COLUMNS_BY_DATASET[dataset]
    missing = tuple(sorted(required - observed))
    return CryptoDatasetContractCheck(
        dataset=dataset,
        passed=not missing,
        missing_columns=missing,
    )


def require_crypto_pit_dataset(dataset: str) -> CryptoPITDataset:
    try:
        return CryptoPITDataset(dataset)
    except ValueError as exc:
        raise ValueError(f"unsupported crypto PIT dataset: {dataset}") from exc


def is_crypto_research_eligible(tier: DatasetTier) -> bool:
    return tier == DatasetTier.PIT
