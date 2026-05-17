"""Build coverage manifest for the minimum A-share PIT lake."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import polars as pl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="A-share lake root")
    parser.add_argument("--json", action="store_true", help="Print manifest JSON")
    args = parser.parse_args(argv)

    root = Path(args.root)
    path = root / "pit" / "kline_daily_pit.parquet"
    if not path.exists():
        raise SystemExit(f"missing parquet: {path}")

    frame = pl.read_parquet(path)
    universe = _read_universe_manifest(root)
    item = {
        "dataset": "ashare.kline_daily_pit",
        "path": "pit/kline_daily_pit.parquet",
        "row_count": frame.height,
        "symbols": sorted(frame["symbol"].unique().to_list()) if frame.height else [],
        "start": str(frame["event_time"].min()) if frame.height else None,
        "end": str(frame["event_time"].max()) if frame.height else None,
        "content_hash": _sha256_file(path),
    }
    if universe:
        item["scan_universe"] = universe
        item["requested_symbols"] = universe.get("requested", {}).get("symbols", [])
        item["processed_symbols"] = universe.get("processed", {}).get("symbols", [])
        item["success_symbols"] = universe.get("success", {}).get("symbols", [])
        item["empty_symbols"] = universe.get("empty", {}).get("symbols", [])
        item["failure_symbols"] = universe.get("failure", {}).get("symbols", [])
        item["pit_symbols"] = universe.get("pit_symbols", {}).get("symbols", [])
    manifest = {"items": [item]}
    manifest_path = root / "_manifest" / "coverage.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote coverage manifest: {manifest_path}")
    return 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_universe_manifest(root: Path) -> dict:
    path = root / "_manifest" / "universe_manifest.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
