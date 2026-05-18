"""Merge an incremental A-share PIT batch into an existing lake."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl


REQUIRED_COLUMNS = {
    "symbol",
    "market",
    "event_time",
    "available_at",
    "source_updated_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True, help="A-share lake root to update")
    parser.add_argument("--batch-parquet", help="Incremental PIT parquet batch")
    parser.add_argument("--scan-out-dir", help="Existing tdx-cli scan-kline batch directory")
    parser.add_argument("--start", help="Batch start date YYYY-MM-DD")
    parser.add_argument("--end", help="Batch end date YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Print JSON manifest")
    args = parser.parse_args(argv)

    if bool(args.batch_parquet) == bool(args.scan_out_dir):
        raise SystemExit("provide exactly one of --batch-parquet or --scan-out-dir")

    out_root = Path(args.out_root)
    lake_path = out_root / "pit" / "kline_daily_pit.parquet"
    previous_hash = _sha256_file(lake_path) if lake_path.exists() else ""
    existing = pl.read_parquet(lake_path) if lake_path.exists() else pl.DataFrame()
    batch, source, universe = _load_batch(args)
    _validate_batch(batch)
    merged = _merge(existing, batch)
    lake_path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(lake_path)
    new_hash = _sha256_file(lake_path)
    manifest = {
        "dataset": "ashare.kline_daily_pit",
        "mode": "incremental",
        "source": source,
        "start": args.start,
        "end": args.end,
        "batch_rows": batch.height,
        "previous_rows": existing.height,
        "merged_rows": merged.height,
        "symbols": sorted(merged["symbol"].unique().to_list()) if merged.height else [],
        "previous_content_hash": previous_hash,
        "new_content_hash": new_hash,
        "written_at": datetime.now(tz=UTC).isoformat(),
    }
    manifest_dir = out_root / "_manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_dir / "incremental_batch.json", manifest)
    if universe:
        _write_json(manifest_dir / "universe_manifest.json", _merged_universe_manifest(universe, merged))
    (manifest_dir / "previous_content_hash").write_text(previous_hash + "\n", encoding="utf-8")
    (manifest_dir / "new_content_hash").write_text(new_hash + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"merged {batch.height} batch rows into {lake_path}")
    return 0


def _load_batch(args: argparse.Namespace) -> tuple[pl.DataFrame, str, dict[str, Any]]:
    if args.batch_parquet:
        path = Path(args.batch_parquet)
        return pl.read_parquet(path), str(path), {}
    bridge = _load_bridge()
    frame, universe = bridge._normalize_scan_out_dir(Path(args.scan_out_dir))
    return frame, str(args.scan_out_dir), universe


def _load_bridge() -> Any:
    path = Path(__file__).with_name("ingest-ashare-bridge.py")
    spec = importlib.util.spec_from_file_location("ingest_ashare_bridge", path)
    module = importlib.util.module_from_spec(spec)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot load ingest bridge: {path}")
    spec.loader.exec_module(module)
    return module


def _validate_batch(frame: pl.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise SystemExit(f"batch missing columns: {', '.join(missing)}")


def _merge(existing: pl.DataFrame, batch: pl.DataFrame) -> pl.DataFrame:
    if existing.is_empty():
        frame = batch
    else:
        existing, batch = _align_columns(existing, batch)
        frame = pl.concat([existing, batch], how="vertical_relaxed")
    return frame.unique(subset=["symbol", "event_time"], keep="last").sort(["symbol", "event_time"])


def _align_columns(left: pl.DataFrame, right: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    columns = list(dict.fromkeys([*left.columns, *right.columns]))
    return _select_with_missing(left, columns), _select_with_missing(right, columns)


def _select_with_missing(frame: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    expressions = [
        pl.col(column) if column in frame.columns else pl.lit(None).alias(column)
        for column in columns
    ]
    return frame.select(expressions)


def _merged_universe_manifest(universe: dict[str, Any], merged: pl.DataFrame) -> dict[str, Any]:
    pit_symbols = sorted(str(symbol) for symbol in merged["symbol"].unique().to_list()) if merged.height else []
    payload = dict(universe)
    payload["source"] = f"incremental:{universe.get('source', '')}".rstrip(":")
    payload["pit_symbols"] = {"count": len(pit_symbols), "symbols": pit_symbols}
    payload["universe"] = {"count": len(pit_symbols), "symbols": pit_symbols}
    return payload


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
