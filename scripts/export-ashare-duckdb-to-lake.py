"""Export legacy A-share DuckDB daily bars into a PIT parquet lake."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl

from app.data.paths import resolve_ashare_duckdb_path, resolve_ashare_lake_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", help="Path to legacy DuckDB file")
    parser.add_argument("--out-root", help="A-share lake root")
    parser.add_argument("--start", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else resolve_ashare_duckdb_path()
    if not db_path.exists():
        raise SystemExit(f"missing DuckDB file: {db_path}")

    out_root = Path(args.out_root) if args.out_root else resolve_ashare_lake_root()
    frame = _export_daily_bars(db_path, args.start, args.end)
    if frame.is_empty():
        raise SystemExit("legacy DuckDB export returned no rows")

    dataset_path = out_root / "pit" / "kline_daily_pit.parquet"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(dataset_path)

    universe = {
        "source": "legacy_duckdb_export",
        "start": args.start,
        "end": args.end,
        "requested": {"count": frame["symbol"].n_unique(), "symbols": _symbols(frame)},
        "processed": {"count": frame["symbol"].n_unique(), "symbols": _symbols(frame)},
        "success": {"count": frame["symbol"].n_unique(), "symbols": _symbols(frame)},
        "empty": {"count": 0, "symbols": []},
        "failure": {"count": 0, "symbols": [], "items": []},
        "pit_symbols": {"count": frame["symbol"].n_unique(), "symbols": _symbols(frame)},
        "universe": {"count": frame["symbol"].n_unique(), "symbols": _symbols(frame)},
    }
    manifest_path = out_root / "_manifest" / "universe_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    summary = {
        "dataset": "ashare.kline_daily_pit",
        "path": str(dataset_path),
        "rows": frame.height,
        "symbols": _symbols(frame),
        "start": str(frame["event_time"].min()),
        "end": str(frame["event_time"].max()),
        "universe_manifest": str(manifest_path),
    }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) if args.json else f"wrote {frame.height} rows to {dataset_path}")
    return 0


def _export_daily_bars(db_path: Path, start: str | None, end: str | None) -> pl.DataFrame:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        where: list[str] = []
        params: list[str] = []
        if start:
            where.append("date >= ?")
            params.append(start)
        if end:
            where.append("date <= ?")
            params.append(end)
        query = """
            SELECT code, market, date, open, high, low, close, volume, amount
            FROM kline_daily
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY code, market, date"
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    finally:
        conn.close()

    raw = pl.DataFrame(rows, schema=columns, orient="row") if rows else pl.DataFrame()
    if raw.is_empty():
        return raw

    rows: list[dict] = []
    for item in raw.iter_rows(named=True):
        current = _as_date(item["date"])
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        available_at = event_time + timedelta(days=1, hours=9, minutes=30)
        rows.append(
            {
                "symbol": _canonical_symbol(str(item["code"]), int(item["market"])),
                "market": _canonical_market(int(item["market"])),
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": available_at,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": int(item["volume"]),
                "amount": float(item["amount"]),
            }
        )
    return pl.DataFrame(rows).sort(["symbol", "event_time"])


def _canonical_symbol(code: str, market: int) -> str:
    return f"{code.zfill(6)}.{_canonical_market(market)}"


def _canonical_market(market: int) -> str:
    return "SH" if market == 1 else "SZ"


def _symbols(frame: pl.DataFrame) -> list[str]:
    return sorted(str(symbol) for symbol in frame["symbol"].unique().to_list())


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


if __name__ == "__main__":
    raise SystemExit(main())
