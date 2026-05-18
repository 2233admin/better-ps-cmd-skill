from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from app.data.delta_lake import canonical_delta_path, write_polars_delta


def _load_checker():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "check-ashare-factor-data-readiness.py"
    spec = importlib.util.spec_from_file_location("check_ashare_factor_data_readiness", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bar(symbol: str) -> dict:
    event_time = datetime(2026, 1, 2, tzinfo=UTC)
    return {
        "symbol": symbol,
        "market": symbol.split(".")[1],
        "event_time": event_time,
        "available_at": datetime(2026, 1, 3, 9, 30, tzinfo=UTC),
        "source_updated_at": datetime(2026, 1, 3, 9, 31, tzinfo=UTC),
        "open": 10.0,
        "high": 10.1,
        "low": 9.9,
        "close": 10.0,
        "volume": 100,
        "amount": 1000.0,
    }


def test_factor_readiness_reports_price_ready_and_fundamental_blockers(tmp_path):
    checker = _load_checker()
    root = tmp_path / "Ashare"
    (root / "pit").mkdir(parents=True)
    (root / "features").mkdir()
    (root / "normalized" / "tdx").mkdir(parents=True)
    pl.DataFrame([_bar("600000.SH")]).write_parquet(root / "pit" / "kline_daily_pit.parquet")
    pl.DataFrame([_bar("000001.SH")]).write_parquet(root / "pit" / "index_daily_pit.parquet")
    pl.DataFrame(
        [
            _bar("600000.SH")
            | {
                "date": datetime(2026, 1, 2).date(),
                "raw_close": 10.0,
                "adjusted_close": 10.0,
                "adjustment_factor": 1.0,
                "limit_up_proxy": False,
                "limit_down_proxy": False,
                "limit_status_source": "price_proxy",
                "is_st": False,
                "is_suspended": False,
                "limit_up": False,
                "limit_down": False,
                "listed_days": 1,
                "is_tradable": True,
                "reason": "fixture",
                "research_eligible": True,
                "eligibility_reason": "eligible",
            }
        ]
    ).write_parquet(root / "features" / "backtest_daily_pit_20260518.parquet")
    pl.DataFrame({"symbol": ["600000.SH"], "updated_date": ["2026-04-30"]}).write_parquet(
        root / "normalized" / "tdx" / "finance_snapshot_20260518.parquet"
    )

    report = checker.build_readiness_report(root)

    assert report["can_run_price_factors"] is True
    assert report["can_run_fundamental_factors"] is False
    assert "market_cap_and_float" in report["blocking_granularities"]
    by_granularity = {item["granularity"]: item for item in report["items"]}
    assert by_granularity["price_daily"]["status"] == "ready"
    assert by_granularity["limit_up_down"]["status"] == "proxy_ready"
    assert by_granularity["financial_statements"]["status"] == "missing_pit"


def test_factor_readiness_recognizes_official_chinadata_status_layer(tmp_path):
    checker = _load_checker()
    root = tmp_path / "Ashare"
    (root / "pit").mkdir(parents=True)
    (root / "features").mkdir()
    (root / "normalized" / "tdx").mkdir(parents=True)
    pl.DataFrame([_bar("600000.SH")]).write_parquet(root / "pit" / "kline_daily_pit.parquet")
    pl.DataFrame([_bar("000001.SH")]).write_parquet(root / "pit" / "index_daily_pit.parquet")
    pl.DataFrame(
        [
            _bar("600000.SH")
            | {
                "is_st": True,
                "is_suspended": False,
                "limit_up": True,
                "limit_down": False,
                "listed_days": 200,
                "is_tradable": True,
                "reason": "st_namechange,official_up_limit_price",
                "up_limit_price": 11.0,
                "down_limit_price": 9.0,
                "limit_price_source": "chinadata.stk_limit",
                "status_source": "chinadata_official",
            }
        ]
    ).write_parquet(root / "pit" / "tradability_status_pit.parquet")
    pl.DataFrame({"symbol": ["600000.SH"], "updated_date": ["2026-04-30"]}).write_parquet(
        root / "normalized" / "tdx" / "finance_snapshot_20260518.parquet"
    )

    report = checker.build_readiness_report(root)

    by_granularity = {item["granularity"]: item for item in report["items"]}
    assert by_granularity["historical_tradability"]["status"] == "ready"
    assert by_granularity["limit_up_down"]["status"] == "ready"


def test_factor_readiness_recognizes_market_cap_and_industry_pit_layers(tmp_path):
    checker = _load_checker()
    root = tmp_path / "Ashare"
    (root / "pit").mkdir(parents=True)
    (root / "normalized" / "tdx").mkdir(parents=True)
    pl.DataFrame([_bar("600000.SH")]).write_parquet(root / "pit" / "kline_daily_pit.parquet")
    pl.DataFrame([_bar("000001.SH")]).write_parquet(root / "pit" / "index_daily_pit.parquet")
    write_polars_delta(
        pl.DataFrame(
            [
                _bar("600000.SH")
                | {
                    "is_st": False,
                    "is_suspended": False,
                    "limit_up": False,
                    "limit_down": False,
                    "listed_days": 200,
                    "is_tradable": True,
                    "reason": "",
                    "up_limit_price": 11.0,
                    "down_limit_price": 9.0,
                    "limit_price_source": "chinadata.stk_limit",
                    "status_source": "chinadata_official",
                    "trade_year": 2026,
                }
            ]
        ),
        canonical_delta_path(root / "pit" / "tradability_status_pit.parquet"),
        mode="overwrite",
        partition_by=["trade_year"],
        schema_mode="overwrite",
    )
    write_polars_delta(
        pl.DataFrame(
            [
                {
                    "symbol": "600000.SH",
                    "market": "SH",
                    "event_time": datetime(2026, 1, 2, tzinfo=UTC),
                    "available_at": datetime(2026, 1, 3, 9, 30, tzinfo=UTC),
                    "source_updated_at": datetime(2026, 1, 3, 9, 31, tzinfo=UTC),
                    "total_share": 1000.0,
                    "float_share": 800.0,
                    "free_share": 600.0,
                    "total_mv": 10000.0,
                    "circ_mv": 8000.0,
                    "trade_year": 2026,
                    "trade_month": 1,
                }
            ]
        ),
        canonical_delta_path(root / "pit" / "market_cap_daily_pit.parquet"),
        mode="overwrite",
        partition_by=["trade_year", "trade_month"],
        schema_mode="overwrite",
    )
    write_polars_delta(
        pl.DataFrame(
            [
                {
                    "symbol": "600000.SH",
                    "market": "SH",
                    "event_time": datetime(2026, 1, 2, tzinfo=UTC),
                    "available_at": datetime(2026, 1, 3, 9, 30, tzinfo=UTC),
                    "source_updated_at": datetime(2026, 1, 3, 9, 31, tzinfo=UTC),
                    "industry": "银行",
                    "area": "上海",
                    "trade_year": 2026,
                    "trade_month": 1,
                }
            ]
        ),
        canonical_delta_path(root / "pit" / "industry_daily_pit.parquet"),
        mode="overwrite",
        partition_by=["trade_year", "trade_month"],
        schema_mode="overwrite",
    )
    write_polars_delta(
        pl.DataFrame(
            [
                {
                    "symbol": "600000.SH",
                    "market": "SH",
                    "event_time": datetime(2026, 2, 2, tzinfo=UTC),
                    "available_at": datetime(2026, 1, 3, 9, 30, tzinfo=UTC),
                    "source_updated_at": datetime(2026, 1, 3, 9, 31, tzinfo=UTC),
                    "ann_date": datetime(2026, 1, 2, tzinfo=UTC),
                    "float_share": 50.0,
                    "float_ratio": 0.05,
                    "holder_name": "holder",
                    "share_type": "首发原股东限售股",
                    "ann_year": 2026,
                    "ann_month": 1,
                }
            ]
        ),
        canonical_delta_path(root / "pit" / "share_float_event_pit.parquet"),
        mode="overwrite",
        partition_by=["ann_year", "ann_month"],
        schema_mode="overwrite",
    )
    pl.DataFrame({"symbol": ["600000.SH"], "updated_date": ["2026-04-30"]}).write_parquet(
        root / "normalized" / "tdx" / "finance_snapshot_20260518.parquet"
    )

    report = checker.build_readiness_report(root)

    assert report["can_run_market_cap_neutral_factors"] is True
    assert report["can_run_industry_neutral_factors"] is True
    by_granularity = {item["granularity"]: item for item in report["items"]}
    assert by_granularity["market_cap_and_float"]["status"] == "ready"
    assert by_granularity["industry_and_blocks"]["status"] == "ready"
    assert by_granularity["share_float_events"]["status"] == "ready"
