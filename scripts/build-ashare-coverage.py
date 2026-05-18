"""Build coverage manifest for the minimum A-share PIT lake."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import dataset_stats, resolve_delta_or_parquet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="A-share lake root")
    parser.add_argument("--json", action="store_true", help="Print manifest JSON")
    args = parser.parse_args(argv)

    root = Path(args.root)
    path = resolve_delta_or_parquet(root / "pit" / "kline_daily_pit.parquet")
    if path is None:
        raise SystemExit(f"missing parquet: {path}")

    universe = _read_universe_manifest(root)
    item = _coverage_item("ashare.kline_daily_pit", root, path)
    if universe:
        item["scan_universe"] = universe
        item["requested_symbols"] = universe.get("requested", {}).get("symbols", [])
        item["processed_symbols"] = universe.get("processed", {}).get("symbols", [])
        item["success_symbols"] = universe.get("success", {}).get("symbols", [])
        item["empty_symbols"] = universe.get("empty", {}).get("symbols", [])
        item["failure_symbols"] = universe.get("failure", {}).get("symbols", [])
        item["pit_symbols"] = universe.get("pit_symbols", {}).get("symbols", [])
    items = [item]
    for dataset, relative in (
        ("ashare.tradability_status_pit", "pit/tradability_status_pit.parquet"),
        ("ashare.adjustment_factor_pit", "pit/adjustment_factor_pit.parquet"),
        ("ashare.corporate_action_pit", "pit/corporate_action_pit.parquet"),
        ("ashare.index_daily_pit", "pit/index_daily_pit.parquet"),
        ("ashare.market_cap_daily_pit", "pit/market_cap_daily_pit.parquet"),
        ("ashare.industry_daily_pit", "pit/industry_daily_pit.parquet"),
        ("ashare.share_float_event_pit", "pit/share_float_event_pit.parquet"),
    ):
        candidate = resolve_delta_or_parquet(root / relative)
        if candidate is not None:
            items.append(_coverage_item(dataset, root, candidate))
    manifest = {"items": items}
    manifest_path = root / "_manifest" / "coverage.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote coverage manifest: {manifest_path}")
    return 0


def _coverage_item(dataset: str, root: Path, path: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    return {
        "dataset": dataset,
        "path": relative,
        **dataset_stats(path),
    }


def _read_universe_manifest(root: Path) -> dict:
    path = root / "_manifest" / "universe_manifest.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
