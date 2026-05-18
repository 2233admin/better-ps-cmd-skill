from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

import polars as pl
from deltalake import DeltaTable, write_deltalake


def canonical_delta_path(parquet_path: Path) -> Path:
    return parquet_path.with_suffix("")


def is_delta_table(path: Path) -> bool:
    return path.is_dir() and (path / "_delta_log").exists()


def resolve_delta_or_parquet(parquet_path: Path) -> Path | None:
    delta_path = canonical_delta_path(parquet_path)
    if is_delta_table(delta_path):
        return delta_path
    if parquet_path.exists():
        return parquet_path
    return None


def read_delta_or_parquet(path: Path, *, columns: Iterable[str] | None = None) -> pl.DataFrame:
    if is_delta_table(path):
        table = DeltaTable(str(path))
        data = table.to_pyarrow_table(columns=list(columns) if columns is not None else None)
        return pl.from_arrow(data)
    if columns is None:
        return pl.read_parquet(path)
    return pl.read_parquet(path, columns=list(columns))


def scan_delta_or_parquet(path: Path) -> pl.LazyFrame:
    if is_delta_table(path):
        table = DeltaTable(str(path))
        return pl.scan_pyarrow_dataset(table.to_pyarrow_dataset())
    return pl.scan_parquet(path)


def dataset_stats(path: Path) -> dict[str, Any]:
    lazy = scan_delta_or_parquet(path)
    schema = lazy.collect_schema()
    selectors = [pl.len().alias("row_count")]
    if "symbol" in schema.names():
        selectors.append(pl.col("symbol").n_unique().alias("symbol_count"))
    if "event_time" in schema.names():
        selectors.extend([pl.col("event_time").min().alias("start"), pl.col("event_time").max().alias("end")])
    stats = lazy.select(selectors).collect().row(0, named=True)
    payload = {key: str(value) if key in {"start", "end"} else int(value) for key, value in stats.items()}
    if is_delta_table(path):
        payload["content_hash"] = delta_log_hash(path)
        payload["storage_format"] = "delta"
        payload["delta_version"] = DeltaTable(str(path)).version()
    else:
        payload["content_hash"] = parquet_file_hash(path)
        payload["storage_format"] = "parquet"
    return payload


def parquet_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def delta_log_hash(path: Path) -> str:
    log_dir = path / "_delta_log"
    digest = hashlib.sha256()
    for item in sorted(log_dir.rglob("*")):
        if not item.is_file():
            continue
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(str(item.stat().st_size).encode("ascii"))
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def write_polars_delta(
    frame: pl.DataFrame,
    path: Path,
    *,
    mode: str = "overwrite",
    partition_by: list[str] | None = None,
    schema_mode: str | None = None,
    predicate: str | None = None,
    target_file_size: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(
        str(path),
        frame.to_arrow(),
        mode=mode,
        partition_by=partition_by,
        schema_mode=schema_mode,
        predicate=predicate,
        target_file_size=target_file_size,
    )


def merge_polars_delta(
    frame: pl.DataFrame,
    path: Path,
    *,
    keys: list[str],
) -> None:
    if frame.is_empty():
        return
    table = DeltaTable(str(path))
    predicate = " AND ".join([f"target.{key} = source.{key}" for key in keys])
    (
        table.merge(
            frame.to_arrow(),
            predicate=predicate,
            source_alias="source",
            target_alias="target",
        )
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
