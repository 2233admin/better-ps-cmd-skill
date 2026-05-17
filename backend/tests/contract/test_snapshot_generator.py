"""Contract tests for immutable dataset snapshot generation."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import polars as pl
import pytest

SNAPSHOT_TEST_PARENT = Path(__file__).resolve().parents[1]


def _pit_frame(close: float = 10.5) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2024, 1, 31, tzinfo=UTC)],
            "available_at": [datetime(2024, 2, 1, tzinfo=UTC)],
            "source_updated_at": [datetime(2024, 2, 1, 1, tzinfo=UTC)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.9],
            "close": [close],
            "volume": [1000],
            "amount": [10500.0],
        }
    )


def _write_parquet(frame: pl.DataFrame, path: Path) -> None:
    if path.exists():
        path.unlink()
    frame.write_parquet(path)


def test_parquet_snapshot_has_stable_schema_and_data_hash():
    from app.research.ashare_data_contract import ASharePITDataset
    from app.research.models import Frequency, Market
    from app.research.snapshot import DatasetSnapshotGenerator

    with TemporaryDirectory(dir=SNAPSHOT_TEST_PARENT) as directory:
        path = Path(directory) / "daily.parquet"
        _write_parquet(_pit_frame(), path)

        generator = DatasetSnapshotGenerator()
        first = generator.from_parquet(
            path,
            dataset=ASharePITDataset.KLINE_DAILY,
            market=Market.ASHARE,
            frequency=Frequency.DAILY,
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            version="2024-02-01",
            source="fixture",
        )
        second = generator.from_parquet(
            path,
            dataset=ASharePITDataset.KLINE_DAILY,
            market=Market.ASHARE,
            frequency=Frequency.DAILY,
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            version="2024-02-01",
            source="fixture",
        )

    assert first.version.schema_hash == second.version.schema_hash
    assert first.version.data_hash == second.version.data_hash
    assert first.snapshot.artifact.content_hash == first.version.data_hash
    assert first.snapshot.row_count == 1


def test_snapshot_schema_and_data_changes_are_reflected():
    from app.research.ashare_data_contract import ASharePITDataset
    from app.research.models import Frequency, Market
    from app.research.snapshot import DatasetSnapshotGenerator

    with TemporaryDirectory(dir=SNAPSHOT_TEST_PARENT) as directory:
        base_path = Path(directory) / "base.parquet"
        schema_path = Path(directory) / "schema.parquet"
        data_path = Path(directory) / "data.parquet"
        _write_parquet(_pit_frame(), base_path)
        _write_parquet(_pit_frame().with_columns(pl.lit("tdx").alias("vendor")), schema_path)
        _write_parquet(_pit_frame(close=10.8), data_path)

        generator = DatasetSnapshotGenerator()
        kwargs = {
            "dataset": ASharePITDataset.KLINE_DAILY,
            "market": Market.ASHARE,
            "frequency": Frequency.DAILY,
            "as_of": datetime(2024, 2, 1, tzinfo=UTC),
            "version": "2024-02-01",
            "source": "fixture",
        }

        base = generator.from_parquet(base_path, **kwargs)
        schema_changed = generator.from_parquet(schema_path, **kwargs)
        data_changed = generator.from_parquet(data_path, **kwargs)

    assert schema_changed.version.schema_hash != base.version.schema_hash
    assert data_changed.version.data_hash != base.version.data_hash


def test_snapshot_rejects_missing_available_at():
    from app.research.ashare_data_contract import ASharePITDataset
    from app.research.models import Frequency, Market
    from app.research.snapshot import DatasetSnapshotGenerator

    with TemporaryDirectory(dir=SNAPSHOT_TEST_PARENT) as directory:
        path = Path(directory) / "unsafe.parquet"
        _write_parquet(_pit_frame().drop("available_at"), path)

        with pytest.raises(ValueError, match="available_at"):
            DatasetSnapshotGenerator().from_parquet(
                path,
                dataset=ASharePITDataset.KLINE_DAILY,
                market=Market.ASHARE,
                frequency=Frequency.DAILY,
                as_of=datetime(2024, 2, 1, tzinfo=UTC),
                version="2024-02-01",
                source="fixture",
            )


def test_duckdb_snapshot_uses_same_contract_and_hashes():
    from app.research.ashare_data_contract import ASharePITDataset
    from app.research.models import Frequency, Market
    from app.research.snapshot import DatasetSnapshotGenerator

    with TemporaryDirectory(dir=SNAPSHOT_TEST_PARENT) as directory:
        db_path = Path(directory) / "fixture.duckdb"
        connection = duckdb.connect(str(db_path))
        try:
            connection.execute(
                """
                CREATE TABLE kline_daily_pit (
                    symbol VARCHAR,
                    market VARCHAR,
                    event_time TIMESTAMP,
                    available_at TIMESTAMP,
                    source_updated_at TIMESTAMP,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    amount DOUBLE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO kline_daily_pit VALUES (
                    '600000.SH',
                    'SH',
                    TIMESTAMP '2024-01-31 00:00:00',
                    TIMESTAMP '2024-02-01 00:00:00',
                    TIMESTAMP '2024-02-01 01:00:00',
                    10.0,
                    11.0,
                    9.9,
                    10.5,
                    1000,
                    10500.0
                )
                """
            )
        finally:
            connection.close()

        result = DatasetSnapshotGenerator().from_duckdb(
            db_path,
            table="kline_daily_pit",
            dataset=ASharePITDataset.KLINE_DAILY,
            market=Market.ASHARE,
            frequency=Frequency.DAILY,
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            version="2024-02-01",
            source="fixture",
        )

    assert result.snapshot.row_count == 1
    assert len(result.version.schema_hash) == 64
    assert len(result.version.data_hash) == 64
