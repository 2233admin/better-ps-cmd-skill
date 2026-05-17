"""CLI for the minimum credible A-share research chain."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .data_lake import resolve_kline_daily_symbols
from .runner import PipelineConfig, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Pipeline date in YYYY-MM-DD format")
    parser.add_argument(
        "--symbols",
        help="Comma-separated A-share symbols; omit with --data-root to scan the lake universe",
    )
    parser.add_argument("--out", required=True, help="Output artifact directory")
    parser.add_argument("--pit-parquet", help="Optional PIT parquet input; fixture is used if omitted")
    parser.add_argument("--data-root", help="Optional A-share data lake root containing PIT parquet")
    parser.add_argument("--code-commit", default="manual", help="Git commit or explicit code id")
    parser.add_argument(
        "--artifact-level",
        choices=("summary", "full"),
        default="summary",
        help="summary writes parquet factor/signal tables only; full also writes large JSON dumps",
    )
    args = parser.parse_args(argv)

    data_root = Path(args.data_root) if args.data_root else None
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()) if args.symbols else ()
    if not symbols and data_root is not None:
        symbols = resolve_kline_daily_symbols(data_root)
    if not symbols:
        parser.error("--symbols is required unless --data-root has a coverage manifest or PIT parquet symbols")

    run_pipeline(
        PipelineConfig(
            package_date=date.fromisoformat(args.date),
            symbols=symbols,
            out_dir=Path(args.out),
            pit_parquet=Path(args.pit_parquet) if args.pit_parquet else None,
            data_root=data_root,
            code_commit=args.code_commit,
            artifact_level=args.artifact_level,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
