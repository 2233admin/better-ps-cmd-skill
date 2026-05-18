"""Summarize A-share source coverage gaps between TDXCLI and ChinaData/Tushare."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    report = build_source_gap_report(data_root)
    out_path = data_root / "_manifest" / "source_gap_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote source gap report: {out_path}")
    return 0


def build_source_gap_report(data_root: Path) -> dict[str, Any]:
    artifacts = _artifacts(data_root)
    items = [
        _gap_item(
            dataset="daily_price_bars",
            backtest_need="required",
            tdxcli={
                "status": "ready" if artifacts["kline_daily_pit"] else "missing",
                "dataset": "ashare.kline_daily_pit",
                "notes": ["TDXCLI is already the canonical daily price source."],
            },
            chinadata={
                "status": "not_target",
                "apis": [],
                "dataset": None,
                "notes": ["Do not re-pull daily bars from ChinaData when TDX kline PIT already exists."],
            },
            action="keep_tdx_canonical",
        ),
        _gap_item(
            dataset="official_tradability_status",
            backtest_need="required",
            tdxcli={
                "status": "missing",
                "dataset": None,
                "notes": ["TDXCLI does not provide PIT-safe historical ST, suspend, and official limit price history."],
            },
            chinadata={
                "status": "ready" if artifacts["tradability_status_pit"] else "missing",
                "apis": ["namechange", "suspend_d", "stk_limit"],
                "dataset": "ashare.tradability_status_pit",
                "notes": ["This is the official status layer TDX lacks."],
            },
            action="pull_incrementally_from_chinadata_only",
        ),
        _gap_item(
            dataset="daily_market_cap_and_float",
            backtest_need="required_for_market_cap_neutralization",
            tdxcli={
                "status": "snapshot_only" if artifacts["tdx_finance_snapshot"] else "missing",
                "dataset": "ashare.tdx_finance_snapshot",
                "notes": ["TDX only gives a latest finance snapshot, not historical PIT-safe daily market-cap rows."],
            },
            chinadata={
                "status": "ready" if artifacts["market_cap_daily_pit"] else "missing",
                "apis": ["daily_basic"],
                "dataset": "ashare.market_cap_daily_pit",
                "notes": ["Use daily_basic incrementally by missing trade_date only."],
            },
            action="pull_incrementally_from_chinadata_only",
        ),
        _gap_item(
            dataset="daily_industry_classification",
            backtest_need="required_for_industry_neutralization",
            tdxcli={
                "status": "snapshot_only" if artifacts["tdx_block_membership"] else "missing",
                "dataset": "ashare.tdx_block_membership",
                "notes": ["TDX block files are current snapshots without historical effective windows."],
            },
            chinadata={
                "status": "ready" if artifacts["industry_daily_pit"] else "missing",
                "apis": ["bak_daily"],
                "dataset": "ashare.industry_daily_pit",
                "notes": ["bak_daily provides historical day-level industry/area rows from about mid-2017 onward."],
            },
            action="pull_incrementally_from_chinadata_only",
        ),
        _gap_item(
            dataset="share_float_events",
            backtest_need="required_for_unlock_and_float_change_factors",
            tdxcli={
                "status": "missing",
                "dataset": None,
                "notes": ["TDXCLI has no historical unlock event stream."],
            },
            chinadata={
                "status": "ready" if artifacts["share_float_event_pit"] else "missing",
                "apis": ["share_float"],
                "dataset": "ashare.share_float_event_pit",
                "notes": ["Pull via split date windows below the 6000-row cap; then update incrementally with overlap."],
            },
            action="pull_incrementally_from_chinadata_only",
        ),
        _gap_item(
            dataset="financial_statements_pit",
            backtest_need="required_for_fundamental_factors",
            tdxcli={
                "status": "snapshot_only" if artifacts["tdx_finance_snapshot"] else "missing",
                "dataset": "ashare.tdx_finance_snapshot",
                "notes": ["TDX finance is evidence only. It lacks report-period and release-time PIT semantics."],
            },
            chinadata={
                "status": "planned",
                "apis": ["disclosure_date", "income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip"],
                "dataset": "financial_statements",
                "notes": ["This is the next major PIT build for ROE/ROA/growth/cashflow factors."],
            },
            action="next_priority_pull",
        ),
        _gap_item(
            dataset="index_constituents_and_weights",
            backtest_need="required_for_benchmark_relative_tests",
            tdxcli={
                "status": "snapshot_only" if artifacts["tdx_block_membership"] else "missing",
                "dataset": "ashare.tdx_block_membership",
                "notes": ["TDX block snapshots are not a historical constituent/weight series."],
            },
            chinadata={
                "status": "planned",
                "apis": ["index_weight"],
                "dataset": "index_constituents",
                "notes": ["Build this after financial statements."],
            },
            action="next_priority_pull",
        ),
        _gap_item(
            dataset="historical_block_membership",
            backtest_need="required_for_theme_and_block_neutralization",
            tdxcli={
                "status": "snapshot_only" if artifacts["tdx_block_membership"] else "missing",
                "dataset": "ashare.tdx_block_membership",
                "notes": ["TDX block files are current snapshots only."],
            },
            chinadata={
                "status": "planned",
                "apis": ["index_member_all", "tdx_member", "ths_member"],
                "dataset": "industry_classification",
                "notes": ["Use historical membership sources instead of current TDX snapshots."],
            },
            action="next_priority_pull",
        ),
    ]
    return {
        "dataset": "ashare.source_gap_report_v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "source_priority": "PIT Lake > DuckDB cache > xtdata/QMT ingest > TDXCLIrs fallback",
        "principle": "Only pull the datasets TDXCLI does not already cover as PIT-safe research facts, then sync incrementally instead of replaying the full history every run.",
        "items": items,
    }


def _artifacts(data_root: Path) -> dict[str, bool]:
    manifest_root = data_root / "_manifest"
    return {
        "kline_daily_pit": (data_root / "pit" / "kline_daily_pit.parquet").exists(),
        "tradability_status_pit": _dataset_present(data_root, "ashare.tradability_status_pit", "tradability_status_pit"),
        "market_cap_daily_pit": _dataset_present(data_root, "ashare.market_cap_daily_pit", "market_cap_daily_pit"),
        "industry_daily_pit": _dataset_present(data_root, "ashare.industry_daily_pit", "industry_daily_pit"),
        "share_float_event_pit": _dataset_present(data_root, "ashare.share_float_event_pit", "share_float_event_pit"),
        "tdx_finance_snapshot": any(manifest_root.glob("tdx_finance_snapshot_*.json")),
        "tdx_block_membership": any(manifest_root.glob("tdx_block_membership_*.json")),
    }


def _dataset_present(data_root: Path, dataset: str, stem: str) -> bool:
    if _coverage_has_dataset(data_root / "_manifest" / "coverage.json", dataset):
        return True
    manifest_path = data_root / "_manifest" / f"{stem}.json"
    if _manifest_exists(manifest_path, dataset):
        return True
    pit_path = data_root / "pit" / stem
    return pit_path.exists() or pit_path.with_suffix(".parquet").exists()


def _coverage_has_dataset(path: Path, dataset: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return any(item.get("dataset") == dataset for item in payload.get("items", []))


def _manifest_exists(path: Path, dataset: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("dataset") == dataset


def _gap_item(*, dataset: str, backtest_need: str, tdxcli: dict[str, Any], chinadata: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "backtest_need": backtest_need,
        "tdxcli": tdxcli,
        "chinadata": chinadata,
        "action": action,
        "pull_once_then_incremental": action != "keep_tdx_canonical",
    }


if __name__ == "__main__":
    raise SystemExit(main())
