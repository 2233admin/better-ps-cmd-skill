"""Ingest OKX swap open interest into PIT parquet lake.

Snapshot (current):  python-okx PublicData.get_open_interest()
Historical:          python-okx TradingData.get_open_interest_history(instId=...)
                     endpoint /api/v5/rubik/stat/contracts/open-interest-volume
                     raw format: [ts_ms, oi_contracts, oi_base_ccy, oi_usd]

Writes to:
  <out-root>/crypto/open_interest_pit/venue=okx/market_type=swap/inst_id=<SYM>/<DATE>.parquet

XAR-426: production rollout of XAR-423 ccxt + python-okx spike.
Gap note: historical OI via TradingData.get_open_interest_history() wired here (spike gap #2 addressed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import polars as pl
from okx import TradingData

from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter
from app.markets.crypto.okx_pit_writer import (
    OPEN_INTEREST_COLUMNS,
    _now_utc,
    _ms_to_dt,
    validate_pit_invariants,
    backfill_open_interest,
)

UNIVERSE = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]


def _fetch_oi_history(
    td: TradingData.TradingDataAPI,
    inst_id: str,
    limit: int = 90,
) -> list[dict]:
    """Fetch historical OI via TradingData.get_open_interest_history.

    Raw row format: [ts_ms, oi_contracts, oi_base_ccy, oi_usd]
    Confirmed by live probe 2026-05-18.
    """
    r = td.get_open_interest_history(instId=inst_id, limit=str(limit))
    if r.get("code") != "0":
        print(f"  [WARN] get_open_interest_history {inst_id}: code={r.get('code')} msg={r.get('msg')}")
        return []
    raw_data = r.get("data", [])
    rows = []
    for item in raw_data:
        try:
            ts_ms = int(item[0])
            oi_contracts = float(item[1])
            oi_base_ccy = float(item[2])
            oi_usd = float(item[3])
        except (IndexError, ValueError):
            continue
        event_ts = _ms_to_dt(ts_ms)
        rows.append({
            "inst_id": inst_id,
            "venue": "okx",
            "market_type": "swap",
            "event_time": event_ts,
            "available_at": event_ts,  # historical — available at event time
            "source_updated_at": None,  # filled at write time
            "oi_contracts": oi_contracts,
            "oi_base_ccy": oi_base_ccy,
            "oi_usd": oi_usd,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True, help="Crypto lake root dir")
    parser.add_argument(
        "--symbols",
        default=",".join(UNIVERSE),
        help="Comma-separated OKX inst_ids",
    )
    parser.add_argument(
        "--mode", choices=["snapshot", "history"], default="history",
        help="snapshot=current point-in-time; history=via get_open_interest_history()",
    )
    parser.add_argument(
        "--limit", type=int, default=90,
        help="Rows to fetch in history mode (max ~90 per OKX default window)",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    adapter = OKXCCXTAdapter()
    td = TradingData.TradingDataAPI(flag="0")

    total_written = 0
    for sym in symbols:
        print(f"\n[open_interest] {sym} (mode={args.mode})")
        out_dir = (
            out_root / "crypto" / "open_interest_pit"
            / "venue=okx" / "market_type=swap" / f"inst_id={sym}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        ingest_ts = _now_utc()
        date_tag = ingest_ts.strftime("%Y%m%d_%H%M%S")

        if args.mode == "snapshot":
            df = backfill_open_interest(sym, out_dir, adapter=adapter)
            print(f"  -> {len(df)} rows (snapshot)")
            total_written += len(df)
        else:
            rows = _fetch_oi_history(td, sym, limit=args.limit)
            if not rows:
                print(f"  [WARN] no historical OI data for {sym}")
                continue
            for row in rows:
                row["source_updated_at"] = ingest_ts

            df = pl.DataFrame(rows).with_columns([
                pl.col("event_time").cast(pl.Datetime("us", "UTC")),
                pl.col("available_at").cast(pl.Datetime("us", "UTC")),
                pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
            ]).sort("event_time")

            violations = validate_pit_invariants(df, f"open_interest/{sym}")
            if violations:
                for v in violations:
                    print(f"  [WARN] PIT: {v}")

            out_path = out_dir / f"open_interest_{date_tag}.parquet"
            df.write_parquet(out_path)
            print(f"  -> {len(df)} rows, oi_usd={df['oi_usd'][-1]:.0f} -> {out_path.name}")
            total_written += len(df)

    print(f"\nDone. {total_written} rows written across {len(symbols)} symbol(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
