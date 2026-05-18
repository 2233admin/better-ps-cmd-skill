"""Ingest A-share daily bars from MiniQMT xtdata into the PIT parquet lake.

xtdata is an ingest adapter only. Research and backtests must read the promoted
PIT lake, never MiniQMT local caches directly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import polars as pl


SUPPORTED_ADJUST_TYPES = {"none", "front", "back", "front_ratio", "back_ratio"}
PIT_SCHEMA = {
    "symbol": pl.Utf8,
    "market": pl.Utf8,
    "event_time": pl.Datetime(time_zone="UTC"),
    "available_at": pl.Datetime(time_zone="UTC"),
    "source_updated_at": pl.Datetime(time_zone="UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download MiniQMT history into the xtdata local cache")
    _add_xtdata_args(download)
    download.add_argument("--json", action="store_true")

    extract = subparsers.add_parser("extract", help="Extract MiniQMT local cache into a PIT batch parquet")
    _add_xtdata_args(extract)
    extract.add_argument("--out-parquet", required=True)
    extract.add_argument("--json", action="store_true")

    normalize = subparsers.add_parser("normalize", help="Normalize a raw row parquet into a PIT batch parquet")
    normalize.add_argument("--raw-parquet", required=True)
    normalize.add_argument("--out-parquet", required=True)
    normalize.add_argument("--period", default="1d")
    normalize.add_argument("--adjust", default="none", choices=sorted(SUPPORTED_ADJUST_TYPES))
    normalize.add_argument("--json", action="store_true")

    promote = subparsers.add_parser("promote", help="Merge a PIT batch parquet into the lake")
    promote.add_argument("--batch-parquet", required=True)
    promote.add_argument("--out-root", required=True)
    promote.add_argument("--start")
    promote.add_argument("--end")
    promote.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "download":
        symbols = _parse_symbols(args.symbols)
        xtdata = _load_xtdata()
        result = download_xtdata_history(
            xtdata,
            symbols=symbols,
            start=args.start,
            end=args.end,
            period=args.period,
            adjust=args.adjust,
        )
        _print_result(result, json_output=args.json)
        return 0

    if args.command == "extract":
        symbols = _parse_symbols(args.symbols)
        xtdata = _load_xtdata()
        local_data = extract_xtdata_local_data(
            xtdata,
            symbols=symbols,
            start=args.start,
            end=args.end,
            period=args.period,
            adjust=args.adjust,
        )
        frame = normalize_xtdata_local_data(local_data, period=args.period, adjust=args.adjust)
        result = _write_batch(frame, Path(args.out_parquet), source="xtdata.get_local_data")
        _print_result(result, json_output=args.json)
        return 0

    if args.command == "normalize":
        frame = normalize_xtdata_rows(
            pl.read_parquet(args.raw_parquet).iter_rows(named=True),
            period=args.period,
            adjust=args.adjust,
        )
        result = _write_batch(frame, Path(args.out_parquet), source=str(args.raw_parquet))
        _print_result(result, json_output=args.json)
        return 0

    if args.command == "promote":
        return _promote_batch(
            batch_parquet=Path(args.batch_parquet),
            out_root=Path(args.out_root),
            start=args.start,
            end=args.end,
            json_output=args.json,
        )

    raise SystemExit(f"unsupported command: {args.command}")


def download_xtdata_history(
    xtdata: Any,
    *,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    period: str = "1d",
    adjust: str = "none",
) -> dict[str, Any]:
    _validate_xtdata_options(period=period, adjust=adjust)
    try:
        result = xtdata.download_history_data2(
            stock_list=list(symbols),
            period=period,
            start_time=start,
            end_time=end,
        )
        mode = "download_history_data2:keyword"
    except TypeError:
        try:
            result = xtdata.download_history_data2(list(symbols), period, start_time=start, end_time=end)
            mode = "download_history_data2:positional"
        except TypeError:
            result = [
                xtdata.download_history_data(
                    stock_code=symbol,
                    period=period,
                    start_time=start,
                    end_time=end,
                    incrementally=True,
                )
                for symbol in symbols
            ]
            mode = "download_history_data:per-symbol"
    return {
        "source": "xtdata",
        "mode": mode,
        "period": period,
        "adjust": adjust,
        "start": start,
        "end": end,
        "symbols": list(symbols),
        "result": _json_safe(result),
    }


def extract_xtdata_local_data(
    xtdata: Any,
    *,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    period: str = "1d",
    adjust: str = "none",
) -> Any:
    _validate_xtdata_options(period=period, adjust=adjust)
    try:
        return xtdata.get_local_data(
            stock_list=list(symbols),
            period=period,
            start_time=start,
            end_time=end,
            dividend_type=adjust,
        )
    except TypeError:
        return xtdata.get_local_data(
            stock_list=list(symbols),
            period=period,
            start_time=start,
            end_time=end,
        )


def normalize_xtdata_local_data(
    local_data: Any,
    *,
    period: str = "1d",
    adjust: str = "none",
) -> pl.DataFrame:
    _validate_xtdata_options(period=period, adjust=adjust)
    if not isinstance(local_data, dict):
        raise ValueError("xtdata local data must be a dict keyed by symbol")
    rows: list[dict[str, Any]] = []
    for symbol, value in local_data.items():
        for record in _records_from_xtdata_value(value):
            item = dict(record)
            item.setdefault("symbol", symbol)
            rows.append(item)
    return normalize_xtdata_rows(rows, period=period, adjust=adjust)


def normalize_xtdata_rows(
    rows: Iterable[dict[str, Any]],
    *,
    period: str = "1d",
    adjust: str = "none",
) -> pl.DataFrame:
    _validate_xtdata_options(period=period, adjust=adjust)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = _canonical_symbol(str(row.get("symbol") or row.get("stock_code") or row.get("code") or ""))
        if not symbol:
            continue
        event_time = _event_time(row.get("time", row.get("date", row.get("datetime"))))
        available_at = event_time + timedelta(days=1, hours=9, minutes=30)
        normalized.append(
            {
                "symbol": symbol,
                "market": symbol.split(".", 1)[1],
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": available_at,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row.get("volume", 0) or 0)),
                "amount": float(row.get("amount", row.get("turnover", 0)) or 0),
            }
        )
    if not normalized:
        return pl.DataFrame(schema=PIT_SCHEMA)
    return pl.DataFrame(normalized, schema=PIT_SCHEMA).unique(
        subset=["symbol", "event_time"],
        keep="last",
    ).sort(["symbol", "event_time"])


def _add_xtdata_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical symbols, e.g. 600000.SH")
    parser.add_argument("--start", required=True, help="Start date as YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date as YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--period", default="1d")
    parser.add_argument("--adjust", default="none", choices=sorted(SUPPORTED_ADJUST_TYPES))


def _validate_xtdata_options(*, period: str, adjust: str) -> None:
    if period != "1d":
        raise ValueError("xtdata PIT ingest currently promotes only period='1d'")
    if adjust not in SUPPORTED_ADJUST_TYPES:
        raise ValueError(f"unsupported xtdata adjust type: {adjust}")


def _load_xtdata() -> Any:
    try:
        from xtquant import xtdata
    except ImportError as exc:
        raise SystemExit("xtquant.xtdata is not available; start MiniQMT/QMT and install xtquant") from exc
    return xtdata


def _records_from_xtdata_value(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if value and all(isinstance(item, list) for item in value.values()):
            keys = list(value)
            count = min(len(value[key]) for key in keys)
            return [{key: value[key][idx] for key in keys} for idx in range(count)]
        return [dict(value)]
    frame = value
    columns = [str(column) for column in getattr(frame, "columns", [])]
    if "time" not in columns and "date" not in columns and hasattr(frame, "reset_index"):
        frame = frame.reset_index()
    if hasattr(frame, "to_dict"):
        try:
            return [dict(item) for item in frame.to_dict("records")]
        except TypeError:
            pass
    if hasattr(frame, "iter_rows"):
        return [dict(item) for item in frame.iter_rows(named=True)]
    raise ValueError(f"unsupported xtdata local data value: {type(value)!r}")


def _event_time(value: Any) -> datetime:
    if value is None:
        raise ValueError("xtdata row missing time/date")
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        current = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime(current.year, current.month, current.day, tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, int | float):
        number = int(value)
        if 19000101 <= number <= 29991231:
            text = str(number)
            return datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), tzinfo=UTC)
        seconds = number / 1000 if number > 10_000_000_000 else number
        current = datetime.fromtimestamp(seconds, tz=UTC)
        return datetime(current.year, current.month, current.day, tzinfo=UTC)
    text = str(value).strip()
    if not text:
        raise ValueError("xtdata row missing time/date")
    if text.isdigit() and len(text) == 8:
        return datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), tzinfo=UTC)
    current = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    return datetime(current.year, current.month, current.day, tzinfo=UTC)


def _canonical_symbol(value: str) -> str:
    raw = value.strip().upper()
    if not raw:
        return ""
    if len(raw) == 8 and raw[:2] in {"SH", "SZ", "BJ"} and raw[2:].isdigit():
        return f"{raw[2:]}.{raw[:2]}"
    if "." in raw:
        code, market = raw.split(".", 1)
        market = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(market, market)
        return f"{code.zfill(6)}.{market}"
    code = raw.zfill(6)
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = [_canonical_symbol(item) for item in raw.split(",") if item.strip()]
    return tuple(symbol for symbol in symbols if symbol)


def _write_batch(frame: pl.DataFrame, path: Path, *, source: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    return {
        "dataset": "ashare.kline_daily_pit",
        "source": source,
        "path": str(path),
        "rows": frame.height,
        "symbols": sorted(frame["symbol"].unique().to_list()) if frame.height else [],
        "start": str(frame["event_time"].min()) if frame.height else None,
        "end": str(frame["event_time"].max()) if frame.height else None,
    }


def _promote_batch(
    *,
    batch_parquet: Path,
    out_root: Path,
    start: str | None,
    end: str | None,
    json_output: bool,
) -> int:
    module = _load_incremental_module()
    argv = ["--out-root", str(out_root), "--batch-parquet", str(batch_parquet)]
    if start:
        argv.extend(["--start", start])
    if end:
        argv.extend(["--end", end])
    if json_output:
        argv.append("--json")
    return module.main(argv)


def _load_incremental_module() -> Any:
    path = Path(__file__).with_name("ingest-ashare-incremental.py")
    spec = importlib.util.spec_from_file_location("ingest_ashare_incremental", path)
    module = importlib.util.module_from_spec(spec)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot load incremental ingest script: {path}")
    spec.loader.exec_module(module)
    return module


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(result)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
