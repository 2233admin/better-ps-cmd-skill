"""Contract tests for raw TDX download helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl


def _load_downloader():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "download-ashare-tdx-raw.py"
    spec = importlib.util.spec_from_file_location("download_ashare_tdx_raw", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tdx_raw_downloader_all_scope_includes_non_tradable_symbols(tmp_path):
    downloader = _load_downloader()
    status = tmp_path / "pit" / "tradability_status_pit.parquet"
    status.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000.SH", "000003.SZ"],
            "market": ["SH", "SZ"],
            "is_tradable": [True, False],
        }
    ).write_parquet(status)

    all_symbols = downloader._load_symbols(
        data_root=tmp_path,
        symbols_file=None,
        scope="all",
        limit=None,
    )
    tradable_symbols = downloader._load_symbols(
        data_root=tmp_path,
        symbols_file=None,
        scope="tradable",
        limit=None,
    )

    assert [item.symbol for item in all_symbols] == ["000003.SZ", "600000.SH"]
    assert [item.symbol for item in tradable_symbols] == ["600000.SH"]


def test_tdx_raw_downloader_writes_raw_wrappers_and_indexes(tmp_path, monkeypatch):
    downloader = _load_downloader()

    def fake_run_json(command, timeout_seconds):
        assert timeout_seconds == 9
        if "block-info" in command:
            return {"ok": True, "returncode": 0, "payload": {"entries": [{"code": "600000"}]}}
        return {
            "ok": True,
            "returncode": 0,
            "payload": {
                "code": command[2],
                "calibrated": True,
                "complete": True,
                "calibrated_data": {"updated_date": {"iso": "2026-04-30"}},
            },
        }

    monkeypatch.setattr(downloader, "_run_json", fake_run_json)
    downloaded_at = datetime(2026, 5, 18, tzinfo=UTC)
    symbol = downloader.SymbolItem("600000.SH", "600000", "SH", True)

    finance = downloader.download_finance(
        tdx_cli=Path("tdx-cli"),
        raw_root=tmp_path,
        run_id="fixture",
        symbols=[symbol],
        workers=1,
        timeout_seconds=9,
        downloaded_at=downloaded_at,
        force=False,
    )
    blocks = downloader.download_blocks(
        tdx_cli=Path("tdx-cli"),
        raw_root=tmp_path,
        run_id="fixture",
        block_files=("block_gn.dat",),
        timeout_seconds=9,
        downloaded_at=downloaded_at,
        force=False,
    )

    assert finance["requested"] == 1
    assert finance["success"] == 1
    assert finance["failed"] == 0
    assert Path(finance["index_path"]).exists()
    finance_payload = json.loads(
        (tmp_path / "tdx_finance_fixture" / "items" / "600000.SH.json").read_text(
            encoding="utf-8"
        )
    )
    assert finance_payload["dataset"] == "tdx.finance_snapshot"
    assert finance_payload["tdx_payload"]["calibrated_data"]["updated_date"]["iso"] == "2026-04-30"

    assert blocks["success"] == 1
    assert blocks["items"][0]["item_count"] == 1
    block_payload = json.loads(
        (tmp_path / "tdx_blocks_fixture" / "block_gn.dat.json").read_text(encoding="utf-8")
    )
    assert block_payload["dataset"] == "tdx.block_info"
    assert block_payload["tdx_payload"]["entries"][0]["code"] == "600000"
