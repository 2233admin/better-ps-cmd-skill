"""Normalize raw TDX finance and block snapshots into queryable parquet files.

The outputs are deliberately kept under DATA/Ashare/normalized/tdx. TDX finance
and block files are current snapshots, not historical announcement/effective
streams, so they must not be treated as PIT-safe research inputs yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare", help="A-share data root")
    parser.add_argument("--run-id", help="TDX raw run id. Defaults to the latest matching raw dirs.")
    parser.add_argument("--finance-dir", help="Raw tdx_finance_<run-id> directory")
    parser.add_argument("--blocks-dir", help="Raw tdx_blocks_<run-id> directory")
    parser.add_argument("--skip-finance", action="store_true")
    parser.add_argument("--skip-blocks", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    raw_root = data_root / "raw"
    normalized_root = data_root / "normalized" / "tdx"
    manifest_root = data_root / "_manifest"
    normalized_root.mkdir(parents=True, exist_ok=True)
    manifest_root.mkdir(parents=True, exist_ok=True)

    run_id = args.run_id or _infer_run_id(raw_root)
    finance_dir = Path(args.finance_dir) if args.finance_dir else raw_root / f"tdx_finance_{run_id}"
    blocks_dir = Path(args.blocks_dir) if args.blocks_dir else raw_root / f"tdx_blocks_{run_id}"

    summary: dict[str, Any] = {
        "run_id": run_id,
        "finance": None,
        "blocks": None,
    }
    if not args.skip_finance:
        finance_path = normalized_root / f"finance_snapshot_{run_id}.parquet"
        summary["finance"] = normalize_finance_snapshot(
            finance_dir=finance_dir,
            out_parquet=finance_path,
            manifest_path=manifest_root / f"tdx_finance_snapshot_{run_id}.json",
            data_root=data_root,
        )
    if not args.skip_blocks:
        block_path = normalized_root / f"block_membership_{run_id}.parquet"
        summary["blocks"] = normalize_block_membership(
            blocks_dir=blocks_dir,
            out_parquet=block_path,
            manifest_path=manifest_root / f"tdx_block_membership_{run_id}.json",
            data_root=data_root,
        )

    combined_manifest = manifest_root / f"tdx_normalized_{run_id}.json"
    combined_manifest.write_text(
        json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    summary["manifest_path"] = str(combined_manifest)
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote normalized TDX manifest: {combined_manifest}")
    return 0


def _infer_run_id(raw_root: Path) -> str:
    finance_ids = {
        path.name.removeprefix("tdx_finance_")
        for path in raw_root.glob("tdx_finance_*")
        if path.is_dir() and "smoke" not in path.name
    }
    block_ids = {
        path.name.removeprefix("tdx_blocks_")
        for path in raw_root.glob("tdx_blocks_*")
        if path.is_dir() and "smoke" not in path.name
    }
    candidates = sorted(finance_ids & block_ids)
    if not candidates:
        raise SystemExit(f"no matching tdx_finance_* and tdx_blocks_* dirs under {raw_root}")
    return candidates[-1]


def normalize_finance_snapshot(
    *,
    finance_dir: Path,
    out_parquet: Path,
    manifest_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    item_dir = finance_dir / "items"
    if not item_dir.exists():
        raise SystemExit(f"finance item dir not found: {item_dir}")
    rows: list[dict[str, Any]] = []
    for path in sorted(item_dir.glob("*.json")):
        wrapper = _read_json(path)
        row = _finance_row(wrapper, source_path=path)
        rows.append(row)
    if not rows:
        raise SystemExit(f"no finance item json files under {item_dir}")

    frame = pl.DataFrame(rows, infer_schema_length=None).sort("symbol")
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_parquet)

    updated_counts = Counter(str(value) for value in frame.get_column("updated_date").to_list())
    updated_counts.pop("None", None)
    missing_updated = frame.filter(pl.col("updated_date").is_null()).height
    manifest = {
        "dataset": "ashare.tdx_finance_snapshot",
        "tier": "normalized",
        "research_eligible": False,
        "path": _relative(out_parquet, data_root),
        "source_dir": _relative(finance_dir, data_root),
        "row_count": frame.height,
        "symbol_count": frame.select(pl.col("symbol").n_unique()).item(),
        "content_hash": _sha256_file(out_parquet),
        "complete_count": int(frame.filter(pl.col("complete") == True).height),
        "calibrated_count": int(frame.filter(pl.col("calibrated") == True).height),
        "missing_updated_date_count": missing_updated,
        "updated_date_top20": updated_counts.most_common(20),
        "honest_gaps": [
            "TDX finance is a latest snapshot, not a historical statement release stream",
            "updated_date is the vendor finance update/report marker; announcement timestamp is unavailable",
            "dataset is normalized evidence only and must not feed historical backtests until promoted with explicit available_at semantics",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest


def _finance_row(wrapper: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    payload = wrapper.get("tdx_payload") or {}
    calibrated_data = payload.get("calibrated_data") or {}
    symbol = str(wrapper.get("symbol") or "").upper()
    if not symbol:
        code = str(wrapper.get("code") or payload.get("code") or "").zfill(6)
        market = str(wrapper.get("market") or payload.get("market") or "").upper()
        symbol = f"{code}.{market}" if code and market else code
    downloaded_at = _parse_datetime(wrapper.get("downloaded_at"))
    row: dict[str, Any] = {
        "symbol": symbol,
        "market": str(wrapper.get("market") or payload.get("market") or "").upper(),
        "code": str(wrapper.get("code") or payload.get("code") or "").zfill(6),
        "is_tradable": wrapper.get("is_tradable"),
        "downloaded_at": downloaded_at,
        "source_updated_at": downloaded_at,
        "complete": bool(payload.get("complete")),
        "calibrated": bool(payload.get("calibrated")),
        "raw_safe": bool(payload.get("raw_safe")),
        "field_count": int(payload.get("count") or len(calibrated_data)),
        "tdx_market": str(payload.get("market") or ""),
        "payload_hash": _sha256_json(payload),
        "source_path": str(source_path),
    }
    for raw_name, item in calibrated_data.items():
        if not isinstance(item, dict):
            continue
        column = _safe_column(raw_name)
        row[column] = _field_value(raw_name, item)
        name = item.get("name")
        if name not in (None, ""):
            row[f"{column}_name"] = str(name)
    row.setdefault("updated_date", None)
    row.setdefault("ipo_date", None)
    return row


def normalize_block_membership(
    *,
    blocks_dir: Path,
    out_parquet: Path,
    manifest_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    if not blocks_dir.exists():
        raise SystemExit(f"block dir not found: {blocks_dir}")
    known_symbols = _known_symbol_by_code(data_root)
    rows: list[dict[str, Any]] = []
    for path in sorted(blocks_dir.glob("*.dat.json")):
        wrapper = _read_json(path)
        payload = wrapper.get("tdx_payload") or {}
        entries = payload.get("entries") or []
        block_file = str(wrapper.get("block_file") or path.name.removesuffix(".json"))
        downloaded_at = _parse_datetime(wrapper.get("downloaded_at"))
        payload_hash = _sha256_json(payload)
        for item in entries:
            code = str(item.get("code") or "").strip().zfill(6)
            symbol, market, is_known = _canonical_symbol_from_code(code, known_symbols)
            rows.append(
                {
                    "block_file": block_file,
                    "block_type": int(item.get("block_type") or 0),
                    "block_name": str(item.get("blockname") or item.get("block_name") or ""),
                    "code": code,
                    "symbol": symbol,
                    "market": market,
                    "is_known_ashare": is_known,
                    "code_index": int(item.get("code_index") or 0),
                    "downloaded_at": downloaded_at,
                    "source_updated_at": downloaded_at,
                    "payload_hash": payload_hash,
                    "source_path": str(path),
                }
            )
    if not rows:
        raise SystemExit(f"no block entries under {blocks_dir}")

    frame = (
        pl.DataFrame(rows, infer_schema_length=None)
        .unique(subset=["block_file", "block_type", "block_name", "code", "code_index"], keep="first")
        .sort(["block_file", "block_name", "code_index", "code"])
    )
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out_parquet)

    manifest = {
        "dataset": "ashare.tdx_block_membership",
        "tier": "normalized",
        "research_eligible": False,
        "path": _relative(out_parquet, data_root),
        "source_dir": _relative(blocks_dir, data_root),
        "row_count": frame.height,
        "block_file_count": frame.select(pl.col("block_file").n_unique()).item(),
        "block_name_count": frame.select(pl.col("block_name").n_unique()).item(),
        "known_ashare_row_count": int(frame.filter(pl.col("is_known_ashare") == True).height),
        "known_ashare_symbol_count": frame.filter(pl.col("is_known_ashare") == True)
        .select(pl.col("symbol").n_unique())
        .item(),
        "content_hash": _sha256_file(out_parquet),
        "block_files": sorted(frame["block_file"].unique().to_list()),
        "honest_gaps": [
            "TDX block files are latest membership snapshots without historical effective dates",
            "dataset is normalized evidence only and must not feed historical backtests until membership available_at/effective windows exist",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest


def _known_symbol_by_code(data_root: Path) -> dict[str, str]:
    path = data_root / "pit" / "tradability_status_pit.parquet"
    if not path.exists():
        return {}
    frame = pl.scan_parquet(path).select("symbol").unique().collect()
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for symbol in frame["symbol"].to_list():
        code = str(symbol).split(".", 1)[0]
        if code in mapping and mapping[code] != symbol:
            ambiguous.add(code)
        else:
            mapping[code] = str(symbol)
    for code in ambiguous:
        mapping.pop(code, None)
    return mapping


def _canonical_symbol_from_code(code: str, known_symbols: dict[str, str]) -> tuple[str | None, str | None, bool]:
    if code in known_symbols:
        symbol = known_symbols[code]
        return symbol, symbol.split(".", 1)[1], True
    market = _infer_market(code)
    return (f"{code}.{market}", market, False) if market else (None, None, False)


def _infer_market(code: str) -> str | None:
    if code.startswith(("600", "601", "603", "605", "688", "510", "511", "512", "513", "515", "516", "518", "588")):
        return "SH"
    if code.startswith(("000", "001", "002", "003", "300", "301", "159", "399")):
        return "SZ"
    if code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")):
        return "BJ"
    return None


def _field_value(raw_name: str, item: dict[str, Any]) -> Any:
    if item.get("encoding") == "YYYYMMDD" or raw_name.endswith("_date"):
        iso = item.get("iso")
        return str(iso) if iso else None
    iso = item.get("iso")
    if iso:
        return str(iso)
    if "value" in item:
        return _scalar(item.get("value"))
    if "raw" in item:
        return _scalar(item.get("raw"))
    return None


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str, date, datetime)):
        return value
    return None


def _safe_column(value: str) -> str:
    column = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower())
    column = re.sub(r"_+", "_", column).strip("_")
    return column or "field"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _sha256_json(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
