"""Ingest A-share daily bars from tdx-cli into a PIT parquet lake."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-cli", help="Path to tdx-cli executable")
    parser.add_argument("--symbols", help="Comma-separated canonical symbols, e.g. 600000.SH")
    parser.add_argument("--scan-out-dir", help="Existing tdx-cli scan-kline --out-dir parquet dataset")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--out-root", required=True, help="A-share lake root")
    parser.add_argument("--dataset", default="ashare.kline_daily_pit")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    if args.scan_out_dir:
        frame, universe = _normalize_scan_out_dir(Path(args.scan_out_dir))
    else:
        if not args.tdx_cli:
            raise SystemExit("--tdx-cli is required unless --scan-out-dir is provided")
        if not args.symbols:
            raise SystemExit("--symbols is required unless --scan-out-dir is provided")
        tdx_cli = Path(args.tdx_cli)
        if not tdx_cli.exists():
            raise SystemExit(f"tdx-cli not found: {tdx_cli}")
        symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
        rows: list[dict[str, Any]] = []
        processed_symbols: list[str] = []
        success_symbols: list[str] = []
        empty_symbols: list[str] = []
        failed_symbols: list[dict[str, str]] = []
        for symbol in symbols:
            code, market = _split_symbol(symbol)
            processed_symbols.append(symbol)
            try:
                payload = _run_tdxcli(tdx_cli, code, args.start, args.end)
            except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
                failed_symbols.append({"symbol": symbol, "reason": _failure_reason(exc)})
                continue
            items = payload.get("items", [])
            if items:
                success_symbols.append(symbol)
            else:
                empty_symbols.append(symbol)
            for item in payload.get("items", []):
                rows.append(_normalize_bar(item, symbol=symbol, market=market))
        universe = _build_universe_manifest(
            source="tdx-cli kline-range",
            requested_symbols=symbols,
            processed_symbols=processed_symbols,
            success_symbols=success_symbols,
            empty_symbols=empty_symbols,
            failed_symbols=failed_symbols,
            pit_symbols=_symbols_from_rows(rows),
            start=args.start,
            end=args.end,
        )
        if not rows:
            _write_universe_manifest(out_root, universe)
            raise SystemExit("tdx-cli returned no rows")
        frame = pl.DataFrame(rows)

    frame = frame.sort(["symbol", "event_time"])
    dataset_path = out_root / "pit" / "kline_daily_pit.parquet"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(dataset_path)
    universe_path = _write_universe_manifest(out_root, universe)

    summary = {
        "dataset": args.dataset,
        "path": str(dataset_path),
        "rows": frame.height,
        "symbols": sorted(frame["symbol"].unique().to_list()),
        "start": str(frame["event_time"].min()),
        "end": str(frame["event_time"].max()),
        "universe_manifest": str(universe_path),
        "universe": universe,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote {frame.height} rows to {dataset_path}")
    return 0


def _split_symbol(symbol: str) -> tuple[str, str]:
    if "." not in symbol:
        raise SystemExit(f"symbol must be canonical CODE.MARKET: {symbol}")
    code, market = symbol.split(".", 1)
    market = market.upper()
    if market not in {"SH", "SZ", "BJ"}:
        raise SystemExit(f"unsupported market suffix: {symbol}")
    return code, market


def _run_tdxcli(tdx_cli: Path, code: str, start: str, end: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(tdx_cli), "kline-range", code, "--start", start, "--end", end, "--json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _failure_reason(exc: subprocess.CalledProcessError | json.JSONDecodeError) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout
        return f"exit {exc.returncode}: {detail}" if detail else f"exit {exc.returncode}"
    return f"invalid json: {exc}"


def _normalize_bar(item: dict[str, Any], *, symbol: str, market: str) -> dict[str, Any]:
    current = datetime.fromisoformat(str(item["date"])).date()
    event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
    available_at = event_time + timedelta(days=1, hours=9, minutes=30)
    return {
        "symbol": symbol,
        "market": market,
        "event_time": event_time,
        "available_at": available_at,
        "source_updated_at": available_at,
        "open": float(item["open"]),
        "high": float(item["high"]),
        "low": float(item["low"]),
        "close": float(item["close"]),
        "volume": int(item["volume"]),
        "amount": float(item["amount"]),
        "is_suspended": False,
        "is_st": False,
        "limit_up": False,
        "limit_down": False,
    }


def _normalize_scan_out_dir(path: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    parquet_paths = sorted(path.rglob("*.parquet"))
    if not parquet_paths:
        raise SystemExit(f"scan out dir contains no parquet files: {path}")
    raw = pl.concat([pl.read_parquet(item) for item in parquet_paths], how="vertical_relaxed")
    required = {
        "market",
        "code",
        "date",
        "open_i64",
        "high_i64",
        "low_i64",
        "close_i64",
        "amount_i64",
        "volume",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise SystemExit(f"scan parquet missing columns: {', '.join(missing)}")

    rows: list[dict[str, Any]] = []
    for item in raw.iter_rows(named=True):
        market = str(item["market"]).upper()
        symbol = f"{str(item['code']).zfill(6)}.{market}"
        current = item["date"]
        event_time = datetime(current.year, current.month, current.day, tzinfo=UTC)
        available_at = event_time + timedelta(days=1, hours=9, minutes=30)
        rows.append(
            {
                "symbol": symbol,
                "market": market,
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": available_at,
                "open": int(item["open_i64"]) / 10000.0,
                "high": int(item["high_i64"]) / 10000.0,
                "low": int(item["low_i64"]) / 10000.0,
                "close": int(item["close_i64"]) / 10000.0,
                "volume": int(item["volume"]),
                "amount": int(item["amount_i64"]) / 10000.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
            }
        )
    frame = pl.DataFrame(rows)
    source_manifest = _read_scan_universe_manifest(path)
    pit_symbols = sorted(frame["symbol"].unique().to_list()) if frame.height else []
    universe = _build_universe_manifest(
        source="tdx-cli scan-kline",
        requested_symbols=source_manifest.get("requested_symbols") or pit_symbols,
        processed_symbols=source_manifest.get("processed_symbols") or source_manifest.get("processed") or pit_symbols,
        success_symbols=source_manifest.get("success_symbols") or source_manifest.get("success") or pit_symbols,
        empty_symbols=source_manifest.get("empty_symbols") or source_manifest.get("empty") or [],
        failed_symbols=source_manifest.get("failed_symbols")
        or source_manifest.get("failure_symbols")
        or source_manifest.get("failures")
        or source_manifest.get("failure")
        or [],
        pit_symbols=pit_symbols,
        start=_json_scalar(source_manifest.get("start")),
        end=_json_scalar(source_manifest.get("end")),
    )
    return frame, universe


def _read_scan_universe_manifest(path: Path) -> dict[str, Any]:
    checkpoint = _read_scan_checkpoint(path)
    jsonl_manifest = _read_scan_manifest_jsonl(path)
    if jsonl_manifest:
        return checkpoint | jsonl_manifest

    names = {
        "universe_manifest.json",
        "scan_manifest.json",
        "scan_summary.json",
        "summary.json",
        "manifest.json",
    }
    for candidate in sorted(path.rglob("*.json")):
        if candidate.name not in names:
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            universe = payload.get("universe")
            return universe if isinstance(universe, dict) else payload
    return checkpoint


def _read_scan_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = path / "_checkpoint.json"
    if not checkpoint.exists():
        return {}
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    requested_range = payload.get("requested_range", {})
    return {
        "requested_count": payload.get("requested_symbols"),
        "processed_count": payload.get("processed"),
        "success_count": payload.get("success"),
        "empty_count": payload.get("empty"),
        "failure_count": payload.get("failure"),
        "start": requested_range.get("start") if isinstance(requested_range, dict) else None,
        "end": requested_range.get("end") if isinstance(requested_range, dict) else None,
        "markets": payload.get("markets", []),
        "equity_only": payload.get("equity_only"),
        "elapsed_ms": payload.get("elapsed_ms"),
        "concurrency": payload.get("concurrency"),
    }


def _read_scan_manifest_jsonl(path: Path) -> dict[str, Any]:
    manifest = path / "_manifest.jsonl"
    if not manifest.exists():
        return {}
    symbols: list[str] = []
    empty_symbols: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        symbol = _scan_manifest_symbol(item)
        if not symbol:
            continue
        symbols.append(symbol)
        if int(item.get("count", 0) or 0) == 0:
            empty_symbols.append(symbol)
    return {
        "requested_symbols": symbols,
        "processed_symbols": symbols,
        "success_symbols": symbols,
        "empty_symbols": empty_symbols,
    }


def _scan_manifest_symbol(item: dict[str, Any]) -> str:
    code = str(item.get("code", "")).strip()
    market = str(item.get("market", "")).strip().upper()
    if not code or market not in {"SH", "SZ", "BJ"}:
        return ""
    return f"{code.zfill(6)}.{market}"


def _build_universe_manifest(
    *,
    source: str,
    requested_symbols: Any,
    processed_symbols: Any,
    success_symbols: Any,
    empty_symbols: Any,
    failed_symbols: Any,
    pit_symbols: Any,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    requested = _symbol_list(requested_symbols)
    processed = _symbol_list(processed_symbols)
    success = _symbol_list(success_symbols)
    empty = _symbol_list(empty_symbols)
    failures = _failure_list(failed_symbols)
    failed = _symbol_list([item["symbol"] for item in failures])
    pit = _symbol_list(pit_symbols)
    universe = _symbol_list([*requested, *processed, *success, *empty, *failed, *pit])
    return {
        "source": source,
        "start": start,
        "end": end,
        "requested": {"count": len(requested), "symbols": requested},
        "processed": {"count": len(processed), "symbols": processed},
        "success": {"count": len(success), "symbols": success},
        "empty": {"count": len(empty), "symbols": empty},
        "failure": {"count": len(failures), "symbols": failed, "items": failures},
        "pit_symbols": {"count": len(pit), "symbols": pit},
        "universe": {"count": len(universe), "symbols": universe},
    }


def _write_universe_manifest(out_root: Path, universe: dict[str, Any]) -> Path:
    manifest_path = out_root / "_manifest" / "universe_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def _symbol_list(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, dict):
        raw = value.get("symbols", [])
        values = raw if isinstance(raw, list) else []
    elif isinstance(value, list | tuple | set):
        values = list(value)
    symbols = [str(item).strip().upper() for item in values if str(item).strip()]
    return sorted(dict.fromkeys(symbols))


def _failure_list(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        raw_items = value.get("items", value.get("symbols", []))
    else:
        raw_items = value
    if not isinstance(raw_items, list | tuple | set):
        return []
    failures: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            symbol = str(item.get("symbol", "")).strip().upper()
            reason = str(item.get("reason", item.get("error", ""))).strip()
        else:
            symbol = str(item).strip().upper()
            reason = ""
        if symbol:
            failures.append({"symbol": symbol, "reason": reason})
    return sorted(failures, key=lambda item: item["symbol"])


def _symbols_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row["symbol"]) for row in rows})


def _json_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return str(value)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
