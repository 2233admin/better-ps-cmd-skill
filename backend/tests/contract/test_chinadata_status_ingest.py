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
    path = root / "scripts" / "ingest-ashare-chinadata-status.py"
    spec = importlib.util.spec_from_file_location("ingest_ashare_chinadata_status", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chinadata_status_producer_builds_official_tradability_rows(tmp_path):
    from app.research.ashare_data_contract import ASharePITDataset, validate_pit_columns

    module = _load_module()
    root = tmp_path / "Ashare"
    pit = root / "pit"
    pit.mkdir(parents=True)

    base_day = date(2026, 5, 12)
    rows = []
    closes = [10.0, 11.0, 9.0]
    for idx, close in enumerate(closes):
        current = base_day + timedelta(days=idx)
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        rows.append(
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": event_time,
                "available_at": event_time + timedelta(days=1, hours=9, minutes=30),
                "source_updated_at": event_time + timedelta(days=1, hours=9, minutes=31),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000 + idx,
                "amount": close * (1000 + idx),
            }
        )
    pl.DataFrame(rows).write_parquet(pit / "kline_daily_pit.parquet")

    result = module.build_official_tradability_status(
        data_root=root,
        namechange_frame=pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "name": "ST浦发",
                    "start_date": "20260513",
                    "end_date": "20260514",
                    "ann_date": "20260512",
                    "change_reason": "ST",
                }
            ]
        ),
        suspend_frame=pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260514",
                    "suspend_timing": None,
                    "suspend_type": "S",
                }
            ]
        ),
        stk_limit_frame=pd.DataFrame(
            [
                {"ts_code": "600000.SH", "trade_date": "20260513", "up_limit": 11.0, "down_limit": 9.0},
                {"ts_code": "600000.SH", "trade_date": "20260514", "up_limit": 11.0, "down_limit": 9.0},
            ]
        ),
    )

    frame = read_delta_or_parquet(canonical_delta_path(root / "pit" / "tradability_status_pit.parquet"))
    check = validate_pit_columns(ASharePITDataset.TRADABILITY_STATUS, frame.columns)
    assert check.passed, f"missing required columns: {check.missing_columns}"
    assert {"status_source", "up_limit_price", "down_limit_price"}.issubset(set(frame.columns))
    assert result["row_count"] == 3

    by_date = {row["event_time"].date(): row for row in frame.iter_rows(named=True)}
    day1 = by_date[date(2026, 5, 12)]
    day2 = by_date[date(2026, 5, 13)]
    day3 = by_date[date(2026, 5, 14)]

    assert day1["is_st"] is False
    assert day2["is_st"] is True
    assert day2["limit_up"] is True
    assert "official_up_limit_price" in day2["reason"]
    assert day3["is_suspended"] is True
    assert day3["limit_down"] is True
    assert day3["is_tradable"] is False
    assert "suspend_d" in day3["reason"]
    assert day3["status_source"] == "chinadata_official"
    assert day3["listed_days"] == 3

    universe = json.loads((root / "_manifest" / "tradability_universe.json").read_text(encoding="utf-8"))
    assert universe["mode"] == "official_daily_status_aligned_to_kline_daily_pit"
    assert universe["official_counts"]["st_rows"] == 2
    assert universe["official_counts"]["suspended_rows"] == 1
