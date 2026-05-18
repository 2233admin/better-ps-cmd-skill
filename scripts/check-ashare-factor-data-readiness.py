"""Report A-share factor/backtest data granularity readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import dataset_stats, read_delta_or_parquet, resolve_delta_or_parquet, scan_delta_or_parquet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare")
    parser.add_argument("--out", help="Output JSON report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_readiness_report(Path(args.data_root))
    out = Path(args.out) if args.out else Path(args.data_root) / "_manifest" / "factor_data_readiness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    report["path"] = str(out)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote factor data readiness report: {out}")
    return 0


def build_readiness_report(data_root: Path) -> dict[str, Any]:
    market_cap_item = _market_cap_item(data_root)
    industry_item = _industry_item(data_root)
    items = [
        _dataset_item(data_root, "price_daily", "ashare.kline_daily_pit", "pit/kline_daily_pit.parquet", "ready"),
        _dataset_item(data_root, "benchmark_index", "ashare.index_daily_pit", "pit/index_daily_pit.parquet", "ready"),
        _dataset_item(data_root, "backtest_feature_view", "ashare.backtest_daily_features_v1", "features/backtest_daily_pit_20260518.parquet", "ready"),
        _tradability_item(data_root),
        _limit_item(data_root),
        market_cap_item,
        industry_item,
        _share_float_item(data_root),
        _sidecar_item(
            data_root,
            "financial_statements",
            "normalized/tdx/finance_snapshot_20260518.parquet",
            "missing_pit",
            "latest finance snapshot exists, but report-period and announcement-time PIT statements are missing",
        ),
    ]
    blocking = [
        item["granularity"]
        for item in items
        if item["status"] in {"missing", "missing_pit"}
    ]
    partial = [item["granularity"] for item in items if item["status"] == "partial"]
    return {
        "dataset": "ashare.factor_data_readiness_v1",
        "overall": "price_factor_ready" if not any(item["granularity"] == "price_daily" and item["status"] != "ready" for item in items) else "not_ready",
        "can_run_price_factors": True,
        "can_run_fundamental_factors": False,
        "can_run_industry_neutral_factors": industry_item["status"] == "ready",
        "can_run_market_cap_neutral_factors": market_cap_item["status"] == "ready",
        "blocking_granularities": blocking,
        "partial_granularities": partial,
        "items": items,
    }


def _dataset_item(data_root: Path, granularity: str, dataset: str, relative: str, ready_status: str) -> dict[str, Any]:
    path = resolve_delta_or_parquet(data_root / relative)
    if path is None:
        return {
            "granularity": granularity,
            "dataset": dataset,
            "status": "missing",
            "path": relative,
            "reason": "dataset file not found",
        }
    stats = dataset_stats(path)
    return {
        "granularity": granularity,
        "dataset": dataset,
        "status": ready_status,
        "path": str(path.relative_to(data_root)).replace("\\", "/"),
        **stats,
    }


def _tradability_item(data_root: Path) -> dict[str, Any]:
    item = _dataset_item(
        data_root,
        "historical_tradability",
        "ashare.tradability_status_pit",
        "pit/tradability_status_pit.parquet",
        "partial",
    )
    if item["status"] == "missing":
        return item
    path = resolve_delta_or_parquet(data_root / "pit" / "tradability_status_pit.parquet")
    assert path is not None
    frame = scan_delta_or_parquet(path)
    schema = frame.collect_schema()
    event_dates = frame.select(pl.col("event_time").dt.date().n_unique()).collect().item()
    item["event_date_count"] = int(event_dates)
    if {"status_source", "up_limit_price", "down_limit_price"}.issubset(set(schema.names())):
        source_values = set(
            frame.select(pl.col("status_source").drop_nulls().unique()).collect()["status_source"].to_list()
        )
        if "chinadata_official" in source_values:
            item["status"] = "ready"
            item["reason"] = "official daily ST/suspension/limit-price status is aligned to observed PIT bars"
            return item
    item["reason"] = "PIT status exists, but current lake has a snapshot-level status history, not full historical ST/suspension timeline"
    return item


def _limit_item(data_root: Path) -> dict[str, Any]:
    status_path = resolve_delta_or_parquet(data_root / "pit" / "tradability_status_pit.parquet")
    if status_path is not None:
        status_schema = scan_delta_or_parquet(status_path).collect_schema()
        if {"up_limit_price", "down_limit_price", "status_source"}.issubset(set(status_schema.names())):
            stats = scan_delta_or_parquet(status_path).select(
                pl.len().alias("row_count"),
                pl.col("symbol").n_unique().alias("symbol_count"),
                pl.col("limit_up").sum().alias("limit_up_rows"),
                pl.col("limit_down").sum().alias("limit_down_rows"),
            ).collect().row(0, named=True)
            return {
                "granularity": "limit_up_down",
                "dataset": "ashare.tradability_status_pit",
                "status": "ready",
                "path": str(status_path.relative_to(data_root)).replace("\\", "/"),
                "reason": "official limit prices are present and mapped onto PIT daily status rows",
                **{key: int(value) for key, value in stats.items()},
            }
    path = resolve_delta_or_parquet(data_root / "features" / "backtest_daily_pit_20260518.parquet")
    if path is None:
        return {
            "granularity": "limit_up_down",
            "dataset": "feature.limit_status_proxy",
            "status": "missing",
            "path": "features/backtest_daily_pit_20260518.parquet",
            "reason": "backtest feature view not found",
        }
    schema = scan_delta_or_parquet(path).collect_schema()
    if not {"limit_up_proxy", "limit_down_proxy", "limit_status_source"}.issubset(set(schema.names())):
        return {
            "granularity": "limit_up_down",
            "dataset": "feature.limit_status_proxy",
            "status": "missing",
            "path": str(path.relative_to(data_root)).replace("\\", "/"),
            "reason": "limit proxy columns are missing",
        }
    stats = scan_delta_or_parquet(path).select(
        pl.len().alias("row_count"),
        pl.col("symbol").n_unique().alias("symbol_count"),
        pl.col("limit_up_proxy").sum().alias("limit_up_proxy_rows"),
        pl.col("limit_down_proxy").sum().alias("limit_down_proxy_rows"),
    ).collect().row(0, named=True)
    return {
        "granularity": "limit_up_down",
        "dataset": "feature.limit_status_proxy",
        "status": "proxy_ready",
        "path": str(path.relative_to(data_root)).replace("\\", "/"),
        "reason": "price-derived limit proxy is available; official historical limit/ST regime feed is still missing",
        **{key: int(value) for key, value in stats.items()},
    }


def _market_cap_item(data_root: Path) -> dict[str, Any]:
    path = resolve_delta_or_parquet(data_root / "pit" / "market_cap_daily_pit.parquet")
    if path is None:
        return _sidecar_item(
            data_root,
            "market_cap_and_float",
            "normalized/tdx/finance_snapshot_20260518.parquet",
            "missing_pit",
            "current finance snapshot exists, but historical share-count/market-cap PIT is missing",
        )
    item = _dataset_item(
        data_root,
        "market_cap_and_float",
        "ashare.market_cap_daily_pit",
        "pit/market_cap_daily_pit.parquet",
        "ready",
    )
    item["reason"] = "daily market-cap, float-share, and valuation PIT is available from chinadata daily_basic"
    return item


def _industry_item(data_root: Path) -> dict[str, Any]:
    path = resolve_delta_or_parquet(data_root / "pit" / "industry_daily_pit.parquet")
    if path is None:
        return _sidecar_item(
            data_root,
            "industry_and_blocks",
            "normalized/tdx/block_membership_20260518.parquet",
            "missing_pit",
            "current block snapshot exists, but historical industry/block PIT is missing",
        )
    item = _dataset_item(
        data_root,
        "industry_and_blocks",
        "ashare.industry_daily_pit",
        "pit/industry_daily_pit.parquet",
        "ready",
    )
    item["reason"] = "historical daily industry classification PIT is available; theme/block history can be added later"
    return item


def _share_float_item(data_root: Path) -> dict[str, Any]:
    path = resolve_delta_or_parquet(data_root / "pit" / "share_float_event_pit.parquet")
    if path is None:
        return {
            "granularity": "share_float_events",
            "dataset": "ashare.share_float_event_pit",
            "status": "missing",
            "path": "pit/share_float_event_pit.parquet",
            "reason": "share float event PIT not found",
        }
    item = _dataset_item(
        data_root,
        "share_float_events",
        "ashare.share_float_event_pit",
        "pit/share_float_event_pit.parquet",
        "ready",
    )
    item["reason"] = "unlock/float events with ann_date visibility are available"
    return item


def _sidecar_item(data_root: Path, granularity: str, relative: str, status: str, reason: str) -> dict[str, Any]:
    path = resolve_delta_or_parquet(data_root / relative)
    item = {
        "granularity": granularity,
        "dataset": relative,
        "status": status if path is not None else "missing",
        "path": relative,
        "reason": reason if path is not None else "source sidecar missing",
    }
    if path is not None:
        item["path"] = str(path.relative_to(data_root)).replace("\\", "/")
        item.update(dataset_stats(path))
    return item


if __name__ == "__main__":
    raise SystemExit(main())
