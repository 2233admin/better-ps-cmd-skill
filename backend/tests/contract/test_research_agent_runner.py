"""Contract tests for the controlled research-agent boundary."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest


def _dataset_version():
    from app.research.manifest import DatasetVersion
    from app.research.models import Frequency, Market

    return DatasetVersion(
        name="ashare.kline_daily_pit",
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        snapshot_id="ds-ashare-daily-20240201",
        schema_hash="a" * 64,
        data_hash="b" * 64,
        as_of=datetime(2024, 2, 1, tzinfo=UTC),
        version="2024-02-01",
    )


def test_research_agent_builds_pit_experiment_without_signals_or_trading_imports():
    from app.research.agent import ResearchAgentRequest, ResearchAgentRunner

    source = (
        Path(__file__).resolve().parents[2] / "app" / "research" / "agent.py"
    ).read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all("trading" not in line for line in import_lines)
    assert all("trade" not in line for line in import_lines)

    output = ResearchAgentRunner().build_plan(
        ResearchAgentRequest(
            name="ashare_momentum_research",
            symbols=("600000.SH",),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            dataset_version=_dataset_version(),
            hypothesis="20d momentum survives PIT validation.",
        )
    )

    assert [factor.name for factor in output.factors] == [
        "momentum_20d",
        "volatility_20d",
    ]
    assert output.experiment.datasets[0].source == "duckdb.pit"
    assert output.experiment.datasets[0].as_of == datetime(2024, 2, 1, tzinfo=UTC)
    assert output.experiment.datasets[0].version == "2024-02-01"
    assert output.experiment.factors == output.factors
    assert output.signals == ()


def test_research_agent_plan_can_be_promoted_to_manifest():
    from app.research.agent import ResearchAgentRequest, ResearchAgentRunner

    dataset_version = _dataset_version()
    output = ResearchAgentRunner().build_plan(
        ResearchAgentRequest(
            name="ashare_momentum_research",
            symbols=("600000.SH",),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            dataset_version=dataset_version,
        )
    )
    manifest = ResearchAgentRunner().promote_to_manifest(
        output,
        code_commit="c8b3779",
        data_versions=(dataset_version,),
        config={"symbols": ("600000.SH",)},
        random_seed=42,
    )

    assert len(manifest.manifest_hash()) == 64
    assert manifest.data_versions[0].name == "ashare.kline_daily_pit"


def test_research_agent_run_executes_pit_factor_signal_loop_without_legacy_backtest():
    from app.research.agent import ResearchAgentRequest, ResearchAgentRunner
    from app.research.models import ExperimentStatus, SignalSide

    class FakePITStore:
        def query(self, request):
            assert request.symbol == "600000.SH"
            assert request.as_of == datetime(2024, 2, 1, tzinfo=UTC)
            days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
            closes = [10.0 + i * 0.1 for i in range(30)]
            return pl.DataFrame(
                {
                    "date": days,
                    "open": closes,
                    "high": [value + 0.05 for value in closes],
                    "low": [value - 0.05 for value in closes],
                    "close": closes,
                    "volume": [1_000_000 + i * 1000 for i in range(30)],
                    "amount": [10_000_000 + i * 10000 for i in range(30)],
                }
            )

    output = ResearchAgentRunner().run(
        ResearchAgentRequest(
            name="ashare_momentum_research",
            symbols=("600000.SH",),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            dataset_version=_dataset_version(),
            entry_threshold=0.01,
        ),
        FakePITStore(),
    )

    assert output.experiment.status == ExperimentStatus.COMPLETED
    assert output.experiment.metrics["symbols_scanned"] == 1.0
    assert output.experiment.metrics["signals_generated"] >= 1.0
    assert output.factor_values
    assert output.signals
    assert output.signals[0].side == SignalSide.BUY


def test_research_agent_rejects_unknown_factor():
    from app.research.agent import ResearchAgentRequest, ResearchAgentRunner

    with pytest.raises(ValueError, match="unsupported research agent factor"):
        ResearchAgentRunner().build_plan(
            ResearchAgentRequest(
                name="bad_factor",
                symbols=("600000.SH",),
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
                as_of=datetime(2024, 2, 1, tzinfo=UTC),
                dataset_version=_dataset_version(),
                factor_names=("future_news_alpha",),
            )
        )


def test_research_agent_rejects_mismatched_dataset_version():
    from app.research.agent import ResearchAgentRequest
    from app.research.manifest import DatasetVersion
    from app.research.models import Frequency, Market

    with pytest.raises(ValueError, match="dataset_version"):
        ResearchAgentRequest(
            name="bad_dataset",
            symbols=("600000.SH",),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            dataset_version=DatasetVersion(
                name="ashare.kline_minute_pit",
                market=Market.ASHARE,
                frequency=Frequency.DAILY,
                snapshot_id="ds-ashare-minute-20240201",
                schema_hash="a" * 64,
                data_hash="b" * 64,
                as_of=datetime(2024, 2, 1, tzinfo=UTC),
                version="2024-02-01",
            ),
        )
