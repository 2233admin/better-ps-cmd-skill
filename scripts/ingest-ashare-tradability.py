"""Ingest A-share tradability status from tdx-cli into a PIT parquet lake.

Wraps `tdx-cli security-list <market> --all --json`. Derives is_st / is_delisted
from the security name. Suspension and limit-up/down flags require quote-side
data (a separate ticker/feed) and are emitted as False with a low-fidelity
reason note. Honest gaps are tracked in the universe manifest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl


SUPPORTED_MARKETS = ("sh", "sz", "bj")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-cli", help="Path to tdx-cli executable")
    parser.add_argument(
        "--security-list-json",
        action="append",
        default=[],
        help="Pre-captured tdx-cli security-list JSON (repeat per market). "
        "Used instead of invoking tdx-cli; primary path for fixtures + tests.",
    )
    parser.add_argument(
        "--markets",
        default=",".join(SUPPORTED_MARKETS),
        help="Comma-separated markets to ingest (sh,sz,bj)",
    )
    parser.add_argument("--out-root", required=True, help="A-share lake root")
    parser.add_argument("--event-time", help="Snapshot event_time ISO date (defaults to today UTC)")
    parser.add_argument("--duckdb-path", help="Optional A-share DuckDB file used for listing age and inactive history")
    parser.add_argument(
        "--equity-only",
        action="store_true",
        help="Keep only A-share equity code ranges from security-list payloads",
    )
    parser.add_argument(
        "--include-history-inactive",
        action="store_true",
        help="Append historical symbols from tdx_daily that are missing from the current security list as non-tradable",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    event_time = _resolve_event_time(args.event_time)
    event_date = event_time.date()
    available_at = event_time
    source_updated_at = datetime.now(tz=UTC)
    history = _read_history(args.duckdb_path, out_root) if args.duckdb_path or args.include_history_inactive else {}

    payloads: list[dict[str, Any]] = []
    sources: list[str] = []
    if args.security_list_json:
        for raw in args.security_list_json:
            payload = json.loads(Path(raw).read_text(encoding="utf-8"))
            payloads.append(payload)
            sources.append(f"file:{raw}")
    else:
        if not args.tdx_cli:
            raise SystemExit("--tdx-cli is required unless --security-list-json is provided")
        tdx_cli = Path(args.tdx_cli)
        if not tdx_cli.exists():
            raise SystemExit(f"tdx-cli not found: {tdx_cli}")
        markets = tuple(part.strip().lower() for part in args.markets.split(",") if part.strip())
        for market in markets:
            if market not in SUPPORTED_MARKETS:
                raise SystemExit(f"unsupported market: {market}")
            payload = _run_tdxcli(tdx_cli, market)
            payloads.append(payload)
            sources.append(f"tdx-cli:security-list:{market}")

    rows: list[dict[str, Any]] = []
    per_market_counts: dict[str, int] = {}
    filtered_counts: dict[str, int] = {}
    seen_symbols: set[str] = set()
    for payload in payloads:
        market = str(payload.get("market", "")).upper()
        if market not in {"SH", "SZ", "BJ"}:
            raise SystemExit(f"security-list payload missing/invalid market: {payload.get('market')!r}")
        items = payload.get("items", [])
        per_market_counts[market] = len(items)
        for item in items:
            code = str(item.get("code", "")).strip().zfill(6)
            if args.equity_only and not _is_equity_symbol(code, market):
                filtered_counts[market] = filtered_counts.get(market, 0) + 1
                continue
            row = _normalize_item(
                item,
                market=market,
                event_time=event_time,
                event_date=event_date,
                available_at=available_at,
                source_updated_at=source_updated_at,
                history=history,
            )
            rows.append(row)
            seen_symbols.add(str(row["symbol"]))

    inactive_rows = []
    if args.include_history_inactive:
        for symbol, item in sorted(history.items()):
            if symbol in seen_symbols:
                continue
            if args.equity_only and not _is_equity_symbol(symbol[:6], symbol[-2:]):
                continue
            row = _inactive_history_row(
                symbol=symbol,
                event_time=event_time,
                event_date=event_date,
                available_at=available_at,
                source_updated_at=source_updated_at,
                history_item=item,
            )
            inactive_rows.append(row)
        rows.extend(inactive_rows)

    if not rows:
        raise SystemExit("security-list returned no rows for any market")

    frame = pl.DataFrame(rows).sort(["symbol", "event_time"])
    dataset_path = out_root / "pit" / "tradability_status_pit.parquet"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(dataset_path)

    universe = {
        "source": sources,
        "event_time": event_time.isoformat(),
        "rows": frame.height,
        "per_market_counts": per_market_counts,
        "filtered_counts": filtered_counts,
        "history_rows": len(history),
        "inferred_inactive_rows": len(inactive_rows),
        "honest_gaps": [
            "is_suspended inferred from current-list presence plus missing same-day bar; needs SSE/SZSE 停牌名单 cross-check",
            "limit_up/limit_down derived as False; needs same-day quote feed",
            "is_st derived from name prefix; historical ST state changes need 公告 backfill",
            "inactive/delisted history inferred from tdx_daily last_trade_date when official delist_date is unavailable",
        ],
    }
    universe_path = out_root / "_manifest" / "tradability_universe.json"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_text(
        json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    summary = {
        "dataset": "ashare.tradability_status_pit",
        "path": str(dataset_path),
        "rows": frame.height,
        "per_market_counts": per_market_counts,
        "filtered_counts": filtered_counts,
        "history_rows": len(history),
        "inferred_inactive_rows": len(inactive_rows),
        "universe_manifest": str(universe_path),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote {frame.height} rows to {dataset_path}")
    return 0


def _resolve_event_time(raw: str | None) -> datetime:
    if not raw:
        today = datetime.now(tz=UTC).date()
        return datetime(today.year, today.month, today.day, tzinfo=UTC)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _run_tdxcli(tdx_cli: Path, market: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(tdx_cli), "security-list", market, "--all", "--json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _normalize_item(
    item: dict[str, Any],
    *,
    market: str,
    event_time: datetime,
    event_date: date,
    available_at: datetime,
    source_updated_at: datetime,
    history: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    code = str(item.get("code", "")).strip()
    if not code:
        raise SystemExit(f"security-list item missing code: {item!r}")
    name = str(item.get("name", "")).strip()
    is_st = _detect_st(name)
    is_delisted = _detect_delisted(name)
    symbol = f"{code.zfill(6)}.{market}"
    reason_bits: list[str] = []
    if is_st:
        reason_bits.append("st_name_prefix")
    if is_delisted:
        reason_bits.append("delisted_name_marker")
    reason = ",".join(reason_bits) if reason_bits else ""
    history_item = history.get(symbol, {})
    first_trade_date = history_item.get("first_trade_date")
    last_trade_date = history_item.get("last_trade_date")
    has_same_day_bar = last_trade_date == event_date
    missing_same_day_bar = bool(history and not has_same_day_bar and not is_delisted)
    is_suspended = missing_same_day_bar
    if is_suspended:
        reason_bits.append("no_same_day_bar")
    listed_days = _listed_days(first_trade_date, event_date)
    is_tradable = not is_delisted and not is_suspended
    reason = ",".join(reason_bits) if reason_bits else ""
    return {
        "symbol": symbol,
        "market": market,
        "event_time": event_time,
        "available_at": available_at,
        "source_updated_at": source_updated_at,
        "is_st": is_st,
        "is_suspended": is_suspended,
        "limit_up": False,
        "limit_down": False,
        "listed_days": listed_days,
        "is_tradable": is_tradable,
        "reason": reason,
        "first_trade_date": first_trade_date,
        "last_trade_date": last_trade_date,
        "has_same_day_bar": has_same_day_bar,
    }


def _detect_st(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("ST") or upper.startswith("*ST") or "ST" in upper.split()[:1]


def _detect_delisted(name: str) -> bool:
    return "退" in name


def _inactive_history_row(
    *,
    symbol: str,
    event_time: datetime,
    event_date: date,
    available_at: datetime,
    source_updated_at: datetime,
    history_item: dict[str, Any],
) -> dict[str, Any]:
    first_trade_date = history_item.get("first_trade_date")
    last_trade_date = history_item.get("last_trade_date")
    return {
        "symbol": symbol,
        "market": symbol[-2:],
        "event_time": event_time,
        "available_at": available_at,
        "source_updated_at": source_updated_at,
        "is_st": False,
        "is_suspended": False,
        "limit_up": False,
        "limit_down": False,
        "listed_days": _listed_days(first_trade_date, event_date),
        "is_tradable": False,
        "reason": f"inactive_or_delisted_inferred,last_trade_date:{last_trade_date}",
        "first_trade_date": first_trade_date,
        "last_trade_date": last_trade_date,
        "has_same_day_bar": False,
    }


def _read_history(raw_path: str | None, out_root: Path) -> dict[str, dict[str, Any]]:
    db_path = Path(raw_path) if raw_path else out_root / "Aquant.duckdb"
    if not db_path.exists():
        raise SystemExit(f"missing DuckDB history file: {db_path}")
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT symbol, min(date) AS first_trade_date, max(date) AS last_trade_date, count(*) AS rows
            FROM main.tdx_daily
            GROUP BY symbol
            """
        ).fetchall()
    finally:
        conn.close()
    history: dict[str, dict[str, Any]] = {}
    for raw_symbol, first_trade_date, last_trade_date, rows_count in rows:
        symbol = _canonical_from_tdx_symbol(str(raw_symbol))
        if not symbol:
            continue
        history[symbol] = {
            "first_trade_date": first_trade_date,
            "last_trade_date": last_trade_date,
            "rows": int(rows_count),
        }
    return history


def _canonical_from_tdx_symbol(symbol: str) -> str | None:
    symbol = symbol.strip().lower()
    if len(symbol) != 8:
        return None
    market = symbol[:2].upper()
    code = symbol[2:]
    if market not in {"SH", "SZ", "BJ"} or not code.isdigit():
        return None
    return f"{code}.{market}"


def _is_equity_symbol(code: str, market: str) -> bool:
    code = code.zfill(6)
    market = market.upper()
    if market == "SH":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "SZ":
        return code.startswith(("000", "001", "002", "003", "300", "301", "302"))
    if market == "BJ":
        return code.startswith(("430", "83", "87", "88", "920"))
    return False


def _listed_days(first_trade_date: date | None, event_date: date) -> int:
    if first_trade_date is None:
        return 0
    return max((event_date - first_trade_date).days + 1, 0)


if __name__ == "__main__":
    raise SystemExit(main())
