"""CLI for the crypto PIT research pipeline."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .runner import CryptoPipelineConfig, run_crypto_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Session date in YYYY-MM-DD format")
    parser.add_argument("--inst-ids", required=True, help="Comma-separated OKX instruments")
    parser.add_argument("--out", required=True, help="Output artifact directory")
    parser.add_argument("--pit-parquet", help="Optional crypto PIT parquet input; fixture is used if omitted")
    parser.add_argument("--code-commit", default="manual", help="Git commit or explicit code id")
    parser.add_argument("--market-type", choices=("spot", "swap"), default="spot")
    parser.add_argument("--allow-short", action="store_true")
    parser.add_argument("--leverage", type=float, default=1.0)
    args = parser.parse_args(argv)

    run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=date.fromisoformat(args.date),
            inst_ids=tuple(item.strip() for item in args.inst_ids.split(",") if item.strip()),
            out_dir=Path(args.out),
            pit_parquet=Path(args.pit_parquet) if args.pit_parquet else None,
            code_commit=args.code_commit,
            market_type=args.market_type,
            allow_short=args.allow_short,
            leverage=args.leverage,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
