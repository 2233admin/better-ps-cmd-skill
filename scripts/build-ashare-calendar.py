"""Build an auditable A-share trading calendar snapshot from a PIT lake."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="A-share lake root")
    parser.add_argument("--pit-parquet", help="Override PIT parquet path")
    parser.add_argument("--start", help="Calendar start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Calendar end date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Print report JSON")
    args = parser.parse_args(argv)

    _ensure_backend_on_path()
    from app.research.pipeline.calendar import build_calendar_snapshot

    root = Path(args.root)
    pit_path = Path(args.pit_parquet) if args.pit_parquet else root / "pit" / "kline_daily_pit.parquet"
    result = build_calendar_snapshot(
        pit_path=pit_path,
        out_root=root,
        start=_parse_date(args.start),
        end=_parse_date(args.end),
    )
    payload = result.to_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote trading calendar snapshot: {result.calendar_path}")
        print(f"wrote trading calendar report: {result.report_path}")
    return 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _ensure_backend_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


if __name__ == "__main__":
    raise SystemExit(main())
