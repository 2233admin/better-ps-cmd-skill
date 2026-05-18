"""Ingest OKX swap funding rate history into PIT parquet lake.

Writes to:
  <out-root>/crypto/funding_rate_pit/venue=okx/market_type=swap/inst_id=<SYM>/<DATE>.parquet

Pagination: OKX returns max 100 rows per call (8h interval = ~100 rows/month).
--months controls how many months back to backfill via before/after cursor loop.

XAR-426: production rollout of XAR-423 ccxt + python-okx spike.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure backend package is importable when run from project root
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter
from app.markets.crypto.okx_pit_writer import backfill_funding_rate

UNIVERSE = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]


def _fetch_one_page(
    adapter: OKXCCXTAdapter,
    inst_id: str,
    out_dir: Path,
    before_ms: str | None,
    after_ms: str | None,
) -> tuple[int, str | None]:
    """Fetch one page (up to 100 rows). Returns (row_count, oldest_fundingTime_ms).

    oldest_fundingTime_ms is used as `before` cursor for next page.
    """
    import polars as pl
    from app.markets.crypto.okx_pit_writer import FUNDING_RATE_COLUMNS, _funding_row, _now_utc, validate_pit_invariants

    raw_rows = adapter.get_funding_rate_history(
        inst_id,
        before=before_ms,
        after=after_ms,
        limit=100,
    )
    if not raw_rows:
        return 0, None

    ingest_ts = _now_utc()
    rows = [_funding_row(r, ingest_ts) for r in raw_rows]
    df = pl.DataFrame(rows).with_columns([
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
    ]).sort("event_time")

    violations = validate_pit_invariants(df, "funding_rate")
    if violations:
        for v in violations:
            print(f"  [WARN] PIT violation: {v}")

    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = ingest_ts.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"funding_rate_{date_tag}.parquet"
    df.write_parquet(out_path)
    print(f"  -> {len(df)} rows -> {out_path.name}")

    # Oldest row gives the before cursor for the next older page
    oldest_ts = str(raw_rows[-1]["fundingTime"])
    return len(df), oldest_ts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True, help="Crypto lake root dir")
    parser.add_argument(
        "--symbols",
        default=",".join(UNIVERSE),
        help="Comma-separated OKX inst_ids (default: BTC/ETH/SOL swap)",
    )
    parser.add_argument(
        "--months", type=int, default=1,
        help="Number of months to backfill (each month = up to 1 API page of 100 rows)",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.out_root)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    adapter = OKXCCXTAdapter()

    cutoff = datetime.now(tz=UTC) - timedelta(days=30 * args.months)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    total_written = 0
    for sym in symbols:
        print(f"\n[funding] {sym}")
        out_dir = out_root / "crypto" / "funding_rate_pit" / "venue=okx" / "market_type=swap" / f"inst_id={sym}"

        before_ms: str | None = None
        pages = 0
        sym_rows = 0
        while True:
            n, oldest = _fetch_one_page(adapter, sym, out_dir, before_ms=before_ms, after_ms=None)
            sym_rows += n
            pages += 1
            if n == 0 or oldest is None:
                break
            if int(oldest) <= cutoff_ms:
                print(f"  reached cutoff ({cutoff.date()}), stopping pagination")
                break
            before_ms = oldest
            if pages >= args.months * 2:  # safety: max 2 pages per month
                print(f"  safety page limit ({pages}) reached")
                break

        print(f"  {sym}: {sym_rows} rows total ({pages} page(s))")
        total_written += sym_rows

    print(f"\nDone. {total_written} rows written across {len(symbols)} symbol(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
