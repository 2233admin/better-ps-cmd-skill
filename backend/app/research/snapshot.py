"""Dataset snapshot generation for immutable PIT research inputs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from .ashare_data_contract import ASharePITDataset, validate_pit_columns
from .manifest import (
    ArtifactKind,
    DataArtifact,
    DatasetSnapshot,
    DatasetVersion,
    canonical_json,
    sha256_json,
)
from .models import Frequency, Market


@dataclass(frozen=True)
class SnapshotBuildResult:
    snapshot: DatasetSnapshot
    version: DatasetVersion


def _normalize_cell(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _stable_rows_payload(frame: pl.DataFrame) -> list[dict[str, Any]]:
    columns = sorted(frame.columns)
    if not columns:
        return []
    stable = frame.select(columns).sort(columns)
    return [
        {column: _normalize_cell(value) for column, value in row.items()}
        for row in stable.to_dicts()
    ]


def schema_hash(frame: pl.DataFrame) -> str:
    return sha256_json(
        [
            {"name": name, "dtype": str(dtype)}
            for name, dtype in zip(frame.columns, frame.dtypes, strict=True)
        ]
    )


def data_hash(frame: pl.DataFrame) -> str:
    return sha256_json(_stable_rows_payload(frame))


class DatasetSnapshotGenerator:
    """Build immutable snapshot/version records from DuckDB tables or Parquet files."""

    def from_parquet(
        self,
        path: str | Path,
        *,
        dataset: ASharePITDataset,
        market: Market,
        frequency: Frequency,
        as_of: datetime,
        version: str,
        source: str,
    ) -> SnapshotBuildResult:
        source_path = Path(path)
        frame = pl.read_parquet(source_path)
        return self._build(
            frame,
            dataset=dataset,
            market=market,
            frequency=frequency,
            as_of=as_of,
            version=version,
            source=source,
            artifact_uri=f"parquet://{source_path.as_posix()}",
        )

    def from_duckdb(
        self,
        db_path: str | Path,
        *,
        table: str,
        dataset: ASharePITDataset,
        market: Market,
        frequency: Frequency,
        as_of: datetime,
        version: str,
        source: str,
    ) -> SnapshotBuildResult:
        connection = duckdb.connect(str(db_path), read_only=True)
        try:
            columns = connection.execute(f"DESCRIBE {table}").fetchall()
            column_names = [row[0] for row in columns]
            quoted = ", ".join(f'"{column}"' for column in column_names)
            order_by = ", ".join(f'"{column}"' for column in sorted(column_names))
            cursor = connection.execute(
                f'SELECT {quoted} FROM "{table}" ORDER BY {order_by}'
            )
            rows = cursor.fetchall()
            frame = pl.DataFrame(rows, schema=column_names, orient="row")
        finally:
            connection.close()
        return self._build(
            frame,
            dataset=dataset,
            market=market,
            frequency=frequency,
            as_of=as_of,
            version=version,
            source=source,
            artifact_uri=f"duckdb://{Path(db_path).as_posix()}#{table}",
        )

    def write_json(
        self,
        result: SnapshotBuildResult,
        output_dir: str | Path,
    ) -> tuple[Path, Path]:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        snapshot_path = path / f"{result.snapshot.snapshot_id}.snapshot.json"
        version_path = path / f"{result.version.version}.version.json"
        snapshot_path.write_text(
            json.dumps(asdict(result.snapshot), default=str, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        version_path.write_text(
            json.dumps(asdict(result.version), default=str, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return snapshot_path, version_path

    def _build(
        self,
        frame: pl.DataFrame,
        *,
        dataset: ASharePITDataset,
        market: Market,
        frequency: Frequency,
        as_of: datetime,
        version: str,
        source: str,
        artifact_uri: str,
    ) -> SnapshotBuildResult:
        contract = validate_pit_columns(dataset, frame.columns)
        if not contract.passed:
            missing = ", ".join(contract.missing_columns)
            raise ValueError(f"{dataset.value} is missing PIT columns: {missing}")

        schema_digest = schema_hash(frame)
        data_digest = data_hash(frame)
        artifact = DataArtifact(
            kind=ArtifactKind.PIT_TABLE,
            uri=artifact_uri,
            content_hash=data_digest,
            metadata={
                "schema_hash": schema_digest,
                "row_count": frame.height,
                "dataset": dataset.value,
            },
        )
        snapshot = DatasetSnapshot(
            name=dataset.value,
            market=market,
            frequency=frequency,
            tier="pit",
            artifact=artifact,
            row_count=frame.height,
            source=source,
        )
        dataset_version = DatasetVersion(
            name=dataset.value,
            market=market,
            frequency=frequency,
            snapshot_id=snapshot.snapshot_id,
            schema_hash=schema_digest,
            data_hash=data_digest,
            as_of=as_of,
            version=version,
        )
        canonical_json(dataset_version)
        return SnapshotBuildResult(snapshot=snapshot, version=dataset_version)
