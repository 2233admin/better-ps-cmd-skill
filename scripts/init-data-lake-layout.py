"""Initialize the local multi-asset data lake directory layout."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


LAYOUT_VERSION = 1
COMMON_DIRS = ("raw", "normalized", "pit", "features", "experiments", "_manifest")
DEFAULT_ASSETS = ("ashare", "crypto")


@dataclass(frozen=True)
class AssetLayout:
    asset: str
    physical_dir: str
    root: Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA", help="Repository-local DATA root")
    parser.add_argument(
        "--assets",
        default=",".join(DEFAULT_ASSETS),
        help="Comma-separated asset domains to initialize",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    assets = tuple(item.strip().lower() for item in args.assets.split(",") if item.strip())
    layouts = initialize_layout(data_root, assets)
    summary = {
        "layout_version": LAYOUT_VERSION,
        "data_root": str(data_root),
        "registry_root": str(data_root / "_registry"),
        "assets": [
            {
                "asset": layout.asset,
                "physical_dir": layout.physical_dir,
                "root": str(layout.root),
                "dirs": [str(layout.root / item) for item in COMMON_DIRS],
            }
            for layout in layouts
        ],
    }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) if args.json else _format_summary(summary))
    return 0


def initialize_layout(data_root: Path, assets: Iterable[str] = DEFAULT_ASSETS) -> list[AssetLayout]:
    data_root.mkdir(parents=True, exist_ok=True)
    registry_root = data_root / "_registry"
    registry_root.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(
        registry_root / "data_sources.json",
        {"layout_version": LAYOUT_VERSION, "sources": [], "updated_at": _now()},
    )
    _write_json_if_missing(
        registry_root / "dataset_versions.json",
        {"layout_version": LAYOUT_VERSION, "versions": [], "updated_at": _now()},
    )

    layouts: list[AssetLayout] = []
    for asset in assets:
        physical_dir = _physical_asset_dir(asset)
        root = data_root / physical_dir
        for dirname in COMMON_DIRS:
            (root / dirname).mkdir(parents=True, exist_ok=True)
        _write_json_if_missing(
            root / "_manifest" / "lake_manifest.json",
            {
                "layout_version": LAYOUT_VERSION,
                "asset": asset,
                "physical_dir": physical_dir,
                "datasets": [],
                "updated_at": _now(),
            },
        )
        layouts.append(AssetLayout(asset=asset, physical_dir=physical_dir, root=root))
    return layouts


def _physical_asset_dir(asset: str) -> str:
    if asset in {"ashare", "a", "a股", "a-share"}:
        return "Ashare"
    if asset in {"crypto", "digital_asset", "digital-assets"}:
        return "crypto"
    return asset


def _write_json_if_missing(path: Path, payload: dict) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _format_summary(summary: dict) -> str:
    assets = ", ".join(item["root"] for item in summary["assets"])
    return f"initialized data lake layout under {summary['data_root']}: {assets}"


if __name__ == "__main__":
    raise SystemExit(main())
