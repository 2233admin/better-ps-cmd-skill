"""Ingest OKX swap mark price into PIT parquet lake.

Snapshot-only: python-okx PublicData.get_mark_price() returns current snapshot.
For continuous PIT coverage, run this script on a schedule (e.g. every 8 hours).

Writes to:
  <out-root>/crypto/mark_price_pit/venue=okx/market_type=swap/inst_id=<SYM>/<DATE>.parquet

XAR-426: production rollout of XAR-423 ccxt + python-okx spike.
Gap note: no historical mark price endpoint on OKX public API — snapshot polling only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter
from app.markets.crypto.okx_pit_writer import backfill_mark_price

UNIVERSE = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True, help="Crypto lake root dir")
    parser.add_argument(
        "--symbols",
        default=",".join(UNIVERSE),
        help="Comma-separated OKX inst_ids",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    adapter = OKXCCXTAdapter()

    total_written = 0
    for sym in symbols:
        print(f"\n[mark_price] {sym}")
        out_dir = (
            out_root / "crypto" / "mark_price_pit"
            / "venue=okx" / "market_type=swap" / f"inst_id={sym}"
        )
        df = backfill_mark_price(sym, out_dir, adapter=adapter)
        print(f"  -> {len(df)} rows, mark_price={df['mark_price'][0] if len(df) else 'n/a'}")
        total_written += len(df)

    print(f"\nDone. {total_written} rows written across {len(symbols)} symbol(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
