"""Build A-share tradability PIT from ChinaData/Tushare official status feeds.

This producer aligns official ST name windows, daily suspension notices, and
daily limit prices onto the observed daily PIT bar grid so backtests can stop
guessing with TDX-only snapshots.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import polars as pl

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import canonical_delta_path, read_delta_or_parquet, scan_delta_or_parquet, write_polars_delta

TOOL_DATA_DIR = Path(__file__).resolve().parents[1] / "backend" / "tools" / "data"
if str(TOOL_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DATA_DIR))

from tushare_client import fetch_tushare_dataframe, resolve_token  # noqa: E402


DEFAULT_OUTSET = "DATA/Ashare"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=DEFAULT_OUTSET)
    parser.add_argument("--start", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date YYYY-MM-DD")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    parser.add_argument("--token", help="Optional ChinaData token override")
    parser.add_argument("--pause-seconds", type=float, default=0.05)
    parser.add_argument("--namechange-csv", help="Optional offline CSV fixture")
    parser.add_argument("--suspend-csv", help="Optional offline CSV fixture")
    parser.add_argument("--stk-limit-csv", help="Optional offline CSV fixture")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = build_official_tradability_status(
        data_root=Path(args.data_root),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        symbols=tuple(part.strip().upper() for part in (args.symbols or "").split(",") if part.strip()),
        token=args.token,
        pause_seconds=args.pause_seconds,
        namechange_frame=_read_csv(args.namechange_csv),
        suspend_frame=_read_csv(args.suspend_csv),
        stk_limit_frame=_read_csv(args.stk_limit_csv),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote official tradability PIT: {result['path']}")
    return 0


def build_official_tradability_status(
    *,
    data_root: Path,
    start: date | None = None,
    end: date | None = None,
    symbols: tuple[str, ...] = (),
    token: str | None = None,
    pause_seconds: float = 0.05,
    namechange_frame: pd.DataFrame | None = None,
    suspend_frame: pd.DataFrame | None = None,
    stk_limit_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    kline_path = data_root / "pit" / "kline_daily_pit.parquet"
    if not kline_path.exists():
        raise SystemExit(f"missing kline PIT parquet: {kline_path}")

    min_trade_date, max_trade_date, row_count, symbol_count = _base_stats(
        kline_path,
        start=start,
        end=end,
        symbols=symbols,
    )
    if min_trade_date is None or max_trade_date is None or row_count == 0:
        raise SystemExit("no kline PIT rows matched the requested filters")

    source_updated_at = datetime.now(tz=UTC).isoformat()
    resolved_token = token or resolve_token()
    namechange = namechange_frame if namechange_frame is not None else _fetch_namechange(
        min_trade_date, max_trade_date, pause_seconds=pause_seconds, token=resolved_token
    )
    suspensions = suspend_frame if suspend_frame is not None else _fetch_ranged_api(
        "suspend_d",
        min_trade_date,
        max_trade_date,
        pause_seconds=pause_seconds,
        token=resolved_token,
    )
    stk_limit = stk_limit_frame if stk_limit_frame is not None else _fetch_ranged_api(
        "stk_limit",
        min_trade_date,
        max_trade_date,
        pause_seconds=pause_seconds,
        token=resolved_token,
    )

    normalized_namechange = _normalize_namechange(namechange)
    normalized_suspend = _normalize_suspend(suspensions)
    normalized_limit = _normalize_stk_limit(stk_limit)

    parquet_path = data_root / "pit" / "tradability_status_pit.parquet"
    dataset_path = canonical_delta_path(parquet_path)
    materialized_path = data_root / "pit" / "_tradability_status_pit_materialized.parquet"
    universe_path = data_root / "_manifest" / "tradability_universe.json"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.parent.mkdir(parents=True, exist_ok=True)

    _write_tradability_parquet(
        kline_path=kline_path,
        out_path=materialized_path,
        start=start,
        end=end,
        symbols=symbols,
        namechange=normalized_namechange,
        suspensions=normalized_suspend,
        stk_limit=normalized_limit,
        source_updated_at=source_updated_at,
    )
    write_polars_delta(
        pl.read_parquet(materialized_path),
        dataset_path,
        mode="overwrite",
        partition_by=["trade_year", "trade_month"],
        schema_mode="overwrite",
    )
    materialized_path.unlink(missing_ok=True)
    if parquet_path.exists():
        parquet_path.unlink()

    frame = scan_delta_or_parquet(dataset_path)
    stats = frame.select(
        pl.len().alias("row_count"),
        pl.col("symbol").n_unique().alias("symbol_count"),
        pl.col("event_time").min().alias("start"),
        pl.col("event_time").max().alias("end"),
        pl.col("is_st").sum().alias("st_rows"),
        pl.col("is_suspended").sum().alias("suspended_rows"),
        pl.col("limit_up").sum().alias("limit_up_rows"),
        pl.col("limit_down").sum().alias("limit_down_rows"),
    ).collect().row(0, named=True)

    universe = {
        "dataset": "ashare.tradability_status_pit",
        "source": [
            "chinadata:namechange",
            "chinadata:suspend_d",
            "chinadata:stk_limit",
            f"pit:{_relative(kline_path, data_root)}",
        ],
        "mode": "official_daily_status_aligned_to_kline_daily_pit",
        "path": _relative(dataset_path, data_root),
        "base_row_count": int(row_count),
        "base_symbol_count": int(symbol_count),
        "namechange_rows": int(len(normalized_namechange)),
        "suspend_rows": int(len(normalized_suspend)),
        "stk_limit_rows": int(len(normalized_limit)),
        "row_count": int(stats["row_count"]),
        "symbol_count": int(stats["symbol_count"]),
        "start": str(stats["start"]),
        "end": str(stats["end"]),
        "official_counts": {
            "st_rows": int(stats["st_rows"]),
            "suspended_rows": int(stats["suspended_rows"]),
            "limit_up_rows": int(stats["limit_up_rows"]),
            "limit_down_rows": int(stats["limit_down_rows"]),
        },
        "honest_gaps": [
            "status rows are aligned to observed kline_daily_pit bars; full-day suspended symbols without a daily bar are not materialized as standalone PIT rows",
            "namechange ann_date is used as visibility when present; rows with missing ann_date fall back to start_date",
            "limit_up/limit_down are derived from official up_limit/down_limit prices against the observed daily close",
        ],
    }
    universe_path.write_text(
        json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return {
        "dataset": "ashare.tradability_status_pit",
        "path": str(dataset_path),
        "universe_manifest": str(universe_path),
        **{key: (str(value) if key in {"start", "end"} else int(value)) for key, value in stats.items()},
    }


def _base_stats(
    kline_path: Path,
    *,
    start: date | None,
    end: date | None,
    symbols: tuple[str, ...],
) -> tuple[date | None, date | None, int, int]:
    conn = duckdb.connect()
    try:
        where_sql, params = _base_filters(start=start, end=end, symbols=symbols)
        sql = f"""
            SELECT
                min(CAST(event_time AS DATE)) AS start_date,
                max(CAST(event_time AS DATE)) AS end_date,
                count(*) AS row_count,
                count(DISTINCT symbol) AS symbol_count
            FROM read_parquet(?)
            {where_sql}
        """
        row = conn.execute(sql, [str(kline_path), *params]).fetchone()
    finally:
        conn.close()
    return row[0], row[1], int(row[2] or 0), int(row[3] or 0)


def _write_tradability_parquet(
    *,
    kline_path: Path,
    out_path: Path,
    start: date | None,
    end: date | None,
    symbols: tuple[str, ...],
    namechange: pd.DataFrame,
    suspensions: pd.DataFrame,
    stk_limit: pd.DataFrame,
    source_updated_at: str,
) -> None:
    conn = duckdb.connect()
    try:
        conn.register("namechange_rows", namechange)
        conn.register("suspension_rows", suspensions)
        conn.register("limit_rows", stk_limit)
        base_where_sql, base_params = _base_filters(start=start, end=end, symbols=symbols)
        symbol_where_sql, symbol_params = _base_filters(start=None, end=None, symbols=symbols)
        out_sql = str(out_path).replace("\\", "/").replace("'", "''")
        sql = f"""
            COPY (
                WITH base AS (
                    SELECT
                        symbol,
                        market,
                        event_time,
                        available_at,
                        close,
                        CAST(event_time AS DATE) AS trade_date
                    FROM read_parquet(?)
                    {base_where_sql}
                ),
                first_dates AS (
                    SELECT
                        symbol,
                        min(CAST(event_time AS DATE)) AS first_trade_date
                    FROM read_parquet(?)
                    {symbol_where_sql}
                    GROUP BY symbol
                ),
                st_flags AS (
                    SELECT
                        b.symbol,
                        b.trade_date,
                        coalesce(bool_or(n.is_st), FALSE) AS is_st,
                        coalesce(bool_or(n.is_delisted), FALSE) AS is_delisted
                    FROM base b
                    LEFT JOIN namechange_rows n
                      ON b.symbol = n.symbol
                     AND b.trade_date BETWEEN n.start_date AND n.end_date
                     AND CAST(b.available_at AS DATE) >= n.available_from_date
                    GROUP BY b.symbol, b.trade_date
                ),
                suspend_flags AS (
                    SELECT
                        symbol,
                        trade_date,
                        TRUE AS is_suspended
                    FROM suspension_rows
                    GROUP BY symbol, trade_date
                ),
                limit_flags AS (
                    SELECT
                        symbol,
                        trade_date,
                        max(up_limit_price) AS up_limit_price,
                        max(down_limit_price) AS down_limit_price
                    FROM limit_rows
                    GROUP BY symbol, trade_date
                )
                SELECT
                    b.symbol,
                    b.market,
                    b.event_time,
                    b.available_at,
                    year(b.event_time) AS trade_year,
                    month(b.event_time) AS trade_month,
                    CAST(? AS TIMESTAMPTZ) AS source_updated_at,
                    coalesce(st.is_st, FALSE) AS is_st,
                    coalesce(s.is_suspended, FALSE) AS is_suspended,
                    CASE
                        WHEN l.up_limit_price IS NULL THEN FALSE
                        WHEN b.close >= l.up_limit_price - greatest(0.005, l.up_limit_price * 0.0005) THEN TRUE
                        ELSE FALSE
                    END AS limit_up,
                    CASE
                        WHEN l.down_limit_price IS NULL THEN FALSE
                        WHEN b.close <= l.down_limit_price + greatest(0.005, l.down_limit_price * 0.0005) THEN TRUE
                        ELSE FALSE
                    END AS limit_down,
                    datediff('day', fd.first_trade_date, b.trade_date) + 1 AS listed_days,
                    CASE
                        WHEN coalesce(st.is_delisted, FALSE) OR coalesce(s.is_suspended, FALSE) THEN FALSE
                        ELSE TRUE
                    END AS is_tradable,
                    concat_ws(
                        ',',
                        CASE WHEN coalesce(st.is_st, FALSE) THEN 'st_namechange' END,
                        CASE WHEN coalesce(st.is_delisted, FALSE) THEN 'delist_namechange' END,
                        CASE WHEN coalesce(s.is_suspended, FALSE) THEN 'suspend_d' END,
                        CASE
                            WHEN l.up_limit_price IS NOT NULL
                             AND b.close >= l.up_limit_price - greatest(0.005, l.up_limit_price * 0.0005)
                            THEN 'official_up_limit_price'
                        END,
                        CASE
                            WHEN l.down_limit_price IS NOT NULL
                             AND b.close <= l.down_limit_price + greatest(0.005, l.down_limit_price * 0.0005)
                            THEN 'official_down_limit_price'
                        END
                    ) AS reason,
                    l.up_limit_price,
                    l.down_limit_price,
                    CASE
                        WHEN l.up_limit_price IS NOT NULL OR l.down_limit_price IS NOT NULL THEN 'chinadata.stk_limit'
                        ELSE NULL
                    END AS limit_price_source,
                    'chinadata_official' AS status_source
                FROM base b
                LEFT JOIN first_dates fd USING(symbol)
                LEFT JOIN st_flags st
                  ON b.symbol = st.symbol
                 AND b.trade_date = st.trade_date
                LEFT JOIN suspend_flags s
                  ON b.symbol = s.symbol
                 AND b.trade_date = s.trade_date
                LEFT JOIN limit_flags l
                  ON b.symbol = l.symbol
                 AND b.trade_date = l.trade_date
                ORDER BY b.symbol, b.event_time
            ) TO '{out_sql}' (FORMAT PARQUET)
        """
        conn.execute(
            sql,
            [
                str(kline_path),
                *base_params,
                str(kline_path),
                *symbol_params,
                source_updated_at,
            ],
        )
    finally:
        conn.close()


def _fetch_namechange(
    start: date,
    end: date,
    *,
    pause_seconds: float,
    token: str,
) -> pd.DataFrame:
    frames = [fetch_tushare_dataframe("namechange", retries=2, token=token)]
    for year_start, year_end in _year_ranges(start, end):
        frame = fetch_tushare_dataframe(
            "namechange",
            params={"start_date": year_start.strftime("%Y%m%d"), "end_date": year_end.strftime("%Y%m%d")},
            retries=2,
            token=token,
        )
        frames.append(frame)
        time.sleep(pause_seconds)
    return _concat_frames(frames)


def _fetch_ranged_api(
    api_name: str,
    start: date,
    end: date,
    *,
    pause_seconds: float,
    token: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in _month_ranges(start, end):
        frame = fetch_tushare_dataframe(
            api_name,
            params={
                "start_date": chunk_start.strftime("%Y%m%d"),
                "end_date": chunk_end.strftime("%Y%m%d"),
            },
            retries=2,
            token=token,
        )
        frames.append(frame)
        time.sleep(pause_seconds)
    return _concat_frames(frames)


def _normalize_namechange(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "start_date", "end_date", "available_from_date", "is_st", "is_delisted"])
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["name"] = data["name"].fillna("").astype(str)
    data["change_reason"] = data.get("change_reason", "").fillna("").astype(str)
    data["is_st"] = data["name"].map(_looks_st)
    data["is_delisted"] = data["name"].str.contains("退") | data["change_reason"].str.contains("终止上市|退市", regex=True)
    data = data[data["is_st"] | data["is_delisted"]].copy()
    if data.empty:
        return pd.DataFrame(columns=["symbol", "start_date", "end_date", "available_from_date", "is_st", "is_delisted"])
    data["start_date"] = pd.to_datetime(data["start_date"], format="%Y%m%d", errors="coerce").dt.date
    data["end_date"] = pd.to_datetime(data["end_date"], format="%Y%m%d", errors="coerce").dt.date
    data["available_from_date"] = pd.to_datetime(data.get("ann_date"), format="%Y%m%d", errors="coerce").dt.date
    data["available_from_date"] = data["available_from_date"].fillna(data["start_date"])
    data["end_date"] = data["end_date"].fillna(date(2100, 12, 31))
    data = data.dropna(subset=["symbol", "start_date", "available_from_date"])
    return data[["symbol", "start_date", "end_date", "available_from_date", "is_st", "is_delisted"]].drop_duplicates()


def _normalize_suspend(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "trade_date"])
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["trade_date"] = pd.to_datetime(data["trade_date"], format="%Y%m%d", errors="coerce").dt.date
    data["suspend_type"] = data.get("suspend_type", "").fillna("").astype(str).str.upper()
    data = data[(data["suspend_type"] == "S") & data["trade_date"].notna()]
    return data[["symbol", "trade_date"]].drop_duplicates()


def _normalize_stk_limit(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "trade_date", "up_limit_price", "down_limit_price"])
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["trade_date"] = pd.to_datetime(data["trade_date"], format="%Y%m%d", errors="coerce").dt.date
    data["up_limit_price"] = pd.to_numeric(data.get("up_limit"), errors="coerce")
    data["down_limit_price"] = pd.to_numeric(data.get("down_limit"), errors="coerce")
    data = data.dropna(subset=["trade_date"])
    return data[["symbol", "trade_date", "up_limit_price", "down_limit_price"]].drop_duplicates()


def _base_filters(*, start: date | None, end: date | None, symbols: tuple[str, ...]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(event_time AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(event_time AS DATE) <= ?")
        params.append(end)
    if symbols:
        clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
        params.extend(symbols)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


def _month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    current = date(start.year, start.month, 1)
    while current <= end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        chunk_end = min(next_month.fromordinal(next_month.toordinal() - 1), end)
        chunk_start = max(current, start)
        ranges.append((chunk_start, chunk_end))
        current = next_month
    return ranges


def _year_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(date(year, 1, 1), start)
        chunk_end = min(date(year, 12, 31), end)
        ranges.append((chunk_start, chunk_end))
    return ranges


def _concat_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True).drop_duplicates()


def _looks_st(name: str) -> bool:
    upper = str(name).upper().replace(" ", "")
    return "ST" in upper or "PT" in upper


def _read_csv(path: str | None) -> pd.DataFrame | None:
    if not path:
        return None
    return pd.read_csv(path)


def _parse_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
