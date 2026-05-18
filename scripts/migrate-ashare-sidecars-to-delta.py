"""Migrate existing A-share PIT sidecar parquet files into Delta tables.

This keeps price bars on parquet for now and upgrades the higher-churn sidecars
to a Delta/Parquet hybrid lake:
- tradability_status_pit
- market_cap_daily_pit
- industry_daily_pit
- share_float_event_pit
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import canonical_delta_path, dataset_stats, is_delta_table, write_polars_delta


DATASETS = {
    "tradability_status_pit.parquet": {
        "dataset": "ashare.tradability_status_pit",
        "partition_by": ["trade_year", "trade_month"],
        "time_source": "event_time",
    },
    "market_cap_daily_pit.parquet": {
        "dataset": "ashare.market_cap_daily_pit",
        "partition_by": ["trade_year", "trade_month"],
        "time_source": "event_time",
    },
    "industry_daily_pit.parquet": {
        "dataset": "ashare.industry_daily_pit",
        "partition_by": ["trade_year", "trade_month"],
        "time_source": "event_time",
    },
    "share_float_event_pit.parquet": {
        "dataset": "ashare.share_float_event_pit",
        "partition_by": ["ann_year", "ann_month"],
        "time_source": "ann_date",
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare")
    parser.add_argument("--keep-parquet-copy", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = migrate_sidecars_to_delta(
        data_root=Path(args.data_root),
        keep_parquet_copy=args.keep_parquet_copy,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"migrated {summary['completed_count']} sidecar datasets to Delta under {args.data_root}")
    return 0


def migrate_sidecars_to_delta(*, data_root: Path, keep_parquet_copy: bool = False) -> dict[str, Any]:
    pit_root = data_root / "pit"
    backup_root = data_root / "_legacy_parquet" / "pit"
    backup_root.mkdir(parents=True, exist_ok=True)
    completed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")

    for filename, config in DATASETS.items():
        parquet_path = pit_root / filename
        delta_path = canonical_delta_path(parquet_path)
        if is_delta_table(delta_path):
            skipped.append(
                {
                    "dataset": config["dataset"],
                    "status": "skipped",
                    "reason": f"delta table already exists at {delta_path}",
                    "path": _relative(delta_path, data_root),
                }
            )
            continue
        if not parquet_path.exists():
            skipped.append(
                {
                    "dataset": config["dataset"],
                    "status": "skipped",
                    "reason": f"source parquet not found: {parquet_path}",
                    "path": _relative(parquet_path, data_root),
                }
            )
            continue
        frame = pl.read_parquet(parquet_path)
        frame = _ensure_partition_columns(frame, time_source=config["time_source"], partition_by=config["partition_by"])
        write_polars_delta(
            frame,
            delta_path,
            mode="overwrite",
            partition_by=list(config["partition_by"]),
            schema_mode="overwrite",
        )
        backup_path = backup_root / f"{filename}.{timestamp}"
        if keep_parquet_copy:
            shutil.copy2(parquet_path, backup_path)
        else:
            shutil.move(str(parquet_path), str(backup_path))
        completed.append(
            {
                "dataset": config["dataset"],
                "status": "completed",
                "path": _relative(delta_path, data_root),
                "backup_path": _relative(backup_path, data_root),
                **dataset_stats(delta_path),
            }
        )

    manifest = {
        "dataset": "ashare.sidecar_delta_migration_v1",
        "migrated_at": datetime.now(tz=UTC).isoformat(),
        "completed_count": len(completed),
        "skipped_count": len(skipped),
        "completed": completed,
        "skipped": skipped,
    }
    manifest_path = data_root / "_manifest" / "sidecar_delta_migration.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _ensure_partition_columns(frame: pl.DataFrame, *, time_source: str, partition_by: list[str]) -> pl.DataFrame:
    if all(column in frame.columns for column in partition_by):
        return frame
    if time_source not in frame.columns:
        raise SystemExit(f"missing time source column {time_source!r} required for partition columns {partition_by}")
    expressions = []
    if "trade_year" in partition_by and "trade_year" not in frame.columns:
        expressions.append(pl.col(time_source).dt.year().alias("trade_year"))
    if "trade_month" in partition_by and "trade_month" not in frame.columns:
        expressions.append(pl.col(time_source).dt.month().alias("trade_month"))
    if "ann_year" in partition_by and "ann_year" not in frame.columns:
        expressions.append(pl.col(time_source).dt.year().alias("ann_year"))
    if "ann_month" in partition_by and "ann_month" not in frame.columns:
        expressions.append(pl.col(time_source).dt.month().alias("ann_month"))
    return frame.with_columns(expressions) if expressions else frame


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
