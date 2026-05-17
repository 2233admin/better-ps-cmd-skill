"""Build verifiable A-share trading calendar snapshots from PIT lake evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl


@dataclass(frozen=True)
class CalendarSnapshotResult:
    calendar_path: Path
    report_path: Path
    row_count: int
    markets: tuple[str, ...]
    start: date | None
    end: date | None
    source_hash: str
    anomalies: dict[str, list[dict[str, Any]]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "dataset": "ashare.trading_calendar",
            "path": str(self.calendar_path),
            "row_count": self.row_count,
            "markets": list(self.markets),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "source": "tdx_lake_observed",
            "source_hash": self.source_hash,
            "anomalies": _json_safe(self.anomalies),
        }


def build_calendar_snapshot(
    *,
    pit_path: Path,
    out_root: Path,
    start: date | None = None,
    end: date | None = None,
) -> CalendarSnapshotResult:
    """Materialize an A-share calendar snapshot from observed PIT kline dates."""

    if not pit_path.exists():
        raise FileNotFoundError(f"missing PIT parquet: {pit_path}")
    frame = pl.read_parquet(pit_path, columns=["market", "event_time", "symbol"])
    for column in ("market", "event_time", "symbol"):
        if column not in frame.columns:
            raise ValueError(f"A-share PIT parquet missing required calendar column: {column}")

    observed = _observed_market_dates(frame)
    min_date = start or observed["date"].min()
    max_date = end or observed["date"].max()
    if min_date is None or max_date is None:
        raise ValueError("cannot build calendar from empty PIT parquet")
    markets = tuple(sorted(str(market) for market in observed["market"].unique().to_list()))
    calendar = _calendar_grid(markets=markets, start=min_date, end=max_date).join(
        observed,
        on=["market", "date"],
        how="left",
    )
    source_hash = _sha256_file(pit_path)
    verified_at = datetime.now(UTC)
    calendar = (
        calendar.with_columns(
            pl.col("observed_rows").fill_null(0).cast(pl.Int64),
            pl.col("observed_symbols").fill_null(0).cast(pl.Int64),
        )
        .with_columns(
            (pl.col("observed_rows") > 0).alias("is_trading_day"),
            pl.lit("tdx_lake_observed").alias("source"),
            pl.lit(source_hash).alias("source_hash"),
            pl.lit(verified_at).alias("verified_at"),
        )
        .select(
            "date",
            "market",
            "is_trading_day",
            "observed_rows",
            "observed_symbols",
            "weekday_candidate",
            "source",
            "source_hash",
            "verified_at",
        )
        .sort(["date", "market"])
    )

    anomalies = _calendar_anomalies(calendar)
    calendar_path = out_root / "calendar" / "trading_calendar.parquet"
    report_path = out_root / "_manifest" / "trading_calendar.json"
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    calendar.write_parquet(calendar_path)
    result = CalendarSnapshotResult(
        calendar_path=calendar_path,
        report_path=report_path,
        row_count=calendar.height,
        markets=markets,
        start=min_date,
        end=max_date,
        source_hash=source_hash,
        anomalies=anomalies,
    )
    report_path.write_text(_json_dumps(result.to_payload()), encoding="utf-8")
    return result


def _observed_market_dates(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.select(
            pl.col("market").cast(pl.Utf8),
            pl.col("event_time").dt.date().alias("date"),
            pl.col("symbol").cast(pl.Utf8),
        )
        .group_by(["market", "date"])
        .agg(
            pl.len().alias("observed_rows"),
            pl.col("symbol").n_unique().alias("observed_symbols"),
        )
    )


def _calendar_grid(*, markets: tuple[str, ...], start: date, end: date) -> pl.DataFrame:
    dates = list(_date_range(start, end))
    rows = [
        {
            "market": market,
            "date": day,
            "weekday_candidate": day.weekday() < 5,
        }
        for day in dates
        for market in markets
    ]
    return pl.DataFrame(
        rows,
        schema={
            "market": pl.Utf8,
            "date": pl.Date,
            "weekday_candidate": pl.Boolean,
        },
    )


def _calendar_anomalies(calendar: pl.DataFrame) -> dict[str, list[dict[str, Any]]]:
    weekday_without_bars = (
        calendar.filter(pl.col("weekday_candidate") & ~pl.col("is_trading_day"))
        .select("date", "market")
        .to_dicts()
    )
    weekend_with_bars = (
        calendar.filter(~pl.col("weekday_candidate") & pl.col("is_trading_day"))
        .select("date", "market", "observed_rows", "observed_symbols")
        .to_dicts()
    )
    return {
        "weekday_without_bars": weekday_without_bars,
        "weekend_with_bars": weekend_with_bars,
    }


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True, indent=2)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value
