"""Ingest A-share xdxr (除权除息) records from tdx-cli into PIT parquet lake.

Wraps `tdx-cli xdxr <code> --json` per symbol. Emits two PIT datasets:

- ashare.adjustment_factor_pit: per-event qfq multiplier (denominator =
  1 + bonus_ratio + rights_ratio); raw cash/rights kept in corporate action
  dataset so consumers can pick the convention.
- ashare.corporate_action_pit: raw event records (category, cash, rights price,
  bonus ratio, rights ratio) keyed by symbol + effective date.

`available_at` defaults to the effective date (xdxr date in TDX). Real
announcement-date lookahead requires a 公告 feed and is tracked as an honest
gap in the universe manifest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl


CATEGORY_LABELS = {
    1: "chuquan_chuxi",
    2: "songpei_shangshi",
    3: "feiliutong_gu",
    4: "unknown_guben",
    5: "guben_bianhua",
    6: "zengfa_xingu",
    7: "gupiao_huigou",
    8: "zengfa_shangshi",
    9: "zhuanpei_shangshi",
    10: "kozhuan_shangshi",
    11: "kuosuo_gu",
    12: "suogu",
    13: "song_renzhengquan",
    14: "song_renguzhengquan",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-cli", help="Path to tdx-cli executable")
    parser.add_argument("--symbols", help="Comma-separated canonical symbols, e.g. 600000.SH,000001.SZ")
    parser.add_argument(
        "--xdxr-json",
        action="append",
        default=[],
        help="Pre-captured tdx-cli xdxr JSON (repeat per symbol). "
        "Used instead of invoking tdx-cli; primary path for fixtures + tests.",
    )
    parser.add_argument(
        "--xdxr-cache-dir",
        help="tdx-cli xdxr-cache refresh Parquet directory. "
        "Used for full-market batch ingest.",
    )
    parser.add_argument("--out-root", required=True, help="A-share lake root")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    source_updated_at = datetime.now(tz=UTC)

    payloads: list[dict[str, Any]] = []
    sources: list[str] = []
    cache_dir = Path(args.xdxr_cache_dir) if args.xdxr_cache_dir else None
    if args.xdxr_json:
        for raw in args.xdxr_json:
            payload = json.loads(Path(raw).read_text(encoding="utf-8"))
            payloads.append(payload)
            sources.append(f"file:{raw}")
    elif cache_dir is not None:
        if not cache_dir.exists():
            raise SystemExit(f"xdxr cache dir not found: {cache_dir}")
        sources.append(f"tdx-cli:xdxr-cache:{cache_dir}")
    else:
        if not args.tdx_cli:
            raise SystemExit("--tdx-cli is required unless --xdxr-json is provided")
        if not args.symbols:
            raise SystemExit("--symbols is required unless --xdxr-json is provided")
        tdx_cli = Path(args.tdx_cli)
        if not tdx_cli.exists():
            raise SystemExit(f"tdx-cli not found: {tdx_cli}")
        symbols = tuple(
            symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()
        )
        for symbol in symbols:
            code, market = _split_symbol(symbol)
            payload = _run_tdxcli(tdx_cli, code)
            payload["__symbol__"] = symbol
            payload["__market__"] = market
            payloads.append(payload)
            sources.append(f"tdx-cli:xdxr:{symbol}")

    adjustment_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    per_symbol_counts: dict[str, int] = {}
    for payload in payloads:
        symbol, market = _resolve_payload_symbol(payload)
        items = payload.get("items", [])
        per_symbol_counts[symbol] = len(items)
        for item in items:
            _append_xdxr_item(
                adjustment_rows,
                action_rows,
                symbol=symbol,
                market=market,
                item=item,
                source_updated_at=source_updated_at,
            )

    if cache_dir is not None:
        for row in _read_cache_rows(cache_dir):
            market = _canonical_market(row.get("market"))
            code = str(row.get("code", "")).zfill(6)
            symbol = f"{code}.{market}"
            per_symbol_counts[symbol] = per_symbol_counts.get(symbol, 0) + 1
            _append_xdxr_item(
                adjustment_rows,
                action_rows,
                symbol=symbol,
                market=market,
                item=_cache_row_to_item(row),
                source_updated_at=source_updated_at,
            )

    if not action_rows:
        raise SystemExit("xdxr returned no rows for any symbol")

    adjustment_frame = _frame(adjustment_rows, _adjustment_schema()).sort(["symbol", "event_time"])
    adjustment_path = out_root / "pit" / "adjustment_factor_pit.parquet"
    adjustment_path.parent.mkdir(parents=True, exist_ok=True)
    adjustment_frame.write_parquet(adjustment_path)

    action_frame = _frame(action_rows, _action_schema()).sort(["symbol", "event_time"])
    action_path = out_root / "pit" / "corporate_action_pit.parquet"
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_frame.write_parquet(action_path)

    universe = {
        "source": sources,
        "rows": {
            "adjustment_factor": adjustment_frame.height,
            "corporate_action": action_frame.height,
        },
        "per_symbol_counts": per_symbol_counts,
        "honest_gaps": [
            "available_at defaults to effective date; real announcement_date needs 公告 feed",
            "corporate_action category mapping covers TDX 14 enum values; novel category_code surfaces as 'unknown'",
            "qfq_denominator factor is emitted for chuquan_chuxi events only; consumer must compose chronologically for cumulative adjustment",
        ],
    }
    universe_path = out_root / "_manifest" / "xdxr_universe.json"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_text(
        json.dumps(universe, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    summary = {
        "datasets": [
            {"name": "ashare.adjustment_factor_pit", "path": str(adjustment_path), "rows": adjustment_frame.height},
            {"name": "ashare.corporate_action_pit", "path": str(action_path), "rows": action_frame.height},
        ],
        "per_symbol_counts": per_symbol_counts,
        "universe_manifest": str(universe_path),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(
            f"wrote {adjustment_frame.height} adjustment rows + "
            f"{action_frame.height} corporate-action rows"
        )
    return 0


def _split_symbol(symbol: str) -> tuple[str, str]:
    if "." not in symbol:
        raise SystemExit(f"symbol must be canonical CODE.MARKET: {symbol}")
    code, market = symbol.split(".", 1)
    market = market.upper()
    if market not in {"SH", "SZ", "BJ"}:
        raise SystemExit(f"unsupported market suffix: {symbol}")
    return code, market


def _run_tdxcli(tdx_cli: Path, code: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(tdx_cli), "xdxr", code, "--json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _resolve_payload_symbol(payload: dict[str, Any]) -> tuple[str, str]:
    if payload.get("__symbol__"):
        return str(payload["__symbol__"]).upper(), str(payload["__market__"]).upper()
    code = str(payload.get("code", "")).zfill(6)
    market = str(payload.get("market", "")).upper()
    if market not in {"SH", "SZ", "BJ"}:
        raise SystemExit(f"xdxr payload missing/invalid market: {payload.get('market')!r}")
    return f"{code}.{market}", market


def _append_xdxr_item(
    adjustment_rows: list[dict[str, Any]],
    action_rows: list[dict[str, Any]],
    *,
    symbol: str,
    market: str,
    item: dict[str, Any],
    source_updated_at: datetime,
) -> None:
    event_time = _parse_event_time(item)
    available_at = event_time
    category_code = _category_code(item.get("category"))
    cash = float(item.get("fh_qltp", 0.0) or 0.0)
    rights_price = float(item.get("pgj_qzgb", 0.0) or 0.0)
    bonus_ratio = float(item.get("sg_hltp", 0.0) or 0.0)
    rights_ratio = float(item.get("pg_hzgb", 0.0) or 0.0)
    if category_code == 1:
        adjustment_rows.append(
            {
                "symbol": symbol,
                "market": market,
                "event_time": event_time,
                "available_at": available_at,
                "source_updated_at": source_updated_at,
                "adjustment_type": "qfq_denominator",
                "factor": 1.0 + bonus_ratio + rights_ratio,
            }
        )
    action_rows.append(
        {
            "symbol": symbol,
            "market": market,
            "event_time": event_time,
            "available_at": available_at,
            "source_updated_at": source_updated_at,
            "category_code": category_code,
            "category": CATEGORY_LABELS.get(category_code, "unknown"),
            "cash_per_share": cash,
            "rights_price": rights_price,
            "bonus_ratio": bonus_ratio,
            "rights_ratio": rights_ratio,
        }
    )


def _parse_event_time(item: dict[str, Any]) -> datetime:
    raw_date = item.get("date")
    if isinstance(raw_date, int):
        year = raw_date // 10000
        month = (raw_date // 100) % 100
        day = raw_date % 100
    elif isinstance(raw_date, str):
        parsed = datetime.fromisoformat(raw_date)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    elif isinstance(raw_date, date):
        return datetime(raw_date.year, raw_date.month, raw_date.day, tzinfo=UTC)
    else:
        raise SystemExit(f"xdxr item has unparseable date: {raw_date!r}")
    return datetime(year, month, day, tzinfo=UTC)


def _category_code(raw: Any) -> int:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        normalized = _normalize_category(raw)
        for code, label in CATEGORY_LABELS.items():
            if _normalize_category(label) == normalized:
                return code
        return 0
    if isinstance(raw, dict) and "code" in raw:
        return int(raw["code"])
    return 0


def _normalize_category(raw: str) -> str:
    return raw.replace("_", "").replace("-", "").lower()


def _read_cache_rows(cache_dir: Path) -> list[dict[str, Any]]:
    files = sorted(
        path
        for path in cache_dir.rglob("*.parquet")
        if not path.name.startswith("_")
    )
    if not files:
        raise SystemExit(f"xdxr cache dir contains no parquet parts: {cache_dir}")
    return pl.read_parquet(files).iter_rows(named=True)


def _cache_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": row.get("date"),
        "category": row.get("category"),
        "fh_qltp": _scaled_i64(row.get("fh_qltp_i64")),
        "pgj_qzgb": _scaled_i64(row.get("pgj_qzgb_i64")),
        "sg_hltp": _scaled_i64(row.get("sg_hltp_i64")),
        "pg_hzgb": _scaled_i64(row.get("pg_hzgb_i64")),
    }


def _scaled_i64(raw: Any) -> float:
    if raw is None:
        return 0.0
    return float(raw) / 10000.0


def _canonical_market(raw: Any) -> str:
    text = str(raw).upper()
    if text in {"1", "SH"}:
        return "SH"
    if text in {"2", "BJ"}:
        return "BJ"
    if text in {"0", "SZ"}:
        return "SZ"
    raise SystemExit(f"unsupported xdxr market: {raw!r}")


def _frame(rows: list[dict[str, Any]], schema: dict[str, Any]) -> pl.DataFrame:
    if rows:
        return pl.DataFrame(rows, schema=schema)
    return pl.DataFrame(schema=schema)


def _adjustment_schema() -> dict[str, Any]:
    return {
        "symbol": pl.Utf8,
        "market": pl.Utf8,
        "event_time": pl.Datetime(time_zone="UTC"),
        "available_at": pl.Datetime(time_zone="UTC"),
        "source_updated_at": pl.Datetime(time_zone="UTC"),
        "adjustment_type": pl.Utf8,
        "factor": pl.Float64,
    }


def _action_schema() -> dict[str, Any]:
    return {
        "symbol": pl.Utf8,
        "market": pl.Utf8,
        "event_time": pl.Datetime(time_zone="UTC"),
        "available_at": pl.Datetime(time_zone="UTC"),
        "source_updated_at": pl.Datetime(time_zone="UTC"),
        "category_code": pl.Int64,
        "category": pl.Utf8,
        "cash_per_share": pl.Float64,
        "rights_price": pl.Float64,
        "bonus_ratio": pl.Float64,
        "rights_ratio": pl.Float64,
    }


if __name__ == "__main__":
    raise SystemExit(main())
