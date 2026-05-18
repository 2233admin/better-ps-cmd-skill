from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl


def _load_exporter():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "export-ashare-index-duckdb-to-lake.py"
    spec = importlib.util.spec_from_file_location("export_ashare_index_duckdb_to_lake", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_index_export_writes_pit_daily_benchmark_dataset(tmp_path):
    from app.research.ashare_data_contract import ASharePITDataset, validate_pit_columns

    exporter = _load_exporter()
    db_path = tmp_path / "Aquant.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE tdx_daily (
            symbol VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            amount DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tdx_daily VALUES
        ('sh000001', '2026-01-02', 3000.0, 3010.0, 2990.0, 3005.0, 100, 1000.0),
        ('sz399001', '2026-01-02', 10000.0, 10100.0, 9900.0, 10050.0, 200, 2000.0),
        ('sh600000', '2026-01-02', 10.0, 10.1, 9.9, 10.0, 300, 3000.0)
        """
    )
    conn.close()

    result = exporter.export_index_daily(
        db_path=db_path,
        out_root=tmp_path,
        symbols=("sh000001", "sz399001"),
    )
    frame = pl.read_parquet(tmp_path / "pit" / "index_daily_pit.parquet")

    assert result["dataset"] == "ashare.index_daily_pit"
    assert frame["symbol"].to_list() == ["000001.SH", "399001.SZ"]
    assert validate_pit_columns(ASharePITDataset.INDEX_DAILY, frame.columns).passed
    assert frame["available_at"].to_list()[0] == datetime(2026, 1, 3, 9, 30, tzinfo=UTC)
    assert result["row_count"] == 2
