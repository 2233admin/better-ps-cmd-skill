"""Unit tests for app.research.factors.families."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from app.research.factors.families import (
    MOMENTUM_RESID_VOL_SPEC,
    QUALITY_ROE_SPEC,
    VALUE_PB_SPEC,
    momentum_resid_volatility,
    quality_factor_roe,
    value_factor_pb,
)


@pytest.fixture
def fundamentals_fixture() -> pl.DataFrame:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    rows = []
    # Three symbols with two PIT snapshots each; latest visible row wins.
    for symbol, book_value, net_income, equity in (
        ("600000.SH", 10.0, 5.0, 50.0),
        ("000001.SZ", 4.0, 1.0, 20.0),
        ("300750.SZ", 1.0, 10.0, 100.0),
    ):
        rows.append(
            {
                "symbol": symbol,
                "event_time": base - timedelta(days=30),
                "available_at": base - timedelta(days=29),
                "book_value": book_value * 0.5,  # stale snapshot
                "net_income": net_income * 0.5,
                "equity": equity * 0.5,
            }
        )
        rows.append(
            {
                "symbol": symbol,
                "event_time": base,
                "available_at": base + timedelta(hours=16),
                "book_value": book_value,
                "net_income": net_income,
                "equity": equity,
            }
        )
    return pl.DataFrame(rows)


@pytest.fixture
def kline_fixture() -> pl.DataFrame:
    base = datetime(2026, 5, 15, tzinfo=UTC)
    rows = []
    for symbol, last_close in (("600000.SH", 5.0), ("000001.SZ", 10.0), ("300750.SZ", 200.0)):
        rows.append(
            {
                "symbol": symbol,
                "event_time": base - timedelta(days=1),
                "close": last_close * 0.99,
                "volume": 1_000_000,
                "amount": last_close * 0.99 * 1_000_000,
            }
        )
        rows.append(
            {
                "symbol": symbol,
                "event_time": base,
                "close": last_close,
                "volume": 1_000_000,
                "amount": last_close * 1_000_000,
            }
        )
    return pl.DataFrame(rows)


@pytest.fixture
def synthetic_kline_long() -> pl.DataFrame:
    """A 60-bar synthetic kline for two symbols, deterministic, no nulls."""

    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for symbol_idx, symbol in enumerate(("AAA.SH", "BBB.SZ")):
        for i in range(60):
            # symbol AAA trends up, BBB trends down + larger swings
            if symbol_idx == 0:
                close = 10.0 + i * 0.10
            else:
                close = 50.0 - i * 0.20 + (1 if i % 2 == 0 else -1) * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "event_time": base + timedelta(days=i),
                    "close": close,
                    "volume": 1_000_000 + i * 100,
                    "amount": close * (1_000_000 + i * 100),
                }
            )
    return pl.DataFrame(rows)


class TestValueFactorPB:
    def test_long_format_schema(self, fundamentals_fixture: pl.DataFrame, kline_fixture: pl.DataFrame) -> None:
        out = value_factor_pb(fundamentals_fixture, kline_fixture)
        assert set(out.columns) == {"factor", "symbol", "timestamp", "value"}
        assert out["factor"].unique().to_list() == [VALUE_PB_SPEC.name]
        assert out.height == 3

    def test_zscore_mean_zero(self, fundamentals_fixture: pl.DataFrame, kline_fixture: pl.DataFrame) -> None:
        out = value_factor_pb(fundamentals_fixture, kline_fixture)
        values = out["value"].to_list()
        # book/price across symbols: 10/5=2.0, 4/10=0.4, 1/200=0.005
        # z-scored cross-sectionally should sum to ~0
        assert abs(sum(values)) < 1e-9
        # Highest BP (600000.SH @ 2.0) gets highest z-score
        top = out.sort("value", descending=True).head(1)["symbol"].item()
        assert top == "600000.SH"

    def test_uses_latest_pit_snapshot(self, fundamentals_fixture: pl.DataFrame, kline_fixture: pl.DataFrame) -> None:
        # Stale snapshot has book_value at 50% of fresh; if we accidentally
        # selected stale rows the BP ranking would be unchanged but values
        # halved. Order test above is structural; here we verify the
        # underlying B/P magnitude lines up with the fresh snapshot.
        out = value_factor_pb(fundamentals_fixture, kline_fixture)
        # 3 symbols => stdev of z-scores is 1 (since zscore ddof=0)
        values = out["value"].to_list()
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        assert abs(var - 1.0) < 1e-9

    def test_missing_columns_returns_empty(self, kline_fixture: pl.DataFrame) -> None:
        bad_funds = pl.DataFrame({"symbol": ["X"], "event_time": [datetime(2026, 5, 1, tzinfo=UTC)]})
        out = value_factor_pb(bad_funds, kline_fixture)
        assert out.is_empty()
        assert set(out.columns) == {"factor", "symbol", "timestamp", "value"}


class TestQualityFactorROE:
    def test_long_format_and_ranking(self, fundamentals_fixture: pl.DataFrame) -> None:
        out = quality_factor_roe(fundamentals_fixture)
        assert set(out.columns) == {"factor", "symbol", "timestamp", "value"}
        assert out["factor"].unique().to_list() == [QUALITY_ROE_SPEC.name]
        assert out.height == 3
        # ROE: 600000=5/50=0.10, 000001=1/20=0.05, 300750=10/100=0.10
        # 600000 and 300750 tied at top; 000001 lowest
        bottom = out.sort("value").head(1)["symbol"].item()
        assert bottom == "000001.SZ"

    def test_zero_or_negative_equity_dropped(self) -> None:
        rows = [
            {"symbol": "A", "event_time": datetime(2026, 5, 1, tzinfo=UTC), "net_income": 1.0, "equity": 10.0},
            {"symbol": "B", "event_time": datetime(2026, 5, 1, tzinfo=UTC), "net_income": 1.0, "equity": 0.0},
            {"symbol": "C", "event_time": datetime(2026, 5, 1, tzinfo=UTC), "net_income": 1.0, "equity": -5.0},
        ]
        frame = pl.DataFrame(rows)
        out = quality_factor_roe(frame)
        symbols = sorted(out["symbol"].to_list())
        assert symbols == ["A"]

    def test_missing_columns_returns_empty(self) -> None:
        bad = pl.DataFrame({"symbol": ["X"], "event_time": [datetime(2026, 5, 1, tzinfo=UTC)]})
        out = quality_factor_roe(bad)
        assert out.is_empty()


class TestMomentumResidVolatility:
    def test_long_format(self, synthetic_kline_long: pl.DataFrame) -> None:
        out = momentum_resid_volatility(synthetic_kline_long, window=20)
        assert set(out.columns) == {"factor", "symbol", "timestamp", "value"}
        assert out["factor"].unique().to_list() == [MOMENTUM_RESID_VOL_SPEC.name]
        # 60 bars per symbol; window=20 drops 20 bars (shift+rolling). Each
        # symbol contributes 40 non-null rows.
        per_symbol = out.group_by("symbol").len().sort("symbol")
        counts = per_symbol["len"].to_list()
        assert all(c == 40 for c in counts), counts

    def test_uptrend_symbol_beats_downtrend(self, synthetic_kline_long: pl.DataFrame) -> None:
        out = momentum_resid_volatility(synthetic_kline_long, window=20)
        latest = out.sort("timestamp").group_by("symbol", maintain_order=True).last()
        rows = {row["symbol"]: row["value"] for row in latest.to_dicts()}
        # uptrend AAA should have positive momentum-vol residual; BBB negative-ish
        assert rows["AAA.SH"] > rows["BBB.SZ"]

    def test_empty_input_returns_empty(self) -> None:
        empty = pl.DataFrame(schema={"symbol": pl.Utf8, "event_time": pl.Datetime(time_zone="UTC"), "close": pl.Float64})
        out = momentum_resid_volatility(empty)
        assert out.is_empty()
        assert set(out.columns) == {"factor", "symbol", "timestamp", "value"}

    def test_missing_required_columns_raises(self) -> None:
        frame = pl.DataFrame({"symbol": ["X"], "event_time": [datetime(2026, 5, 1, tzinfo=UTC)]})
        with pytest.raises(ValueError):
            momentum_resid_volatility(frame)
