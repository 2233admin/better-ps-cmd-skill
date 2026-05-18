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
    dataset_path = out_root / "pit" / "kline_daily_pit.parquet"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    summary = _export_daily_bars_to_parquet(db_path, dataset_path, args.start, args.end)
    if summary["rows"] == 0:
        raise SystemExit("legacy DuckDB export returned no rows")

    universe = {
        "source": f"legacy_duckdb_export:{summary['source_table']}",
        "start": args.start,
        "end": args.end,
        "requested": {"count": len(summary["symbols"]), "symbols": summary["symbols"]},
        "processed": {"count": len(summary["symbols"]), "symbols": summary["symbols"]},
        "success": {"count": len(summary["symbols"]), "symbols": summary["symbols"]},
        "empty": {"count": 0, "symbols": []},
        "failure": {"count": 0, "symbols": [], "items": []},
        "pit_symbols": {"count": len(summary["symbols"]), "symbols": summary["symbols"]},
        "universe": {"count": len(summary["symbols"]), "symbols": summary["symbols"]},
    }
    manifest_path = out_root / "_manifest" / "universe_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    output = {
        "dataset": "ashare.kline_daily_pit",
        "source_table": summary["source_table"],
        "path": str(dataset_path),
        "rows": summary["rows"],
        "symbols": summary["symbols"],
        "start": str(summary["start"]),
        "end": str(summary["end"]),
        "universe_manifest": str(manifest_path),
    }
    print(json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2) if args.json else f"wrote {summary['rows']} rows to {dataset_path}")
    return 0


def _export_daily_bars_to_parquet(
    db_path: Path,
    dataset_path: Path,
    start: str | None,
    end: str | None,
) -> dict[str, object]:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        conn.execute("SET TimeZone='UTC'")
        source_table = _select_daily_source_table(conn)
        query = _daily_sql(source_table, start, end)
        conn.execute(f"COPY ({query} ORDER BY symbol, event_time) TO {_sql_string(dataset_path)} (FORMAT PARQUET)")
        rows, start_time, end_time = conn.execute(
            f"""
            SELECT
                count(*) AS rows,
                CAST(min(event_time) AS VARCHAR) AS start_time,
                CAST(max(event_time) AS VARCHAR) AS end_time
            FROM ({query})
            """
        ).fetchone()
        symbols = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT symbol FROM ({query}) ORDER BY symbol"
            ).fetchall()
        ]
    finally:
        conn.close()
    return {
        "source_table": source_table,
        "rows": int(rows),
        "symbols": symbols,
        "start": start_time,
        "end": end_time,
    }


def _export_daily_bars(db_path: Path, start: str | None, end: str | None) -> tuple[pl.DataFrame, str]:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        source_table = _select_daily_source_table(conn)
        query, params = _daily_query(source_table, start, end)
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    finally:
        conn.close()

    raw = pl.DataFrame(rows, schema=columns, orient="row") if rows else pl.DataFrame()
    if raw.is_empty():
        return raw, source_table

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
    return pl.DataFrame(rows).sort(["symbol", "event_time"]), source_table


def _select_daily_source_table(conn) -> str:
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    for table in ("tdx_daily", "kline_daily"):
        if table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return table
    raise SystemExit("no supported non-empty daily table found: expected tdx_daily or kline_daily")


def _daily_query(source_table: str, start: str | None, end: str | None) -> tuple[str, list[str]]:
    where: list[str] = []
    params: list[str] = []
    date_column = "date"
    if start:
        where.append(f"{date_column} >= ?")
        params.append(start)
    if end:
        where.append(f"{date_column} <= ?")
        params.append(end)
    if source_table == "tdx_daily":
        query = """
            SELECT
                substr(symbol, 3) AS code,
                CASE
                    WHEN lower(substr(symbol, 1, 2)) = 'sh' THEN 1
                    WHEN lower(substr(symbol, 1, 2)) = 'bj' THEN 2
                    ELSE 0
                END AS market,
                date,
                open,
                high,
                low,
                close,
                volume,
                amount
            FROM tdx_daily
            WHERE regexp_matches(lower(symbol), '^(sh60|sh68|sz00|sz30|bj43|bj83|bj87|bj88|bj92)')
        """
    else:
        query = """
            SELECT code, market, date, open, high, low, close, volume, amount
            FROM kline_daily
        """
    if where:
        query += " AND " + " AND ".join(where) if " WHERE " in query.upper() else " WHERE " + " AND ".join(where)
    query += " ORDER BY code, market, date"
    return query, params


def _daily_sql(source_table: str, start: str | None, end: str | None) -> str:
    start_date = _optional_sql_date(start)
    end_date = _optional_sql_date(end)
    where = []
    if start_date:
        where.append(f"date >= {start_date}")
    if end_date:
        where.append(f"date <= {end_date}")

    if source_table == "tdx_daily":
        where.insert(0, "regexp_matches(lower(symbol), '^(sh60|sh68|sz00|sz30|bj43|bj83|bj87|bj88|bj92)')")
        where_sql = " AND ".join(where)
        return f"""
            SELECT
                CASE
                    WHEN lower(substr(symbol, 1, 2)) = 'sh' THEN substr(symbol, 3) || '.SH'
                    WHEN lower(substr(symbol, 1, 2)) = 'bj' THEN substr(symbol, 3) || '.BJ'
                    ELSE substr(symbol, 3) || '.SZ'
                END AS symbol,
                CASE
                    WHEN lower(substr(symbol, 1, 2)) = 'sh' THEN 'SH'
                    WHEN lower(substr(symbol, 1, 2)) = 'bj' THEN 'BJ'
                    ELSE 'SZ'
                END AS market,
                CAST(date AS TIMESTAMPTZ) AS event_time,
                CAST(date AS TIMESTAMPTZ) + INTERVAL '1 day 9 hours 30 minutes' AS available_at,
                CAST(date AS TIMESTAMPTZ) + INTERVAL '1 day 9 hours 30 minutes' AS source_updated_at,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS BIGINT) AS volume,
                CAST(amount AS DOUBLE) AS amount
            FROM tdx_daily
            WHERE {where_sql}
        """

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return f"""
        SELECT
            lpad(CAST(code AS VARCHAR), 6, '0') || '.' || CASE WHEN market = 1 THEN 'SH' ELSE 'SZ' END AS symbol,
            CASE WHEN market = 1 THEN 'SH' ELSE 'SZ' END AS market,
            CAST(date AS TIMESTAMPTZ) AS event_time,
            CAST(date AS TIMESTAMPTZ) + INTERVAL '1 day 9 hours 30 minutes' AS available_at,
            CAST(date AS TIMESTAMPTZ) + INTERVAL '1 day 9 hours 30 minutes' AS source_updated_at,
            CAST(open AS DOUBLE) AS open,
            CAST(high AS DOUBLE) AS high,
            CAST(low AS DOUBLE) AS low,
            CAST(close AS DOUBLE) AS close,
            CAST(volume AS BIGINT) AS volume,
            CAST(amount AS DOUBLE) AS amount
        FROM kline_daily
        {where_sql}
    """


def _canonical_symbol(code: str, market: int) -> str:
    return f"{code.zfill(6)}.{_canonical_market(market)}"


def _canonical_market(market: int) -> str:
    if market == 1:
        return "SH"
    if market == 2:
        return "BJ"
    return "SZ"


def _symbols(frame: pl.DataFrame) -> list[str]:
    return sorted(str(symbol) for symbol in frame["symbol"].unique().to_list())


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _optional_sql_date(value: str | None) -> str | None:
    if not value:
        return None
    parsed = date.fromisoformat(value)
    return f"DATE '{parsed.isoformat()}'"


def _sql_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
