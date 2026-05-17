"""Normalize exchange market-data payloads into crypto PIT rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

import polars as pl


def normalize_okx_candles(
    *,
    inst_id: str,
    market_type: str,
    candles: Iterable[list | tuple],
    source_updated_at: datetime,
    venue: str = "okx",
) -> pl.DataFrame:
    """Normalize OKX candle arrays to the internal crypto.kline_pit schema.

    OKX candles are arrays beginning with timestamp, open, high, low, close,
    volume, and quote-volume-like fields. The normalizer is deliberately
    read-only and does not touch credentials or private endpoints.
    """

    rows: list[dict] = []
    source_ts = _as_utc(source_updated_at)
    for item in candles:
        if len(item) < 7:
            raise ValueError("OKX candle requires at least 7 fields")
        event_time = _okx_timestamp(item[0])
        quote_volume = float(item[6])
        if len(item) > 7 and item[7] not in (None, ""):
            quote_volume = float(item[7])
        rows.append(
            {
                "inst_id": inst_id,
                "venue": venue,
                "market_type": market_type,
                "event_time": event_time,
                "available_at": source_ts,
                "source_updated_at": source_ts,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "quote_volume": quote_volume,
            }
        )
    return pl.DataFrame(rows)


def normalize_okx_funding_rates(
    *,
    inst_id: str,
    funding_rates: Iterable[dict | list | tuple],
    source_updated_at: datetime,
    venue: str = "okx",
) -> pl.DataFrame:
    """Normalize OKX funding rows to `crypto.funding_rate_pit`."""

    rows: list[dict] = []
    source_ts = _as_utc(source_updated_at)
    for item in funding_rates:
        payload = _row_payload(item)
        funding_time = _okx_timestamp(
            payload.get("fundingTime") or payload.get("funding_time") or payload.get("event_time")
        )
        rows.append(
            {
                "inst_id": str(payload.get("instId") or payload.get("inst_id") or inst_id),
                "venue": venue,
                "market_type": "swap",
                "event_time": funding_time,
                "available_at": source_ts,
                "source_updated_at": source_ts,
                "funding_rate": float(payload.get("fundingRate") or payload.get("funding_rate") or 0.0),
                "funding_time": funding_time,
            }
        )
    return pl.DataFrame(rows)


def normalize_okx_mark_index_prices(
    *,
    inst_id: str,
    market_type: str,
    prices: Iterable[dict | list | tuple],
    source_updated_at: datetime,
    venue: str = "okx",
) -> pl.DataFrame:
    """Normalize mark/index price rows to `crypto.mark_price_pit`."""

    rows: list[dict] = []
    source_ts = _as_utc(source_updated_at)
    for item in prices:
        payload = _row_payload(item)
        event_time = _okx_timestamp(payload.get("ts") or payload.get("event_time"))
        mark_price = payload.get("markPx") or payload.get("mark_price") or payload.get("markPrice")
        index_price = payload.get("idxPx") or payload.get("index_price") or payload.get("indexPrice")
        rows.append(
            {
                "inst_id": str(payload.get("instId") or payload.get("inst_id") or inst_id),
                "venue": venue,
                "market_type": market_type,
                "event_time": event_time,
                "available_at": source_ts,
                "source_updated_at": source_ts,
                "mark_price": float(mark_price),
                "index_price": float(index_price),
            }
        )
    return pl.DataFrame(rows)


def normalize_okx_open_interest(
    *,
    inst_id: str,
    open_interest: Iterable[dict | list | tuple],
    source_updated_at: datetime,
    venue: str = "okx",
) -> pl.DataFrame:
    """Normalize OKX open-interest rows to `crypto.open_interest_pit`."""

    rows: list[dict] = []
    source_ts = _as_utc(source_updated_at)
    for item in open_interest:
        payload = _row_payload(item)
        event_time = _okx_timestamp(payload.get("ts") or payload.get("event_time"))
        rows.append(
            {
                "inst_id": str(payload.get("instId") or payload.get("inst_id") or inst_id),
                "venue": venue,
                "market_type": "swap",
                "event_time": event_time,
                "available_at": source_ts,
                "source_updated_at": source_ts,
                "open_interest": float(payload.get("oi") or payload.get("open_interest") or 0.0),
                "open_interest_ccy": float(
                    payload.get("oiCcy") or payload.get("open_interest_ccy") or 0.0
                ),
            }
        )
    return pl.DataFrame(rows)


def _okx_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    ts = int(value)
    if ts > 10_000_000_000:
        ts = ts // 1000
    return datetime.fromtimestamp(ts, tz=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _row_payload(item: dict | list | tuple) -> dict:
    if isinstance(item, dict):
        return item
    if len(item) < 2:
        raise ValueError("OKX row requires at least timestamp and value")
    return {"event_time": item[0], "value": item[1]}
