"""Run the A-share research pipeline end-to-end with fixture or real data.

Two modes:
- Fixture (default): exercises the full pipeline with the built-in 45-bar
  600000.SH synthetic series. No TDX, no lake required. Useful for "see
  what shipped" demos and PR review.
- Real lake: point `--data-root` at a PIT parquet lake produced by
  scripts/ingest-ashare-bridge.py + scripts/build-ashare-coverage.py.
  Calls the same code path as run-ashare-control-plane.ps1.

Outputs everything under `--out` (default `D:/tmp/katana-demo-run`):
manifest.json, signals.parquet, factors.parquet, scan_results.{csv,json,
parquet}, backtest/{daily_ledger.csv, fills.csv, orders.csv, trades.csv,
metrics.json}, portfolio_*.parquet, morning_package/{morning_package.md,
morning_package.pdf, control_report.{md,json}, trading_intents.{csv,json}}.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-05-17", help="Package date (YYYY-MM-DD)")
    parser.add_argument(
        "--symbols",
        default="600000.SH",
        help="Comma-separated canonical symbols (e.g. '600000.SH,000001.SZ')",
    )
    parser.add_argument(
        "--out",
        default=r"D:/tmp/katana-demo-run",
        help="Output dir (wiped before run)",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="Optional real PIT lake root (omit to use fixture mode)",
    )
    parser.add_argument(
        "--code-commit",
        default="demo",
        help="Tag stamped into manifest.json for traceability",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Do not wipe --out before running (append/overwrite into existing dir)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if out_dir.exists() and not args.keep:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    backend = repo_root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.research.pipeline import PipelineConfig, run_pipeline

    package_date = date.fromisoformat(args.date)
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    config = PipelineConfig(
        package_date=package_date,
        symbols=symbols,
        out_dir=out_dir,
        code_commit=args.code_commit,
        data_root=Path(args.data_root) if args.data_root else None,
    )

    print(f"[demo] running pipeline -> {out_dir}")
    print(f"[demo] package_date={package_date} symbols={symbols} mode={'real lake' if args.data_root else 'fixture'}")
    result = run_pipeline(config)
    print("[demo] pipeline OK")

    artifacts = sorted(p for p in out_dir.rglob("*") if p.is_file())
    print(f"\n=== {len(artifacts)} artifacts ===")
    for path in artifacts:
        rel = path.relative_to(out_dir)
        print(f"  {rel}  ({path.stat().st_size:,}B)")

    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print("\n=== manifest.json ===")
        for key in ("name", "code_commit", "dataset_version", "config_hash", "manifest_hash"):
            value = manifest.get(key)
            if value is not None:
                print(f"  {key}: {value}")

    print("\n=== control_report ===")
    cr = result.control_report
    print(f"  decision: {cr.decision}")
    print(f"  evidence_level: {cr.evidence_level}")
    print(f"  manifest_hash: {cr.manifest_hash[:16]}...")
    for reason in cr.halt_reasons:
        print(f"  halt: {reason}")
    for warning in cr.warnings:
        print(f"  warning: {warning}")

    print("\n=== ledger backtests ===")
    for symbol, bt in result.ledger_backtests.items():
        raw = getattr(bt, "metrics", {})
        metrics = raw() if callable(raw) else (raw or {})
        print(
            f"  {symbol}: total_return={metrics.get('total_return', 0):.4%} "
            f"trades={int(metrics.get('trade_count', 0))} "
            f"win_rate={metrics.get('win_rate', 0):.2%} "
            f"max_dd={metrics.get('max_drawdown', 0):.4%}"
        )

    intents_path = out_dir / "morning_package" / "trading_intents.json"
    if intents_path.exists():
        intents = json.loads(intents_path.read_text(encoding="utf-8"))
        print(f"\n=== trading_intents ({len(intents)}) ===")
        for intent in intents:
            print(
                f"  {intent.get('symbol')} {intent.get('side')} -> {intent.get('decision')} | "
                f"thesis: {intent.get('thesis', '')[:80]}"
            )

    print(f"\n[demo] full morning package: {out_dir}/morning_package/morning_package.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
