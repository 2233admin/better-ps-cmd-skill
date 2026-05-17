"""Build a PIT-safe A-share world snapshot from kline lake inputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from .data_lake import resolve_kline_daily_parquet, resolve_tradability_status_parquet


@dataclass(frozen=True)
class WorldSnapshotResult:
    snapshot_id: str
    out_dir: Path
    parquet_path: Path
    manifest_path: Path
    frame: pl.DataFrame


def build_world_snapshot(
    *,
    data_root: Path,
    as_of_date: date,
    out_dir: Path,
    symbols: tuple[str, ...] = (),
) -> WorldSnapshotResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = resolve_kline_daily_parquet(data_root, symbols)
    frame = _normalize_input(pl.read_parquet(source_path), symbols)
    as_of = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    visible = frame.filter(pl.col("available_at") <= as_of)
    snapshot = _latest_rows(visible, as_of=as_of)
    status_path = resolve_tradability_status_parquet(data_root)
    if status_path is not None:
        status = _latest_status_rows(pl.read_parquet(status_path), as_of=as_of, symbols=symbols)
        snapshot = _merge_tradability_status(snapshot, status)
    snapshot_id = f"ashare.world_snapshot_v1:{as_of_date.isoformat()}"
    parquet_path = out_dir / "world_snapshot.parquet"
    snapshot.write_parquet(parquet_path)
    manifest = {
        "dataset": "ashare.world_snapshot_v1",
        "snapshot_id": snapshot_id,
        "as_of": as_of.isoformat(),
        "source": str(source_path),
        "tradability_status_source": str(status_path) if status_path is not None else "",
        "rows": snapshot.height,
        "symbols": sorted(snapshot["symbol"].to_list()) if snapshot.height else [],
        "parquet": parquet_path.name,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return WorldSnapshotResult(snapshot_id, out_dir, parquet_path, manifest_path, snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Snapshot date in YYYY-MM-DD format")
    parser.add_argument("--data-root", required=True, help="A-share lake root")
    parser.add_argument("--out", required=True, help="Snapshot output directory")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    args = parser.parse_args(argv)

    build_world_snapshot(
        data_root=Path(args.data_root),
        as_of_date=date.fromisoformat(args.date),
        out_dir=Path(args.out),
        symbols=tuple(symbol.strip() for symbol in (args.symbols or "").split(",") if symbol.strip()),
    )
    return 0


def _normalize_input(frame: pl.DataFrame, symbols: tuple[str, ...]) -> pl.DataFrame:
    columns = set(frame.columns)
    if {"symbol", "event_time", "available_at", "source_updated_at"}.issubset(columns):
        normalized = frame
    elif {"code", "market", "date", "open", "high", "low", "close", "volume", "amount"}.issubset(columns):
        normalized = _normalize_legacy(frame)
    else:
        raise ValueError("world snapshot requires PIT kline or legacy daily kline columns")
    if symbols:
        normalized = normalized.filter(pl.col("symbol").is_in(list(symbols)))
    return normalized.sort(["symbol", "event_time"])


def _normalize_legacy(frame: pl.DataFrame) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        current = _date_value(row["date"])
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        available_at = event_time.replace(hour=23, minute=59, second=59)
        market = "SH" if str(row["market"]).lower() in {"1", "sh"} else "SZ"
        rows.append(
            {
                "symbol": f"{str(row['code']).zfill(6)}.{market}",
                "market": market,
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": available_at,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "amount": float(row["amount"]),
            }
        )
    return pl.DataFrame(rows)


def _latest_rows(frame: pl.DataFrame, *, as_of: datetime) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8,
        "market": pl.Utf8,
        "as_of": pl.Datetime(time_zone="UTC"),
        "latest_event_time": pl.Datetime(time_zone="UTC"),
        "available_at": pl.Datetime(time_zone="UTC"),
        "source_updated_at": pl.Datetime(time_zone="UTC"),
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "amount": pl.Float64,
        "visible_rows": pl.UInt32,
        "is_st": pl.Boolean,
        "is_suspended": pl.Boolean,
        "limit_up": pl.Boolean,
        "limit_down": pl.Boolean,
        "listed_days": pl.Int64,
        "is_tradable": pl.Boolean,
        "research_eligible": pl.Boolean,
        "tradable_reason": pl.Utf8,
        "reason": pl.Utf8,
    }
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    counts = frame.group_by("symbol").len().rename({"len": "visible_rows"})
    latest = frame.sort(["symbol", "event_time"]).group_by("symbol", maintain_order=True).last()
    return (
        latest.join(counts, on="symbol", how="left")
        .with_columns(
            pl.lit(as_of).alias("as_of"),
            pl.col("event_time").alias("latest_event_time"),
            pl.col("is_st").fill_null(False) if "is_st" in latest.columns else pl.lit(False).alias("is_st"),
            pl.col("is_suspended").fill_null(False)
            if "is_suspended" in latest.columns
            else pl.lit(False).alias("is_suspended"),
            pl.col("limit_up").fill_null(False) if "limit_up" in latest.columns else pl.lit(False).alias("limit_up"),
            pl.col("limit_down").fill_null(False)
            if "limit_down" in latest.columns
            else pl.lit(False).alias("limit_down"),
            pl.col("listed_days").fill_null(-1).cast(pl.Int64)
            if "listed_days" in latest.columns
            else pl.lit(-1).alias("listed_days"),
            pl.col("is_tradable").fill_null(True)
            if "is_tradable" in latest.columns
            else pl.lit(True).alias("is_tradable"),
            pl.when(pl.col("is_tradable").fill_null(True))
            .then(pl.lit("visible PIT kline row"))
            .otherwise(pl.col("reason").fill_null("not tradable"))
            .alias("tradable_reason")
            if "is_tradable" in latest.columns
            else pl.lit("tradability status missing; kline visibility only").alias("tradable_reason"),
            pl.col("is_tradable").fill_null(True).alias("research_eligible")
            if "is_tradable" in latest.columns
            else pl.lit(True).alias("research_eligible"),
            pl.lit("visible PIT kline row").alias("reason"),
        )
        .select(list(schema))
        .sort("symbol")
    )


def _latest_status_rows(frame: pl.DataFrame, *, as_of: datetime, symbols: tuple[str, ...]) -> pl.DataFrame:
    if symbols and "symbol" in frame.columns:
        frame = frame.filter(pl.col("symbol").is_in(list(symbols)))
    visible = frame.filter(pl.col("available_at") <= as_of)
    if visible.is_empty():
        return visible
    return visible.sort(["symbol", "event_time"]).group_by("symbol", maintain_order=True).last()


def _merge_tradability_status(snapshot: pl.DataFrame, status: pl.DataFrame) -> pl.DataFrame:
    if snapshot.is_empty() or status.is_empty():
        return snapshot.with_columns(
            pl.lit(False).alias("research_eligible"),
            pl.lit("missing tradability status").alias("tradable_reason"),
        )
    status_cols = [
        "symbol",
        "is_st",
        "is_suspended",
        "limit_up",
        "limit_down",
        "listed_days",
        "is_tradable",
        "reason",
    ]
    joined = snapshot.drop([col for col in status_cols[1:] if col in snapshot.columns]).join(
        status.select([col for col in status_cols if col in status.columns]),
        on="symbol",
        how="left",
    )
    has_status = pl.col("is_tradable").is_not_null()
    return joined.with_columns(
        pl.col("is_st").fill_null(False),
        pl.col("is_suspended").fill_null(False),
        pl.col("limit_up").fill_null(False),
        pl.col("limit_down").fill_null(False),
        pl.col("listed_days").fill_null(-1).cast(pl.Int64),
        pl.col("is_tradable").fill_null(False),
        pl.when(has_status & pl.col("is_tradable"))
        .then(pl.lit("tradable"))
        .when(has_status)
        .then(pl.col("reason").fill_null("not tradable"))
        .otherwise(pl.lit("missing tradability status"))
        .alias("tradable_reason"),
        (has_status & pl.col("is_tradable").fill_null(False)).alias("research_eligible"),
    )


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


if __name__ == "__main__":
    raise SystemExit(main())
