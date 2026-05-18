"""Download useful raw A-share datasets from tdx-cli.

This script is intentionally raw-layer only. It captures the current TDX
finance snapshot and TDX block files with enough per-item metadata to make the
run resumable and auditable. Promotion into normalized/PIT datasets should be a
separate explicit step because financial statements need report-period and
announcement-time handling before they are research-eligible.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import polars as pl


DEFAULT_TDX_CLI = Path(r"D:\projects\tdx\tdxcli-rs\target\debug\tdx-cli.exe")
DEFAULT_BLOCK_FILES = ("block.dat", "block_gn.dat", "block_fg.dat", "block_zs.dat")


@dataclass(frozen=True)
class SymbolItem:
    symbol: str
    code: str
    market: str
    is_tradable: bool | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-cli", default=str(DEFAULT_TDX_CLI), help="Path to tdx-cli executable")
    parser.add_argument("--data-root", default="DATA/Ashare", help="A-share data root")
    parser.add_argument("--run-id", help="Run id suffix, defaults to YYYYMMDD")
    parser.add_argument(
        "--symbols-file",
        help="Optional text file with one canonical symbol per line. "
        "Defaults to pit/tradability_status_pit.parquet.",
    )
    parser.add_argument(
        "--symbol-scope",
        choices=("all", "tradable"),
        default="all",
        help="Use all known PIT symbols or only currently tradable symbols.",
    )
    parser.add_argument("--limit", type=int, help="Limit symbols for a smoke run")
    parser.add_argument("--workers", type=int, default=4, help="Parallel finance workers")
    parser.add_argument("--timeout", type=int, default=45, help="tdx-cli timeout per request, seconds")
    parser.add_argument(
        "--block-files",
        default=",".join(DEFAULT_BLOCK_FILES),
        help="Comma-separated TDX block files to download",
    )
    parser.add_argument("--skip-finance", action="store_true", help="Do not download finance snapshots")
    parser.add_argument("--skip-blocks", action="store_true", help="Do not download block files")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    tdx_cli = Path(args.tdx_cli)
    if not tdx_cli.exists():
        raise SystemExit(f"tdx-cli not found: {tdx_cli}")

    data_root = Path(args.data_root)
    raw_root = data_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or datetime.now(tz=UTC).strftime("%Y%m%d")
    downloaded_at = datetime.now(tz=UTC)

    symbols = _load_symbols(
        data_root=data_root,
        symbols_file=Path(args.symbols_file) if args.symbols_file else None,
        scope=args.symbol_scope,
        limit=args.limit,
    )

    summary: dict[str, Any] = {
        "run_id": run_id,
        "downloaded_at": downloaded_at.isoformat(),
        "symbol_scope": args.symbol_scope,
        "symbol_count": len(symbols),
        "finance": None,
        "blocks": None,
    }

    if not args.skip_blocks:
        block_files = tuple(part.strip() for part in args.block_files.split(",") if part.strip())
        summary["blocks"] = download_blocks(
            tdx_cli=tdx_cli,
            raw_root=raw_root,
            run_id=run_id,
            block_files=block_files,
            timeout_seconds=args.timeout,
            downloaded_at=downloaded_at,
            force=args.force,
        )

    if not args.skip_finance:
        summary["finance"] = download_finance(
            tdx_cli=tdx_cli,
            raw_root=raw_root,
            run_id=run_id,
            symbols=symbols,
            workers=max(1, args.workers),
            timeout_seconds=args.timeout,
            downloaded_at=downloaded_at,
            force=args.force,
        )

    manifest_path = raw_root / f"tdx_raw_{run_id}_manifest.json"
    manifest_path.write_text(
        json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    summary["manifest_path"] = str(manifest_path)

    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote raw TDX manifest: {manifest_path}")
    return 0


def _load_symbols(
    *,
    data_root: Path,
    symbols_file: Path | None,
    scope: str,
    limit: int | None,
) -> list[SymbolItem]:
    if symbols_file is not None:
        symbols = []
        for line in symbols_file.read_text(encoding="utf-8").splitlines():
            symbol = line.strip().upper()
            if not symbol or symbol.startswith("#"):
                continue
            code, market = _split_symbol(symbol)
            symbols.append(SymbolItem(symbol=symbol, code=code, market=market))
        return symbols[:limit] if limit else symbols

    status_path = data_root / "pit" / "tradability_status_pit.parquet"
    if not status_path.exists():
        raise SystemExit(f"tradability status not found: {status_path}")

    lazy = pl.scan_parquet(status_path)
    columns = set(lazy.collect_schema().names())
    needed = ["symbol"]
    if "market" in columns:
        needed.append("market")
    if "is_tradable" in columns:
        needed.append("is_tradable")
    frame = lazy.select(needed).unique(subset=["symbol"], keep="last").collect()
    if scope == "tradable" and "is_tradable" in frame.columns:
        frame = frame.filter(pl.col("is_tradable") == True)

    items: list[SymbolItem] = []
    for row in frame.sort("symbol").iter_rows(named=True):
        symbol = str(row["symbol"]).upper()
        code, suffix_market = _split_symbol(symbol)
        market = str(row.get("market") or suffix_market).upper()
        is_tradable = row.get("is_tradable")
        items.append(
            SymbolItem(
                symbol=symbol,
                code=code,
                market=market,
                is_tradable=bool(is_tradable) if is_tradable is not None else None,
            )
        )
    return items[:limit] if limit else items


def _split_symbol(symbol: str) -> tuple[str, str]:
    if "." not in symbol:
        raise SystemExit(f"symbol must be CODE.MARKET: {symbol}")
    code, market = symbol.split(".", 1)
    code = code.strip().zfill(6)
    market = market.strip().upper()
    if market not in {"SH", "SZ", "BJ"}:
        raise SystemExit(f"unsupported market suffix: {symbol}")
    return code, market


def download_blocks(
    *,
    tdx_cli: Path,
    raw_root: Path,
    run_id: str,
    block_files: Iterable[str],
    timeout_seconds: int,
    downloaded_at: datetime,
    force: bool,
) -> dict[str, Any]:
    out_dir = raw_root / f"tdx_blocks_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for block_file in block_files:
        path = out_dir / f"{block_file}.json"
        if path.exists() and not force:
            status = _existing_file_result(block_file, path)
            results.append(status)
            continue
        result = _run_json([str(tdx_cli), "block-info", block_file, "--json"], timeout_seconds)
        if result["ok"]:
            payload = {
                "dataset": "tdx.block_info",
                "block_file": block_file,
                "downloaded_at": downloaded_at.isoformat(),
                "command": ["tdx-cli", "block-info", block_file, "--json"],
                "tdx_payload": result["payload"],
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            results.append(
                {
                    "block_file": block_file,
                    "ok": True,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "item_count": _payload_item_count(result["payload"]),
                }
            )
        else:
            error_path = out_dir / f"{block_file}.error.json"
            error_payload = {
                "dataset": "tdx.block_info",
                "block_file": block_file,
                "downloaded_at": downloaded_at.isoformat(),
                "command": ["tdx-cli", "block-info", block_file, "--json"],
                "error": result,
            }
            error_path.write_text(
                json.dumps(error_payload, ensure_ascii=True, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            results.append(
                {
                    "block_file": block_file,
                    "ok": False,
                    "path": str(error_path),
                    "error": result.get("error") or result.get("stderr") or "tdx-cli failed",
                }
            )

    summary = {
        "path": str(out_dir),
        "requested": len(tuple(block_files)),
        "success": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "items": results,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return summary


def _existing_file_result(key: str, path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        item_count = _payload_item_count(payload.get("tdx_payload", payload))
    except Exception:
        item_count = None
    return {
        "block_file": key,
        "ok": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "item_count": item_count,
        "skipped_existing": True,
    }


def download_finance(
    *,
    tdx_cli: Path,
    raw_root: Path,
    run_id: str,
    symbols: list[SymbolItem],
    workers: int,
    timeout_seconds: int,
    downloaded_at: datetime,
    force: bool,
) -> dict[str, Any]:
    out_dir = raw_root / f"tdx_finance_{run_id}"
    item_dir = out_dir / "items"
    error_dir = out_dir / "errors"
    item_dir.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _download_one_finance,
                tdx_cli,
                item_dir,
                error_dir,
                symbol,
                timeout_seconds,
                downloaded_at,
                force,
            )
            for symbol in symbols
        ]
        for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if idx % 250 == 0:
                print(
                    json.dumps(
                        {
                            "finance_progress": idx,
                            "total": len(symbols),
                            "success": sum(1 for item in results if item["ok"]),
                            "failed": sum(1 for item in results if not item["ok"]),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    flush=True,
                )

    results.sort(key=lambda item: item["symbol"])
    index_path = out_dir / "finance_index.jsonl"
    with index_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")

    summary = {
        "path": str(out_dir),
        "item_dir": str(item_dir),
        "error_dir": str(error_dir),
        "index_path": str(index_path),
        "requested": len(symbols),
        "success": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "skipped_existing": sum(1 for item in results if item.get("skipped_existing")),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return summary


def _download_one_finance(
    tdx_cli: Path,
    item_dir: Path,
    error_dir: Path,
    symbol: SymbolItem,
    timeout_seconds: int,
    downloaded_at: datetime,
    force: bool,
) -> dict[str, Any]:
    out_path = item_dir / f"{symbol.symbol}.json"
    if out_path.exists() and not force:
        return {
            "symbol": symbol.symbol,
            "code": symbol.code,
            "market": symbol.market,
            "is_tradable": symbol.is_tradable,
            "ok": True,
            "path": str(out_path),
            "bytes": out_path.stat().st_size,
            "skipped_existing": True,
        }

    result = _run_json([str(tdx_cli), "finance", symbol.code, "--json"], timeout_seconds)
    if result["ok"]:
        payload = {
            "dataset": "tdx.finance_snapshot",
            "symbol": symbol.symbol,
            "code": symbol.code,
            "market": symbol.market,
            "is_tradable": symbol.is_tradable,
            "downloaded_at": downloaded_at.isoformat(),
            "command": ["tdx-cli", "finance", symbol.code, "--json"],
            "tdx_payload": result["payload"],
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return {
            "symbol": symbol.symbol,
            "code": symbol.code,
            "market": symbol.market,
            "is_tradable": symbol.is_tradable,
            "ok": True,
            "path": str(out_path),
            "bytes": out_path.stat().st_size,
        }

    error_path = error_dir / f"{symbol.symbol}.json"
    error_payload = {
        "dataset": "tdx.finance_snapshot",
        "symbol": symbol.symbol,
        "code": symbol.code,
        "market": symbol.market,
        "is_tradable": symbol.is_tradable,
        "downloaded_at": downloaded_at.isoformat(),
        "command": ["tdx-cli", "finance", symbol.code, "--json"],
        "error": result,
    }
    error_path.write_text(
        json.dumps(error_payload, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return {
        "symbol": symbol.symbol,
        "code": symbol.code,
        "market": symbol.market,
        "is_tradable": symbol.is_tradable,
        "ok": False,
        "path": str(error_path),
        "error": result.get("error") or result.get("stderr") or "tdx-cli failed",
    }


def _run_json(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "error": f"timeout after {timeout_seconds}s",
            "stdout": _truncate(exc.stdout),
            "stderr": _truncate(exc.stderr),
        }

    if proc.returncode != 0:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": _truncate(proc.stdout),
            "stderr": _truncate(proc.stderr),
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "error": f"invalid json: {exc}",
            "stdout": _truncate(proc.stdout),
            "stderr": _truncate(proc.stderr),
        }
    return {"ok": True, "returncode": proc.returncode, "payload": payload}


def _truncate(value: str | bytes | None, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _payload_item_count(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("items", "entries", "blocks", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return None
    if isinstance(payload, list):
        return len(payload)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
