"""Crypto PIT parquet writer for funding-rate / open-interest / mark-price.

Spike: XAR-423
Follows CRYPTO_DATA_PIT_SPEC.md:
  - event_time: exchange event timestamp (ms -> datetime[us, UTC])
  - available_at: earliest timestamp the row could be used by research
  - source_updated_at: ingest timestamp

For funding rate: event_time = fundingTime, available_at = fundingTime (funding
is published at settlement, immediately available). realizedRate is only known
after settlement so available_at == event_time is correct here.

For open interest + mark price: snapshot data. event_time = ts (exchange ts),
available_at = ingest time (NOW) because these are point-in-time snapshots
that become available only when we poll them.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
from loguru import logger

from .okx_ccxt_adapter import OKXCCXTAdapter

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Schema constants (mirrors CRYPTO_DATA_PIT_SPEC.md)
# ---------------------------------------------------------------------------

FUNDING_RATE_COLUMNS = [
    "inst_id",
    "venue",
    "market_type",
    "event_time",
    "available_at",
    "source_updated_at",
    "funding_rate",
    "realized_rate",
    "formula_type",
    "method",
]

OPEN_INTEREST_COLUMNS = [
    "inst_id",
    "venue",
    "market_type",
    "event_time",
    "available_at",
    "source_updated_at",
    "oi_contracts",
    "oi_base_ccy",
    "oi_usd",
]

MARK_PRICE_COLUMNS = [
    "inst_id",
    "venue",
    "market_type",
    "event_time",
    "available_at",
    "source_updated_at",
    "mark_price",
]


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ms_to_dt(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _funding_row(raw: dict, ingest_ts: datetime) -> dict:
    event_ts = _ms_to_dt(raw["fundingTime"])
    return {
        "inst_id": raw.get("instId", ""),
        "venue": "okx",
        "market_type": "swap",
        "event_time": event_ts,
        # funding rate becomes available at settlement (fundingTime itself)
        "available_at": event_ts,
        "source_updated_at": ingest_ts,
        "funding_rate": float(raw.get("fundingRate") or 0),
        "realized_rate": float(raw.get("realizedRate") or 0),
        "formula_type": raw.get("formulaType", ""),
        "method": raw.get("method", ""),
    }


def _oi_row(raw: dict, ingest_ts: datetime) -> dict:
    event_ts = _ms_to_dt(raw["ts"])
    # OKX exchange ts can be slightly ahead of local clock due to skew.
    # available_at must be >= event_time; take the later of the two.
    available_at = max(event_ts, ingest_ts)
    return {
        "inst_id": raw.get("instId", ""),
        "venue": "okx",
        "market_type": "swap",
        "event_time": event_ts,
        "available_at": available_at,
        "source_updated_at": ingest_ts,
        "oi_contracts": float(raw.get("oi") or 0),
        "oi_base_ccy": float(raw.get("oiCcy") or 0),
        "oi_usd": float(raw.get("oiUsd") or 0),
    }


def _mark_row(raw: dict, ingest_ts: datetime) -> dict:
    event_ts = _ms_to_dt(raw["ts"])
    # Same clock-skew guard as OI.
    available_at = max(event_ts, ingest_ts)
    return {
        "inst_id": raw.get("instId", ""),
        "venue": "okx",
        "market_type": "swap",
        "event_time": event_ts,
        "available_at": available_at,
        "source_updated_at": ingest_ts,
        "mark_price": float(raw.get("markPx") or 0),
    }


# ---------------------------------------------------------------------------
# Schema validators (invariant checking)
# ---------------------------------------------------------------------------

def validate_pit_invariants(df: pl.DataFrame, dataset_name: str) -> list[str]:
    """Return list of violation strings; empty = pass."""
    violations: list[str] = []

    for col in ("event_time", "available_at", "source_updated_at"):
        if col not in df.columns:
            violations.append(f"{dataset_name}: missing required column '{col}'")

    if not violations:
        # available_at must not precede event_time (would imply future knowledge)
        # NOTE: for snapshot data (OI/mark), available_at >= event_time always
        bad = df.filter(pl.col("available_at") < pl.col("event_time"))
        if len(bad) > 0:
            violations.append(
                f"{dataset_name}: {len(bad)} rows where available_at < event_time (PIT violation)"
            )

        # no nulls in time columns
        for col in ("event_time", "available_at", "source_updated_at"):
            null_count = df[col].null_count()
            if null_count > 0:
                violations.append(f"{dataset_name}: {null_count} null values in '{col}'")

        # uniqueness on (inst_id, event_time) for funding
        if "funding_rate" in df.columns:
            dup_count = len(df) - len(df.unique(subset=["inst_id", "event_time"]))
            if dup_count > 0:
                violations.append(
                    f"{dataset_name}: {dup_count} duplicate (inst_id, event_time) rows"
                )

    return violations


# ---------------------------------------------------------------------------
# Backfill functions
# ---------------------------------------------------------------------------

def backfill_funding_rate(
    inst_id: str,
    out_dir: Path,
    limit: int = 100,
    adapter: OKXCCXTAdapter | None = None,
) -> pl.DataFrame:
    """Fetch funding rate history and write PIT parquet.

    Args:
        inst_id: OKX instrument id, e.g. "BTC-USDT-SWAP"
        out_dir: output directory for parquet files
        limit: max rows to fetch (OKX max 100 per call)
        adapter: optional pre-built adapter (useful in tests)

    Returns:
        Polars DataFrame with FUNDING_RATE_COLUMNS schema
    """
    if adapter is None:
        adapter = OKXCCXTAdapter()

    ingest_ts = _now_utc()
    raw_rows = adapter.get_funding_rate_history(inst_id, limit=limit)

    if not raw_rows:
        logger.warning(f"backfill_funding_rate: no data for {inst_id}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in FUNDING_RATE_COLUMNS})

    rows = [_funding_row(r, ingest_ts) for r in raw_rows]
    df = pl.DataFrame(rows).with_columns([
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
    ]).sort("event_time")

    violations = validate_pit_invariants(df, "funding_rate")
    if violations:
        for v in violations:
            logger.error(f"PIT invariant violation: {v}")
        raise ValueError(f"PIT invariants failed for {inst_id}: {violations}")

    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = ingest_ts.strftime("%Y%m%d")
    out_path = out_dir / f"funding_rate_{inst_id.replace('-', '_')}_{date_tag}.parquet"
    df.write_parquet(out_path)
    logger.info(f"funding_rate: wrote {len(df)} rows -> {out_path}")
    return df


def backfill_open_interest(
    inst_id: str,
    out_dir: Path,
    adapter: OKXCCXTAdapter | None = None,
) -> pl.DataFrame:
    """Fetch current open interest snapshot and write PIT parquet."""
    if adapter is None:
        adapter = OKXCCXTAdapter()

    ingest_ts = _now_utc()
    raw = adapter.get_open_interest(inst_id)

    if not raw:
        logger.warning(f"backfill_open_interest: no data for {inst_id}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in OPEN_INTEREST_COLUMNS})

    row = _oi_row(raw, ingest_ts)
    df = pl.DataFrame([row]).with_columns([
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
    ])

    violations = validate_pit_invariants(df, "open_interest")
    if violations:
        for v in violations:
            logger.error(f"PIT invariant violation: {v}")
        raise ValueError(f"PIT invariants failed for {inst_id}: {violations}")

    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = ingest_ts.strftime("%Y%m%d")
    out_path = out_dir / f"open_interest_{inst_id.replace('-', '_')}_{date_tag}.parquet"
    df.write_parquet(out_path)
    logger.info(f"open_interest: wrote {len(df)} rows -> {out_path}")
    return df


def backfill_mark_price(
    inst_id: str,
    out_dir: Path,
    adapter: OKXCCXTAdapter | None = None,
) -> pl.DataFrame:
    """Fetch current mark price snapshot and write PIT parquet."""
    if adapter is None:
        adapter = OKXCCXTAdapter()

    ingest_ts = _now_utc()
    raw = adapter.get_mark_price(inst_id)

    if not raw:
        logger.warning(f"backfill_mark_price: no data for {inst_id}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in MARK_PRICE_COLUMNS})

    row = _mark_row(raw, ingest_ts)
    df = pl.DataFrame([row]).with_columns([
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
    ])

    violations = validate_pit_invariants(df, "mark_price")
    if violations:
        for v in violations:
            logger.error(f"PIT invariant violation: {v}")
        raise ValueError(f"PIT invariants failed for {inst_id}: {violations}")

    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = ingest_ts.strftime("%Y%m%d")
    out_path = out_dir / f"mark_price_{inst_id.replace('-', '_')}_{date_tag}.parquet"
    df.write_parquet(out_path)
    logger.info(f"mark_price: wrote {len(df)} rows -> {out_path}")
    return df
