"""Contract tests for normalized TDX snapshot datasets."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl


def _load_normalizer():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "normalize-ashare-tdx-snapshots.py"
    spec = importlib.util.spec_from_file_location("normalize_ashare_tdx_snapshots", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tdx_finance_snapshot_normalizes_calibrated_fields_without_pit_promotion(tmp_path):
    normalizer = _load_normalizer()
    finance_dir = tmp_path / "raw" / "tdx_finance_fixture"
    item_dir = finance_dir / "items"
    item_dir.mkdir(parents=True)
    (item_dir / "600000.SH.json").write_text(
        json.dumps(
            {
                "dataset": "tdx.finance_snapshot",
                "symbol": "600000.SH",
                "code": "600000",
                "market": "SH",
                "is_tradable": True,
                "downloaded_at": "2026-05-18T08:00:00+00:00",
                "tdx_payload": {
                    "complete": True,
                    "calibrated": True,
                    "raw_safe": True,
                    "count": 1,
                    "market": "sh",
                    "calibrated_data": {
                        "updated_date": {"iso": "2026-04-30"},
                        "ipo_date": {"iso": "1999-11-10"},
                        "industry_code": {"value": 1, "name": "bank"},
                        "zongzichan_cny": {"value": 123.5},
                    },
                },
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    result = normalizer.normalize_finance_snapshot(
        finance_dir=finance_dir,
        out_parquet=tmp_path / "normalized" / "tdx" / "finance_snapshot_fixture.parquet",
        manifest_path=tmp_path / "_manifest" / "tdx_finance_snapshot_fixture.json",
        data_root=tmp_path,
    )
    frame = pl.read_parquet(tmp_path / "normalized" / "tdx" / "finance_snapshot_fixture.parquet")

    assert result["tier"] == "normalized"
    assert result["research_eligible"] is False
    assert frame["symbol"].to_list() == ["600000.SH"]
    assert frame["updated_date"].to_list() == ["2026-04-30"]
    assert frame["industry_code"].to_list() == [1]
    assert frame["industry_code_name"].to_list() == ["bank"]
    assert frame["zongzichan_cny"].to_list() == [123.5]


def test_tdx_block_membership_maps_known_symbols_and_keeps_snapshot_tier(tmp_path):
    normalizer = _load_normalizer()
    status = tmp_path / "pit" / "tradability_status_pit.parquet"
    status.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH", "000001.SZ"]}).write_parquet(status)
    blocks_dir = tmp_path / "raw" / "tdx_blocks_fixture"
    blocks_dir.mkdir(parents=True)
    (blocks_dir / "block_gn.dat.json").write_text(
        json.dumps(
            {
                "dataset": "tdx.block_info",
                "block_file": "block_gn.dat",
                "downloaded_at": "2026-05-18T08:00:00+00:00",
                "tdx_payload": {
                    "entries": [
                        {"block_type": 2, "blockname": "bank", "code": "600000", "code_index": 0},
                        {"block_type": 2, "blockname": "bank", "code": "920001", "code_index": 1},
                    ]
                },
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    result = normalizer.normalize_block_membership(
        blocks_dir=blocks_dir,
        out_parquet=tmp_path / "normalized" / "tdx" / "block_membership_fixture.parquet",
        manifest_path=tmp_path / "_manifest" / "tdx_block_membership_fixture.json",
        data_root=tmp_path,
    )
    frame = pl.read_parquet(tmp_path / "normalized" / "tdx" / "block_membership_fixture.parquet")

    assert result["tier"] == "normalized"
    assert result["research_eligible"] is False
    assert frame["symbol"].to_list() == ["600000.SH", "920001.BJ"]
    assert frame["is_known_ashare"].to_list() == [True, False]
    assert frame["downloaded_at"].to_list() == [datetime(2026, 5, 18, 8, tzinfo=UTC)] * 2
