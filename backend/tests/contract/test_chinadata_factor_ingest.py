from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl

from app.data.delta_lake import canonical_delta_path, read_delta_or_parquet


def _load_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "ingest-ashare-chinadata-factors.py"
    spec = importlib.util.spec_from_file_location("ingest_ashare_chinadata_factors", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _kline_rows(symbol: str = "600000.SH") -> list[dict]:
    rows = []
    for idx in range(2):
        current = date(2026, 1, 2) + timedelta(days=idx)
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        rows.append(
            {
                "symbol": symbol,
                "market": "SH",
                "event_time": event_time,
                "available_at": event_time + timedelta(days=1, hours=9, minutes=30),
                "source_updated_at": event_time + timedelta(days=1, hours=9, minutes=31),
                "open": 10.0 + idx,
                "high": 10.1 + idx,
                "low": 9.9 + idx,
                "close": 10.0 + idx,
                "volume": 1000 + idx,
                "amount": 10000.0 + idx,
            }
        )
    return rows


def test_chinadata_factor_producer_builds_market_cap_industry_and_share_float_pit(tmp_path):
    from app.research.ashare_data_contract import (
        ASharePITDataset,
        validate_pit_columns,
    )

    module = _load_module()
    root = tmp_path / "Ashare"
    pit = root / "pit"
    pit.mkdir(parents=True)
    pl.DataFrame(_kline_rows()).write_parquet(pit / "kline_daily_pit.parquet")

    original_fetch = module.fetch_tushare_dataframe

    def fake_fetch(api_name: str, params=None, fields="", retries=2, token=None):
        if api_name == "daily_basic":
            rows = {
                "20260102": {
                    "ts_code": "600000.SH",
                    "trade_date": "20260102",
                    "turnover_rate": 1.1,
                    "turnover_rate_f": 1.3,
                    "volume_ratio": 0.9,
                    "pe": 5.0,
                    "pe_ttm": 5.1,
                    "pb": 0.8,
                    "ps": 1.0,
                    "ps_ttm": 1.1,
                    "dv_ratio": 2.0,
                    "dv_ttm": 2.1,
                    "total_share": 1000.0,
                    "float_share": 800.0,
                    "free_share": 700.0,
                    "total_mv": 10000.0,
                    "circ_mv": 8000.0,
                },
                "20260103": {
                    "ts_code": "600000.SH",
                    "trade_date": "20260103",
                    "turnover_rate": 1.2,
                    "turnover_rate_f": 1.4,
                    "volume_ratio": 1.0,
                    "pe": 5.2,
                    "pe_ttm": 5.3,
                    "pb": 0.81,
                    "ps": 1.01,
                    "ps_ttm": 1.11,
                    "dv_ratio": 2.2,
                    "dv_ttm": 2.3,
                    "total_share": 1000.0,
                    "float_share": 820.0,
                    "free_share": 710.0,
                    "total_mv": 10100.0,
                    "circ_mv": 8200.0,
                },
            }
            trade_date = (params or {}).get("trade_date")
            return pd.DataFrame([rows[trade_date]]) if trade_date in rows else pd.DataFrame()
        if api_name == "bak_daily":
            rows = {
                "20260102": {
                    "ts_code": "600000.SH",
                    "trade_date": "20260102",
                    "name": "浦发银行",
                    "industry": "银行",
                    "area": "上海",
                },
                "20260103": {
                    "ts_code": "600000.SH",
                    "trade_date": "20260103",
                    "name": "浦发银行",
                    "industry": "银行",
                    "area": "上海",
                },
            }
            trade_date = (params or {}).get("trade_date")
            return pd.DataFrame([rows[trade_date]]) if trade_date in rows else pd.DataFrame()
        if api_name == "share_float":
            return pd.DataFrame(
                [
                    {
                        "ts_code": "600000.SH",
                        "ann_date": "20251231",
                        "float_date": "20260103",
                        "float_share": 50.0,
                        "float_ratio": 5.0,
                        "holder_name": "holder",
                        "share_type": "首发原股东限售股",
                    }
                ]
            )
        return original_fetch(api_name, params=params, fields=fields, retries=retries, token=token)

    module.fetch_tushare_dataframe = fake_fetch
    try:
        result = module.build_chinadata_factor_pit(
            data_root=root,
            start=date(2026, 1, 2),
            end=date(2026, 1, 3),
            pause_seconds=0.0,
        )
    finally:
        module.fetch_tushare_dataframe = original_fetch

    market_cap = read_delta_or_parquet(canonical_delta_path(root / "pit" / "market_cap_daily_pit.parquet"))
    industry = read_delta_or_parquet(canonical_delta_path(root / "pit" / "industry_daily_pit.parquet"))
    share_float = read_delta_or_parquet(canonical_delta_path(root / "pit" / "share_float_event_pit.parquet"))

    assert validate_pit_columns(ASharePITDataset.MARKET_CAP_DAILY, market_cap.columns).passed
    assert validate_pit_columns(ASharePITDataset.INDUSTRY_DAILY, industry.columns).passed
    assert validate_pit_columns(ASharePITDataset.SHARE_FLOAT_EVENT, share_float.columns).passed
    assert market_cap.height == 2
    assert industry.height == 2
    assert share_float.height == 1
    assert market_cap.row(0, named=True)["free_share"] == 700.0
    assert industry.row(0, named=True)["industry"] == "银行"
    assert share_float.row(0, named=True)["holder_name"] == "holder"
    assert result["manifests"]["market_cap_daily"]["dataset"] == "ashare.market_cap_daily_pit"
    assert result["manifests"]["industry_daily"]["dataset"] == "ashare.industry_daily_pit"
    assert result["manifests"]["share_float_event"]["dataset"] == "ashare.share_float_event_pit"

    manifest = json.loads((root / "_manifest" / "chinadata_factor_sidecars.json").read_text(encoding="utf-8"))
    assert set(manifest["manifests"]) == {"market_cap_daily", "industry_daily", "share_float_event"}


def test_chinadata_factor_producer_only_fetches_missing_trade_dates_for_v2_sidecars(tmp_path):
    module = _load_module()
    root = tmp_path / "Ashare"
    pit = root / "pit"
    manifest_root = root / "_manifest"
    pit.mkdir(parents=True)
    manifest_root.mkdir(parents=True)
    pl.DataFrame(_kline_rows()).write_parquet(pit / "kline_daily_pit.parquet")

    existing = pl.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": datetime(2026, 1, 2, tzinfo=UTC),
                "available_at": datetime(2026, 1, 3, 9, 30, tzinfo=UTC),
                "source_updated_at": datetime(2026, 1, 3, 9, 31, tzinfo=UTC),
                "trade_year": 2026,
                "trade_month": 1,
                "turnover_rate": 1.1,
                "turnover_rate_f": 1.3,
                "volume_ratio": 0.9,
                "pe": 5.0,
                "pe_ttm": 5.1,
                "pb": 0.8,
                "ps": 1.0,
                "ps_ttm": 1.1,
                "dv_ratio": 2.0,
                "dv_ttm": 2.1,
                "total_share": 1000.0,
                "float_share": 800.0,
                "free_share": 700.0,
                "total_mv": 10000.0,
                "circ_mv": 8000.0,
            }
        ]
    )
    from app.data.delta_lake import write_polars_delta

    write_polars_delta(
        existing,
        canonical_delta_path(root / "pit" / "market_cap_daily_pit.parquet"),
        mode="overwrite",
        partition_by=["trade_year", "trade_month"],
        schema_mode="overwrite",
    )
    (manifest_root / "market_cap_daily_pit.json").write_text(
        json.dumps({"dataset": "ashare.market_cap_daily_pit", "sync_strategy_version": 2}, ensure_ascii=True),
        encoding="utf-8",
    )

    requested_trade_dates: list[str] = []
    original_fetch = module.fetch_tushare_dataframe

    def fake_fetch(api_name: str, params=None, fields="", retries=2, token=None):
        if api_name != "daily_basic":
            return pd.DataFrame()
        requested_trade_dates.append((params or {}).get("trade_date"))
        return pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260103",
                    "turnover_rate": 1.2,
                    "turnover_rate_f": 1.4,
                    "volume_ratio": 1.0,
                    "pe": 5.2,
                    "pe_ttm": 5.3,
                    "pb": 0.81,
                    "ps": 1.01,
                    "ps_ttm": 1.11,
                    "dv_ratio": 2.2,
                    "dv_ttm": 2.3,
                    "total_share": 1000.0,
                    "float_share": 820.0,
                    "free_share": 710.0,
                    "total_mv": 10100.0,
                    "circ_mv": 8200.0,
                }
            ]
        )

    module.fetch_tushare_dataframe = fake_fetch
    try:
        result = module.build_chinadata_factor_pit(
            data_root=root,
            start=date(2026, 1, 2),
            end=date(2026, 1, 3),
            pause_seconds=0.0,
            build_industry=False,
            build_share_float=False,
        )
    finally:
        module.fetch_tushare_dataframe = original_fetch

    market_cap = read_delta_or_parquet(canonical_delta_path(root / "pit" / "market_cap_daily_pit.parquet"))
    assert requested_trade_dates == ["20260103"]
    assert market_cap.height == 2
    assert result["manifests"]["market_cap_daily"]["sync_mode"] == "incremental"
