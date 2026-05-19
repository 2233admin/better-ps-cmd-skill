"""Factor family builders for the cross-sectional research pipeline (XAR-411).

Each builder takes Polars DataFrames in the same schema conventions as the
runner (``symbol``, ``event_time``, ``available_at`` for PIT, plus whatever
panel data the factor needs) and returns a long-form factor frame matching
``factor_frame`` produced by ``pipeline/runner.py``:

    columns: factor (str), symbol (str), timestamp (datetime UTC), value (f64)

Value & quality families need a fundamentals lake that is **not yet built** in
this repo (no producer exists under ``app.research.pipeline.data_lake``). The
builders accept a fundamentals frame from the consumer so they can be unit
tested today with an inline fixture, and dropped into the runner once a
``resolve_fundamentals_parquet(...)`` helper lands. Schema expected per row::

    {symbol, event_time, available_at, book_value, net_income, equity}

The momentum-residualized volatility variant uses only OHLCV data already
materialized by the existing fixture, so it can run on the demo pipeline today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import polars as pl

from app.research.factors.primitives.transforms import zscore


@dataclass(frozen=True)
class FamilySpec:
    """Static metadata for a factor family. Used by the runner for audit rows."""

    name: str
    requires: tuple[str, ...]
    description: str


VALUE_PB_SPEC = FamilySpec(
    name="value_pb_zscore",
    requires=("fundamentals.book_value", "kline.close"),
    description="Cross-sectional z-score of book-to-price (book_value / close).",
)

QUALITY_ROE_SPEC = FamilySpec(
    name="quality_roe_zscore",
    requires=("fundamentals.net_income", "fundamentals.equity"),
    description="Cross-sectional z-score of ROE (net_income / equity).",
)

MOMENTUM_RESID_VOL_SPEC = FamilySpec(
    name="momentum_resid_volatility",
    requires=("kline.close",),
    description=(
        "20-day momentum minus its 20-day rolling volatility, per symbol. "
        "Penalises momentum carried by noise; runs on OHLCV-only inputs."
    ),
)


def _to_long(wide: pl.DataFrame, factor_name: str) -> pl.DataFrame:
    """Convert a (symbol, timestamp, value) wide frame to runner long format."""

    schema = {
        "factor": pl.Utf8,
        "symbol": pl.Utf8,
        "timestamp": wide.schema["timestamp"],
        "value": pl.Float64,
    }
    if wide.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        wide.select(
            pl.lit(factor_name).alias("factor"),
            pl.col("symbol").cast(pl.Utf8),
            pl.col("timestamp"),
            pl.col("value").cast(pl.Float64),
        )
        .drop_nulls("value")
    )


def _latest_pit_per_symbol(
    fundamentals: pl.DataFrame,
    as_of_column: str = "available_at",
) -> pl.DataFrame:
    """Pick the most recent PIT-visible fundamentals row per symbol."""

    if fundamentals.is_empty():
        return fundamentals
    sort_keys = ["symbol"]
    if "event_time" in fundamentals.columns:
        sort_keys.append("event_time")
    if as_of_column in fundamentals.columns:
        sort_keys.append(as_of_column)
    return fundamentals.sort(sort_keys).group_by("symbol", maintain_order=True).last()


def _latest_close_per_symbol(kline: pl.DataFrame) -> pl.DataFrame:
    """Latest close per symbol from the runner's PIT kline frame."""

    if kline.is_empty():
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "event_time": kline.schema.get("event_time", pl.Datetime),
                "close": pl.Float64,
            }
        )
    if "symbol" not in kline.columns or "close" not in kline.columns:
        raise ValueError("kline frame must include symbol and close")
    time_col = "event_time" if "event_time" in kline.columns else "date"
    if time_col not in kline.columns:
        raise ValueError("kline frame must include event_time or date")
    return (
        kline.sort(["symbol", time_col])
        .group_by("symbol", maintain_order=True)
        .last()
        .select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col(time_col).alias("event_time"),
            pl.col("close").cast(pl.Float64),
        )
    )


def value_factor_pb(
    fundamentals: pl.DataFrame,
    kline: pl.DataFrame,
) -> pl.DataFrame:
    """Cross-sectional book-to-price factor, z-scored."""

    required = {"symbol", "book_value"}
    if fundamentals.is_empty() or not required.issubset(set(fundamentals.columns)):
        return _to_long(
            pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime(time_zone="UTC"),
                    "value": pl.Float64,
                }
            ),
            VALUE_PB_SPEC.name,
        )

    latest_funds = _latest_pit_per_symbol(fundamentals).select(
        pl.col("symbol").cast(pl.Utf8),
        pl.col("book_value").cast(pl.Float64),
    )
    latest_price = _latest_close_per_symbol(kline)
    joined = latest_funds.join(latest_price, on="symbol", how="inner")
    if joined.is_empty():
        return _to_long(
            joined.rename({"event_time": "timestamp"}).with_columns(pl.lit(None).alias("value")),
            VALUE_PB_SPEC.name,
        )

    raw = (pl.col("book_value") / pl.col("close")).alias("_bp")
    scored = joined.with_columns(raw).with_columns(zscore(pl.col("_bp")).alias("value"))
    wide = scored.select(
        pl.col("symbol"),
        pl.col("event_time").alias("timestamp"),
        pl.col("value"),
    )
    return _to_long(wide, VALUE_PB_SPEC.name)


def quality_factor_roe(fundamentals: pl.DataFrame) -> pl.DataFrame:
    """Cross-sectional ROE factor, z-scored."""

    required = {"symbol", "net_income", "equity"}
    if fundamentals.is_empty() or not required.issubset(set(fundamentals.columns)):
        return _to_long(
            pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime(time_zone="UTC"),
                    "value": pl.Float64,
                }
            ),
            QUALITY_ROE_SPEC.name,
        )

    time_col = "event_time" if "event_time" in fundamentals.columns else None
    latest = _latest_pit_per_symbol(fundamentals).filter(pl.col("equity") > 0)
    if latest.is_empty():
        return _to_long(
            pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime(time_zone="UTC"),
                    "value": pl.Float64,
                }
            ),
            QUALITY_ROE_SPEC.name,
        )

    raw = (pl.col("net_income") / pl.col("equity")).alias("_roe")
    scored = latest.with_columns(raw).with_columns(zscore(pl.col("_roe")).alias("value"))
    timestamp_expr = (
        pl.col(time_col).alias("timestamp")
        if time_col is not None
        else pl.lit(None, dtype=pl.Datetime(time_zone="UTC")).alias("timestamp")
    )
    wide = scored.select(
        pl.col("symbol").cast(pl.Utf8),
        timestamp_expr,
        pl.col("value").cast(pl.Float64),
    )
    return _to_long(wide, QUALITY_ROE_SPEC.name)


def momentum_resid_volatility(
    kline: pl.DataFrame,
    window: int = 20,
) -> pl.DataFrame:
    """20-day momentum minus 20-day realised volatility, per symbol."""

    if kline.is_empty():
        return _to_long(
            pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime(time_zone="UTC"),
                    "value": pl.Float64,
                }
            ),
            MOMENTUM_RESID_VOL_SPEC.name,
        )
    required = {"symbol", "close"}
    if not required.issubset(set(kline.columns)):
        raise ValueError("momentum_resid_volatility requires symbol and close columns")

    time_col = "event_time" if "event_time" in kline.columns else "date"
    if time_col not in kline.columns:
        raise ValueError("momentum_resid_volatility requires event_time or date")
    price_col = "adjusted_close" if "adjusted_close" in kline.columns else "close"

    sorted_frame = kline.sort(["symbol", time_col])
    momentum = (pl.col(price_col) / pl.col(price_col).shift(window) - 1).over("symbol")
    vol = pl.col(price_col).pct_change().rolling_std(window).over("symbol")
    wide = (
        sorted_frame.with_columns((momentum - vol).alias("value"))
        .select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col(time_col).alias("timestamp"),
            pl.col("value").cast(pl.Float64),
        )
    )
    return _to_long(wide, MOMENTUM_RESID_VOL_SPEC.name)


FAMILIES: tuple[FamilySpec, ...] = (
    VALUE_PB_SPEC,
    QUALITY_ROE_SPEC,
    MOMENTUM_RESID_VOL_SPEC,
)


__all__: Iterable[str] = (
    "FamilySpec",
    "VALUE_PB_SPEC",
    "QUALITY_ROE_SPEC",
    "MOMENTUM_RESID_VOL_SPEC",
    "FAMILIES",
    "value_factor_pb",
    "quality_factor_roe",
    "momentum_resid_volatility",
)
