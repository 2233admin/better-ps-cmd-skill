"""Sync A-share PIT daily bars into the legacy DuckDB daily cache."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import duckdb

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.paths import resolve_ashare_duckdb_path, resolve_ashare_lake_root


TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", help="Path to A-share DuckDB file")
    parser.add_argument("--pit-path", help="Path to kline_daily_pit.parquet")
    parser.add_argument("--table", default="tdx_daily", help="DuckDB target table name")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Build the batch but do not mutate DuckDB")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    table = _safe_table_name(args.table)
    db_path = Path(args.db_path) if args.db_path else resolve_ashare_duckdb_path()
    pit_path = Path(args.pit_path) if args.pit_path else resolve_ashare_lake_root() / "pit" / "kline_daily_pit.parquet"
    if not db_path.exists():
        raise SystemExit(f"missing DuckDB file: {db_path}")
    if not pit_path.exists():
        raise SystemExit(f"missing PIT parquet: {pit_path}")

    summary = _sync(
        db_path=db_path,
        pit_path=pit_path,
        table=table,
        start=args.start,
        end=args.end,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        action = "would sync" if args.dry_run else "synced"
        print(f"{action} {summary['batch_rows']} rows into {db_path}#{table}")
    return 0


def _sync(
    *,
    db_path: Path,
    pit_path: Path,
    table: str,
    start: str,
    end: str,
    dry_run: bool,
) -> dict[str, Any]:
    conn = duckdb.connect(str(db_path))
    try:
        _ensure_table(conn, table)
        before = _table_stats(conn, table)
        _create_batch(conn, pit_path, start, end)
        batch = _batch_stats(conn)
        if batch["rows"] == 0:
            raise SystemExit(f"PIT parquet returned no rows for {start}..{end}")

        matched_rows = conn.execute(
            f"""
            SELECT count(*)
            FROM {table} AS target
            JOIN ashare_pit_duckdb_batch AS batch
              ON target.symbol = batch.symbol AND target.date = batch.date
            """
        ).fetchone()[0]

        if dry_run:
            after = before
        else:
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    f"""
                    DELETE FROM {table}
                    USING ashare_pit_duckdb_batch AS batch
                    WHERE {table}.symbol = batch.symbol AND {table}.date = batch.date
                    """
                )
                conn.execute(
                    f"""
                    INSERT INTO {table} (symbol, date, open, high, low, close, volume, amount)
                    SELECT symbol, date, open, high, low, close, volume, amount
                    FROM ashare_pit_duckdb_batch
                    ORDER BY symbol, date
                    """
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("CHECKPOINT")
            after = _table_stats(conn, table)

        return {
            "db_path": str(db_path),
            "pit_path": str(pit_path),
            "table": table,
            "start": start,
            "end": end,
            "dry_run": dry_run,
            "batch_rows": batch["rows"],
            "batch_symbols": batch["symbols"],
            "batch_start": batch["start"],
            "batch_end": batch["end"],
            "matched_existing_rows": matched_rows,
            "before": before,
            "after": after,
        }
    finally:
        conn.close()


def _ensure_table(conn: duckdb.DuckDBPyConnection, table: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
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


def _create_batch(conn: duckdb.DuckDBPyConnection, pit_path: Path, start: str, end: str) -> None:
    conn.execute("DROP TABLE IF EXISTS ashare_pit_duckdb_batch")
    conn.execute(
        """
        CREATE TEMP TABLE ashare_pit_duckdb_batch AS
        SELECT
            lower(substr(symbol, 8, 2) || substr(symbol, 1, 6)) AS symbol,
            CAST(event_time AS DATE) AS date,
            CAST(open AS DOUBLE) AS open,
            CAST(high AS DOUBLE) AS high,
            CAST(low AS DOUBLE) AS low,
            CAST(close AS DOUBLE) AS close,
            CAST(volume AS BIGINT) AS volume,
            CAST(amount AS DOUBLE) AS amount
        FROM read_parquet(?)
        WHERE CAST(event_time AS DATE) >= CAST(? AS DATE)
          AND CAST(event_time AS DATE) <= CAST(? AS DATE)
          AND regexp_matches(symbol, '^[0-9]{6}\\.(SH|SZ|BJ)$')
        """,
        [str(pit_path), start, end],
    )


def _table_stats(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, Any]:
    rows, symbols, start, end = conn.execute(
        f"SELECT count(*), count(DISTINCT symbol), min(date), max(date) FROM {table}"
    ).fetchone()
    return {
        "rows": rows,
        "symbols": symbols,
        "start": str(start) if start is not None else None,
        "end": str(end) if end is not None else None,
    }


def _batch_stats(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows, symbols, start, end = conn.execute(
        """
        SELECT count(*), count(DISTINCT symbol), min(date), max(date)
        FROM ashare_pit_duckdb_batch
        """
    ).fetchone()
    return {
        "rows": rows,
        "symbols": symbols,
        "start": str(start) if start is not None else None,
        "end": str(end) if end is not None else None,
    }


def _safe_table_name(value: str) -> str:
    if not TABLE_NAME_RE.fullmatch(value):
        raise SystemExit(f"unsafe DuckDB table name: {value}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
