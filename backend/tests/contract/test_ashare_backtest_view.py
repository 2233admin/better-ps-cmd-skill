"""Contract tests for A-share backtest-ready feature views."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl


def _load_builder():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "build-ashare-backtest-view.py"
    spec = importlib.util.spec_from_file_location("build_ashare_backtest_view", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _kline_rows(symbol: str = "600000.SH") -> list[dict]:
    rows = []
    for idx in range(30):
        current = date(2026, 1, 1) + timedelta(days=idx)
        close = 10.0 + idx * 0.05
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        rows.append(
            {
                "symbol": symbol,
                "market": "SH",
                "event_time": event_time,
                "available_at": event_time + timedelta(days=1, hours=9, minutes=30),
                "source_updated_at": event_time + timedelta(days=1, hours=9, minutes=31),
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1_000 + idx,
                "amount": close * (1_000 + idx),
            }
        )
    return rows


def test_backtest_view_merges_only_visible_pit_layers_and_fills_safe_defaults(tmp_path):
    builder = _load_builder()
    lake = tmp_path / "Ashare"
    pit = lake / "pit"
    pit.mkdir(parents=True)
    pl.DataFrame(_kline_rows()).write_parquet(pit / "kline_daily_pit.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2026, 1, 20, tzinfo=UTC)],
            "available_at": [datetime(2026, 1, 21, 9, 30, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 1, 21, 9, 31, tzinfo=UTC)],
            "is_st": [False],
            "is_suspended": [False],
            "limit_up": [False],
            "limit_down": [False],
            "listed_days": [200],
            "is_tradable": [True],
            "reason": ["tradable"],
        }
    ).write_parquet(pit / "tradability_status_pit.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2026, 1, 10, tzinfo=UTC)],
            "available_at": [datetime(2026, 1, 10, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 1, 10, tzinfo=UTC)],
            "adjustment_type": ["qfq_denominator"],
            "factor": [2.0],
        }
    ).write_parquet(pit / "adjustment_factor_pit.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH"],
            "market": ["SH", "SH"],
            "event_time": [datetime(2026, 1, 19, tzinfo=UTC), datetime(2026, 1, 20, tzinfo=UTC)],
            "available_at": [
                datetime(2026, 1, 20, 9, 30, tzinfo=UTC),
                datetime(2026, 1, 21, 9, 31, tzinfo=UTC),
            ],
            "source_updated_at": [
                datetime(2026, 1, 20, 9, 31, tzinfo=UTC),
                datetime(2026, 1, 21, 9, 32, tzinfo=UTC),
            ],
            "turnover_rate": [1.1, 1.2],
            "turnover_rate_f": [1.3, 1.4],
            "volume_ratio": [0.9, 1.0],
            "pe": [5.0, 5.1],
            "pe_ttm": [5.2, 5.3],
            "pb": [0.8, 0.81],
            "ps": [1.0, 1.01],
            "ps_ttm": [1.1, 1.11],
            "dv_ratio": [2.0, 2.1],
            "dv_ttm": [2.2, 2.3],
            "total_share": [1000.0, 1000.0],
            "float_share": [800.0, 820.0],
            "free_share": [700.0, 710.0],
            "total_mv": [10000.0, 10100.0],
            "circ_mv": [8000.0, 8200.0],
        }
    ).write_parquet(pit / "market_cap_daily_pit.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2026, 1, 20, tzinfo=UTC)],
            "available_at": [datetime(2026, 1, 21, 9, 31, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 1, 21, 9, 32, tzinfo=UTC)],
            "name": ["浦发银行"],
            "industry": ["银行"],
            "area": ["上海"],
        }
    ).write_parquet(pit / "industry_daily_pit.parquet")

    result = builder.build_backtest_daily_view(
        data_root=lake,
        out_parquet=lake / "features" / "backtest_daily_pit_fixture.parquet",
        min_listed_days=21,
    )
    frame = pl.read_parquet(lake / "features" / "backtest_daily_pit_fixture.parquet")

    assert result["dataset"] == "ashare.backtest_daily_features_v1"
    assert result["backtest_ready"] is True
    assert result["excluded_sidecars"] == [
        "normalized/tdx/finance_snapshot_*.parquet",
        "normalized/tdx/block_membership_*.parquet",
        "pit/share_float_event_pit.parquet",
    ]
    first = frame.row(0, named=True)
    assert first["is_tradable"] is True
    assert first["reason"] == "no visible status PIT row; kline-only default"
    assert first["listed_days"] == 1
    assert first["free_share"] is None
    assert first["industry"] is None
    adjusted = frame.filter(pl.col("date") == date(2026, 1, 11)).row(0, named=True)
    assert adjusted["adjustment_factor"] == 2.0
    assert adjusted["adjusted_close"] == adjusted["close"] * 2.0
    assert adjusted["limit_up_proxy"] is False
    assert adjusted["limit_status_source"] == "price_proxy"
    before_visibility = frame.filter(pl.col("date") == date(2026, 1, 20)).row(0, named=True)
    assert before_visibility["free_share"] == 700.0
    assert before_visibility["industry"] is None
    after_status = frame.filter(pl.col("date") == date(2026, 1, 22)).row(0, named=True)
    assert after_status["reason"] == "tradable"
    assert after_status["listed_days"] == 200
    assert after_status["free_share"] == 710.0
    assert after_status["circ_mv"] == 8200.0
    assert after_status["industry"] == "银行"
    assert after_status["area"] == "上海"
    assert frame.filter(pl.col("research_eligible")).height == 11

    manifest = json.loads(
        (lake / "_manifest" / "backtest_daily_pit_fixture.json").read_text(encoding="utf-8")
    )
    assert manifest["content_hash"] == result["content_hash"]


def test_backtest_view_adds_price_derived_limit_proxy_when_status_missing(tmp_path):
    builder = _load_builder()
    lake = tmp_path / "Ashare"
    pit = lake / "pit"
    pit.mkdir(parents=True)
    rows = _kline_rows()
    rows[1]["close"] = rows[0]["close"] * 1.10
    rows[1]["high"] = rows[1]["close"]
    rows[2]["close"] = rows[1]["close"] * 0.90
    rows[2]["low"] = rows[2]["close"]
    pl.DataFrame(rows).write_parquet(pit / "kline_daily_pit.parquet")

    builder.build_backtest_daily_view(
        data_root=lake,
        out_parquet=lake / "features" / "backtest_daily_pit_fixture.parquet",
        min_listed_days=1,
    )
    frame = pl.read_parquet(lake / "features" / "backtest_daily_pit_fixture.parquet")

    up = frame.filter(pl.col("date") == date(2026, 1, 2)).row(0, named=True)
    down = frame.filter(pl.col("date") == date(2026, 1, 3)).row(0, named=True)
    assert up["limit_up"] is True
    assert up["limit_up_proxy"] is True
    assert up["limit_status_source"] == "price_proxy"
    assert down["limit_down"] is True
    assert down["limit_down_proxy"] is True


def test_pipeline_accepts_backtest_feature_view_as_pit_input(tmp_path):
    from app.research.pipeline import PipelineConfig, run_pipeline

    builder = _load_builder()
    lake = tmp_path / "Ashare"
    pit = lake / "pit"
    pit.mkdir(parents=True)
    pl.DataFrame(_kline_rows()).write_parquet(pit / "kline_daily_pit.parquet")
    out = lake / "features" / "backtest_daily_pit_fixture.parquet"
    builder.build_backtest_daily_view(data_root=lake, out_parquet=out)

    result = run_pipeline(
        PipelineConfig(
            package_date=date(2026, 1, 31),
            symbols=("600000.SH",),
            pit_parquet=out,
            out_dir=tmp_path / "run",
        )
    )

    assert result.control_report.decision in {"observe", "promote", "reject"}
    assert (tmp_path / "run" / "visible_kline_daily_pit.parquet").exists()
