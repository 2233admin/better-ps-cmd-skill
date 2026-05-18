"""Export A-share index daily bars from DuckDB into PIT parquet.

This is a migration bridge for benchmark indices already present in
DATA/Ashare/Aquant.duckdb.main.tdx_daily. It writes a PIT-safe daily price
dataset under DATA/Ashare/pit/index_daily_pit.parquet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl


DEFAULT_INDEX_SYMBOLS = (
    "sh000001",  # SSE Composite
    "sz399001",  # SZSE Component
    "sz399006",  # ChiNext
    "sh000300",  # CSI 300
    "sh000905",  # CSI 500
    "sh000852",  # CSI 1000
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="DATA/Ashare/Aquant.duckdb")
    parser.add_argument("--out-root", default="DATA/Ashare")
    parser.add_argument("--symbols", default=",".join(DEFAULT_INDEX_SYMBOLS))
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = export_index_daily(
        db_path=Path(args.db_path),
        out_root=Path(args.out_root),
        symbols=tuple(part.strip() for part in args.symbols.split(",") if part.strip()),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote index PIT parquet: {result['path']}")
    return 0


def export_index_daily(
    *,
    db_path: Path,
    out_root: Path,
    symbols: tuple[str, ...] = DEFAULT_INDEX_SYMBOLS,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    if not db_path.exists():
        raise SystemExit(f"DuckDB file not found: {db_path}")
    frame = _read_duckdb(db_path, symbols=symbols, start=start, end=end)
    if frame.is_empty():
        raise SystemExit("no index rows found in DuckDB tdx_daily")
    normalized = _normalize_index_frame(frame)
    out_path = out_root / "pit" / "index_daily_pit.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_parquet(out_path)
    manifest = {
        "dataset": "ashare.index_daily_pit",
        "path": _relative(out_path, out_root),
        "row_count": normalized.height,
        "symbol_count": normalized.select(pl.col("symbol").n_unique()).item(),
        "symbols": sorted(normalized["symbol"].unique().to_list()),
        "start": str(normalized["event_time"].min()),
        "end": str(normalized["event_time"].max()),
        "content_hash": _sha256_file(out_path),
        "source": f"duckdb:{_relative(db_path, out_root)}:main.tdx_daily",
        "requested_symbols": list(symbols),
    }
    manifest_path = out_root / "_manifest" / "index_daily_pit.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest | {"manifest_path": str(manifest_path)}


def _read_duckdb(
    db_path: Path,
    *,
    symbols: tuple[str, ...],
    start: date | None,
    end: date | None,
) -> pl.DataFrame:
    filters = ["symbol in (" + ",".join("?" for _ in symbols) + ")"]
    params: list[Any] = list(symbols)
    if start is not None:
        filters.append("date >= ?")
        params.append(start)
    if end is not None:
        filters.append("date <= ?")
        params.append(end)
    sql = f"""
        SELECT symbol, date, open, high, low, close, volume, amount
        FROM tdx_daily
        WHERE {' AND '.join(filters)}
        ORDER BY symbol, date
    """
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(sql, params).fetchall()
        return pl.DataFrame(
            rows,
            schema=["symbol", "date", "open", "high", "low", "close", "volume", "amount"],
            orient="row",
        )
    finally:
        conn.close()


def _normalize_index_frame(frame: pl.DataFrame) -> pl.DataFrame:
    event_time = pl.datetime(
        pl.col("date").cast(pl.Date).dt.year(),
        pl.col("date").cast(pl.Date).dt.month(),
        pl.col("date").cast(pl.Date).dt.day(),
        time_zone="UTC",
    )
    return (
        frame.with_columns(
            pl.col("symbol").map_elements(_canonical_index_symbol, return_dtype=pl.Utf8).alias("symbol"),
            event_time.alias("event_time"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Int64),
            pl.col("amount").cast(pl.Float64),
        )
        .with_columns(
            pl.col("symbol").str.split_exact(".", 1).struct.field("field_1").alias("market"),
            (pl.col("event_time") + pl.duration(days=1, hours=9, minutes=30)).alias("available_at"),
        )
        .with_columns(pl.col("available_at").alias("source_updated_at"))
        .select(
            "symbol",
            "market",
            "event_time",
            "available_at",
            "source_updated_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        )
        .unique(subset=["symbol", "event_time"], keep="last")
        .sort(["symbol", "event_time"])
    )


def _canonical_index_symbol(symbol: str) -> str:
    raw = str(symbol).lower()
    if raw.startswith("sh"):
        return f"{raw[2:].zfill(6)}.SH"
    if raw.startswith("sz"):
        return f"{raw[2:].zfill(6)}.SZ"
    if "." in raw:
        code, market = raw.split(".", 1)
        return f"{code.zfill(6)}.{market.upper()}"
    raise ValueError(f"unsupported index symbol: {symbol}")


def _parse_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
