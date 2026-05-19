"""Ingest A-share daily kline from Aquant.duckdb into the warehouse-first PIT lake.

Source: <Aquant.duckdb>::tdx_daily  (~28M rows, 11k+ symbols)
Sink:   .data/lake/pit/kline_daily_pit.parquet  (PIT-safe panel)

Lands as part of XAR-453 (PR-2 C-axis: warehouse-first data layer cutover).
Replaces the legacy `delta_lake.py` + `chinadata` ingest chain on `origin/main`.

PIT contract
------------
event_time   = trading-day midnight UTC (date cast to TIMESTAMPTZ at 00:00:00 UTC)
available_at = event_time + 1 calendar day (conservative T+1 approximation)

Caveat: the real "next trading day" requires an exchange calendar.  A follow-up
PR (after PR-2 ships) should refine to use backend/app/research/pipeline/calendar.py
for next-trading-day lookup.  Being too pessimistic (later available_at) is safer
than too aggressive; all downstream consumers gate on `available_at <= as_of`.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import duckdb
import polars as pl

from app.data.paths import project_root, resolve_ashare_duckdb_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest tdx_daily from Aquant.duckdb into PIT parquet lake."
    )
    parser.add_argument("--duckdb", dest="duckdb_path", metavar="PATH",
                        help="Override Aquant.duckdb location")
    parser.add_argument("--output", metavar="PATH",
                        help="Override output parquet path")
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="Optional date floor (inclusive)")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="Optional date ceiling (inclusive)")
    parser.add_argument("--limit-symbols", metavar="N", type=int,
                        help="Take only first N symbols alphabetically (smoke test)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print row count + sample rows; do NOT write parquet")
    args = parser.parse_args(argv)

    duckdb_path = Path(args.duckdb_path).resolve() if args.duckdb_path else resolve_ashare_duckdb_path()
    if not duckdb_path.exists():
        print(f"[aquant-tdx] ERROR: duckdb not found: {duckdb_path}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output).resolve()
        if args.output
        else (project_root() / ".data/lake/pit/kline_daily_pit.parquet").resolve()
    )

    try:
        df = _read(duckdb_path, args.start, args.end, args.limit_symbols)
    except Exception:
        traceback.print_exc()
        return 1

    if df.is_empty():
        print("[aquant-tdx] ERROR: query returned 0 rows", file=sys.stderr)
        return 1

    print(f"[aquant-tdx] read {df.height:,} rows from {duckdb_path}", file=sys.stderr)

    if args.dry_run:
        print(f"[aquant-tdx] dry-run: planned output -> {output_path}", file=sys.stderr)
        print(f"row_count: {df.height}")
        print("first 5:")
        print(df.head(5))
        print("last 5:")
        print(df.tail(5))
        return 0

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(str(output_path), compression="zstd")
    except Exception:
        traceback.print_exc()
        return 1

    size_bytes = output_path.stat().st_size
    print(
        f"[aquant-tdx] wrote parquet to {output_path} ({size_bytes:,} bytes)",
        file=sys.stderr,
    )
    return 0


def _build_query(start: str | None, end: str | None, limit_symbols: int | None) -> str:
    source_updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    where_clauses: list[str] = []
    if start:
        where_clauses.append(f"date >= DATE '{start}'")
    if end:
        where_clauses.append(f"date <= DATE '{end}'")

    symbol_filter = ""
    if limit_symbols is not None and limit_symbols > 0:
        symbol_filter = f"""
            AND symbol IN (
                SELECT DISTINCT symbol FROM tdx_daily
                ORDER BY symbol
                LIMIT {limit_symbols}
            )
        """

    where_sql = ""
    if where_clauses or symbol_filter:
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else "WHERE 1=1"
        where_sql += symbol_filter

    return f"""
        SELECT
            symbol,
            CAST(date AS TIMESTAMPTZ) AS event_time,
            CAST(date AS TIMESTAMPTZ) + INTERVAL '1 day' AS available_at,
            TIMESTAMPTZ '{source_updated_at}' AS source_updated_at,
            CAST(open   AS DOUBLE)  AS open,
            CAST(high   AS DOUBLE)  AS high,
            CAST(low    AS DOUBLE)  AS low,
            CAST(close  AS DOUBLE)  AS close,
            CAST(volume AS BIGINT)  AS volume,
            CAST(amount AS DOUBLE)  AS amount
        FROM tdx_daily
        {where_sql}
        ORDER BY symbol, event_time
    """


def _read(
    duckdb_path: Path,
    start: str | None,
    end: str | None,
    limit_symbols: int | None,
) -> pl.DataFrame:
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        query = _build_query(start, end, limit_symbols)
        df: pl.DataFrame = con.sql(query).pl()
    finally:
        con.close()
    return df


if __name__ == "__main__":
    raise SystemExit(main())
