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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    event_time = _resolve_event_time(args.event_time)
    available_at = event_time
    source_updated_at = datetime.now(tz=UTC)

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
    for payload in payloads:
        market = str(payload.get("market", "")).upper()
        if market not in {"SH", "SZ", "BJ"}:
            raise SystemExit(f"security-list payload missing/invalid market: {payload.get('market')!r}")
        items = payload.get("items", [])
        per_market_counts[market] = len(items)
        for item in items:
            row = _normalize_item(
                item,
                market=market,
                event_time=event_time,
                available_at=available_at,
                source_updated_at=source_updated_at,
            )
            rows.append(row)

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
        "honest_gaps": [
            "is_suspended derived as False; needs SSE/SZSE 停牌名单 cross-check",
            "limit_up/limit_down derived as False; needs same-day quote feed",
            "is_st derived from name prefix; historical ST state changes need 公告 backfill",
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
    available_at: datetime,
    source_updated_at: datetime,
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
    return {
        "symbol": symbol,
        "market": market,
        "event_time": event_time,
        "available_at": available_at,
        "source_updated_at": source_updated_at,
        "is_st": is_st,
        "is_suspended": False,
        "limit_up": False,
        "limit_down": False,
        "listed_days": 0,
        "is_tradable": not (is_st and is_delisted) and not is_delisted,
        "reason": reason,
    }


def _detect_st(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("ST") or upper.startswith("*ST") or "ST" in upper.split()[:1]


def _detect_delisted(name: str) -> bool:
    return "退" in name


if __name__ == "__main__":
    raise SystemExit(main())
