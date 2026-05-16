"""Contract tests for research schemas, PIT, and HMC boundary."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def test_research_architecture_doc_declares_pit_and_hmc_boundaries():
    doc = Path(__file__).resolve().parents[3] / "docs" / "RESEARCH_ARCHITECTURE.md"
    text = doc.read_text(encoding="utf-8")

    assert "Point-in-Time" in text
    assert "HMC" in text
    assert "ResearchSignal" in text
    assert "KATANA_TRADING_MODE=research" in text


def test_experiment_requires_as_of_not_before_end():
    from app.research.models import DatasetRef, Experiment, FactorDefinition
    from app.research.models import FactorKind, Frequency, Market

    dataset = DatasetRef(
        name="ashare_daily",
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        source="duckdb",
    )
    factor = FactorDefinition(
        name="momentum_20d",
        kind=FactorKind.PRICE_VOLUME,
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        inputs=("close",),
    )

    with pytest.raises(ValueError, match="as_of"):
        Experiment(
            name="bad_future_leak",
            market=Market.ASHARE,
            datasets=(dataset,),
            factors=(factor,),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 1, 15, tzinfo=UTC),
        )


def test_factor_value_rejects_future_visibility():
    from app.research.models import FactorValue

    with pytest.raises(ValueError, match="as_of"):
        FactorValue(
            factor="momentum_20d",
            symbol="600000.SH",
            timestamp=datetime(2024, 1, 31, tzinfo=UTC),
            value=1.0,
            as_of=datetime(2024, 1, 30, tzinfo=UTC),
        )


def test_pit_daily_query_clips_to_as_of():
    import polars as pl

    from app.research.models import Market
    from app.research.pit import PITDataset, PointInTimeQuery, PointInTimeStore

    class FakeStore:
        def get_kline_daily(self, code, market=None, start_date="", end_date=""):
            assert code == "600000"
            assert market == 1
            assert end_date == "2024-01-01"
            return pl.DataFrame({"date": [date(2024, 1, 1)], "close": [10.5]})

    pit = PointInTimeStore(FakeStore())
    result = pit.query(
        PointInTimeQuery(
            dataset=PITDataset.KLINE_DAILY,
            market=Market.ASHARE,
            symbol="600000",
            as_of=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        )
    )

    assert result.height == 1
    assert result["close"].to_list() == [10.5]


def test_hmc_boundary_emits_state_without_trading_imports():
    import polars as pl

    from app.research.hmc import HMCInputWindow, HMCResearchAdapter

    source = (
        Path(__file__).resolve().parents[2] / "app" / "research" / "hmc.py"
    ).read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all("trading" not in line for line in import_lines)

    adapter = HMCResearchAdapter()
    state = adapter.estimate_state(
        HMCInputWindow(
            symbol="600000.SH",
            as_of=datetime(2024, 1, 31, tzinfo=UTC),
            bars=pl.DataFrame({"close": [10.0, 10.5, 11.0]}),
        )
    )

    assert state.symbol == "600000.SH"
    assert state.regime == "trend_up"
    assert 0.0 <= state.risk_score <= 1.0
