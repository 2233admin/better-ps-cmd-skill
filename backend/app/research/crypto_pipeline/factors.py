"""Pure crypto PIT factors and signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl


@dataclass(frozen=True)
class CryptoFactorValue:
    factor: str
    inst_id: str
    timestamp: datetime
    value: float
    as_of: datetime


@dataclass(frozen=True)
class CryptoSignal:
    inst_id: str
    side: str
    timestamp: datetime
    as_of: datetime
    strategy: str
    confidence: float
    reason: str


def calculate_crypto_factors(frame: pl.DataFrame) -> pl.DataFrame:
    if "close" not in frame.columns:
        raise ValueError("crypto factors require close")
    if "funding_rate" not in frame.columns:
        frame = frame.with_columns(pl.lit(0.0).alias("funding_rate"))
    if "mark_price" not in frame.columns:
        frame = frame.with_columns(pl.col("close").alias("mark_price"))
    if "index_price" not in frame.columns:
        frame = frame.with_columns(pl.col("close").alias("index_price"))
    if "spot_close" not in frame.columns:
        frame = frame.with_columns(pl.col("index_price").alias("spot_close"))
    if "open_interest" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest"))
    return frame.sort("event_time").with_columns(
        (pl.col("close") / pl.col("close").shift(20) - 1).alias("momentum_20"),
        pl.col("close").pct_change().rolling_std(20).alias("volatility_20"),
        pl.col("funding_rate").rolling_mean(20).fill_null(pl.col("funding_rate")).alias(
            "funding_pressure"
        ),
        (pl.col("funding_rate") * 3 * 365).alias("funding_annualized"),
        ((pl.col("mark_price") - pl.col("index_price")) / pl.col("index_price")).alias(
            "mark_index_basis"
        ),
        ((pl.col("close") - pl.col("spot_close")) / pl.col("spot_close")).alias(
            "perp_spot_basis"
        ),
        pl.col("open_interest").pct_change().alias("open_interest_change"),
        (pl.col("quote_volume") / pl.col("close")).alias("liquidity_score"),
        pl.col("close").pct_change().rolling_std(20).alias("volatility_regime"),
    )


def factor_values(frame: pl.DataFrame, *, inst_id: str, as_of: datetime) -> tuple[CryptoFactorValue, ...]:
    values: list[CryptoFactorValue] = []
    for row in frame.iter_rows(named=True):
        timestamp = _as_datetime(row["event_time"], as_of)
        for factor in (
            "funding_annualized",
            "mark_index_basis",
            "perp_spot_basis",
            "open_interest_change",
            "liquidity_score",
            "volatility_regime",
            "momentum_20",
            "volatility_20",
            "funding_pressure",
        ):
            value = row.get(factor)
            if value is None:
                continue
            values.append(
                CryptoFactorValue(
                    factor=factor,
                    inst_id=inst_id,
                    timestamp=timestamp,
                    value=float(value),
                    as_of=as_of,
                )
            )
    return tuple(values)


def generate_crypto_signals(
    frame: pl.DataFrame,
    *,
    inst_id: str,
    as_of: datetime,
    market_type: str,
    allow_short: bool,
    entry_threshold: float = 0.10,
    exit_threshold: float = -0.10,
) -> tuple[CryptoSignal, ...]:
    signals: list[CryptoSignal] = []
    for row in frame.iter_rows(named=True):
        funding = _float_or_none(row.get("funding_annualized"))
        mark_basis = _float_or_none(row.get("mark_index_basis"))
        perp_basis = _float_or_none(row.get("perp_spot_basis"))
        liquidity = _float_or_none(row.get("liquidity_score")) or 0.0
        volatility = _float_or_none(row.get("volatility_regime")) or 0.0

        if market_type == "swap" and funding is not None and liquidity > 0 and volatility < 0.20:
            side = "flat"
            if funding > entry_threshold and allow_short:
                side = "short"
            elif funding < exit_threshold:
                side = "long"
            if side != "flat":
                signals.append(
                    CryptoSignal(
                        inst_id=inst_id,
                        side=side,
                        timestamp=_as_datetime(row["event_time"], as_of),
                        as_of=as_of,
                        strategy="crypto_funding_basis_v1",
                        confidence=min(1.0, abs(funding) / 0.50),
                        reason=(
                            f"funding_annualized={funding:.4f};"
                            f"mark_index_basis={(mark_basis or 0.0):.6f};"
                            f"perp_spot_basis={(perp_basis or 0.0):.6f}"
                        ),
                    )
                )
                continue

        momentum = row.get("momentum_20")
        if momentum is None:
            continue
        value = float(momentum)
        side = "flat"
        if value > entry_threshold:
            side = "buy" if market_type == "spot" else "long"
        elif value < exit_threshold:
            side = "sell-to-flat"
            if market_type == "swap" and allow_short:
                side = "short"
        if side == "flat":
            continue
        signals.append(
            CryptoSignal(
                inst_id=inst_id,
                side=side,
                timestamp=_as_datetime(row["event_time"], as_of),
                as_of=as_of,
                strategy="crypto_funding_basis_fallback_momentum_v1",
                confidence=min(1.0, abs(value) / 0.10),
                reason=f"momentum_20={value:.4f}",
            )
        )
    return tuple(signals)


def ledger_signal_frame(bars: pl.DataFrame, signals: tuple[CryptoSignal, ...]) -> pl.DataFrame:
    times = bars.sort("event_time")["event_time"].to_list()
    signal_by_time = {signal.timestamp: signal for signal in signals}
    values: list[int] = []
    for value in times:
        timestamp = _as_datetime(value, datetime.now(UTC))
        signal = signal_by_time.get(timestamp)
        if signal is None:
            values.append(0)
        elif signal.side in {"buy", "long"}:
            values.append(1)
        elif signal.side == "short":
            values.append(-1)
        else:
            values.append(0)
    return pl.DataFrame({"event_time": times, "signal": values})


def _as_datetime(value: object, as_of: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=as_of.tzinfo or UTC)
    raise ValueError(f"unsupported crypto timestamp: {value!r}")


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
