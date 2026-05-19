from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_datactl():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "ashare-datactl.py"
    spec = importlib.util.spec_from_file_location("ashare_datactl", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_local_dry_run_plans_non_network_data_lake_steps(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "build-local",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--date",
            "2026-05-18",
            "--dry-run",
            "--json",
        ]
    )

    summary = datactl.build_local(args)

    assert summary["run_id"] == "20260518"
    steps = {step["name"]: step for step in summary["steps"]}
    assert list(steps) == [
        "ensure-layout",
        "export-index",
        "normalize-tdx",
        "build-backtest-view",
        "coverage",
        "readiness",
    ]
    assert all(step["status"] == "planned" for step in summary["steps"])
    commands = [" ".join(step["command"]) for step in summary["steps"] if step["command"]]
    assert not any("download-ashare-tdx-raw.py" in command for command in commands)
    assert any("build-ashare-backtest-view.py" in command for command in commands)


def test_build_local_skips_missing_raw_tdx_dirs_without_failing(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "build-local",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--date",
            "2026-05-18",
            "--skip-index",
            "--skip-backtest-view",
            "--skip-coverage",
            "--skip-readiness",
        ]
    )

    summary = datactl.build_local(args)

    normalize = next(step for step in summary["steps"] if step["name"] == "normalize-tdx")
    assert normalize["status"] == "skipped"
    assert normalize["reason"] == "no raw TDX dirs for run_id=20260518"
    assert summary["failed"] is False


def test_sync_chinadata_status_dry_run_plans_official_status_build(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "sync-chinadata-status",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-18",
            "--dry-run",
        ]
    )

    summary = datactl.sync_chinadata_status(args)

    assert summary["failed"] is False
    step = summary["steps"][0]
    assert step["name"] == "sync-chinadata-status"
    assert step["status"] == "planned"
    assert "ingest-ashare-chinadata-status.py" in " ".join(step["command"])


def test_sync_chinadata_factors_dry_run_plans_factor_sidecars_build(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "sync-chinadata-factors",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--start",
            "2016-01-01",
            "--end",
            "2026-05-18",
            "--dry-run",
        ]
    )

    summary = datactl.sync_chinadata_factors(args)

    assert summary["failed"] is False
    step = summary["steps"][0]
    assert step["name"] == "sync-chinadata-factors"
    assert step["status"] == "planned"
    assert "ingest-ashare-chinadata-factors.py" in " ".join(step["command"])


def test_sync_chinadata_factors_dry_run_can_request_full_refresh(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "sync-chinadata-factors",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--full-refresh",
            "--dry-run",
        ]
    )

    summary = datactl.sync_chinadata_factors(args)

    step = summary["steps"][0]
    assert "--full-refresh" in step["command"]


def test_report_source_gaps_dry_run_plans_report_build(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "report-source-gaps",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--dry-run",
        ]
    )

    summary = datactl.report_source_gaps(args)

    assert summary["failed"] is False
    step = summary["steps"][0]
    assert step["name"] == "report-source-gaps"
    assert step["status"] == "planned"
    assert "report-ashare-source-gaps.py" in " ".join(step["command"])


def test_watch_progress_snapshot_surfaces_dataset_progress(tmp_path):
    datactl = _load_datactl()
    root = tmp_path / "Ashare"
    manifest_root = root / "_manifest"
    manifest_root.mkdir(parents=True)
    (manifest_root / "market_cap_daily_pit.json").write_text(
        """
        {
          "dataset": "ashare.market_cap_daily_pit",
          "tier": "pit",
          "exists": true,
          "sync_mode": "repair_full_refresh",
          "sync_strategy_version": 2,
          "progress_trade_date_count": 280,
          "planned_trade_date_count": 2516,
          "end": "2017-02-28 00:00:00+00:00",
          "row_count": 745823
        }
        """,
        encoding="utf-8",
    )
    (manifest_root / "industry_daily_pit.json").write_text(
        '{"dataset":"ashare.industry_daily_pit","tier":"pit","exists":true,"row_count":756000}',
        encoding="utf-8",
    )
    (manifest_root / "share_float_event_pit.json").write_text(
        '{"dataset":"ashare.share_float_event_pit","tier":"pit","exists":true,"progress_ann_date_end":"2020-01-31","row_count":58765}',
        encoding="utf-8",
    )
    args = datactl.main_argparse_for_test(
        [
            "watch-progress",
            "--data-root",
            str(root),
            "--iterations",
            "1",
        ]
    )

    snapshot = datactl.watch_progress_snapshot(args)

    market_cap = snapshot["datasets"]["market_cap_daily_pit"]
    assert market_cap["progress_trade_date_count"] == 280
    assert market_cap["planned_trade_date_count"] == 2516
    assert market_cap["progress_percent"] == 11.13
    assert snapshot["datasets"]["share_float_event_pit"]["progress_ann_date_end"] == "2020-01-31"


def test_compare_status_reports_mismatched_artifacts(tmp_path):
    datactl = _load_datactl()
    local_root = tmp_path / "local" / "Ashare"
    manifest_root = local_root / "_manifest"
    manifest_root.mkdir(parents=True)
    (manifest_root / "coverage.json").write_text(
        """
        {
          "items": [
            {
              "dataset": "ashare.market_cap_daily_pit",
              "path": "pit/market_cap_daily_pit",
              "storage_format": "delta",
              "row_count": 100,
              "symbol_count": 10,
              "start": "2016-01-01 00:00:00+00:00",
              "end": "2016-01-31 00:00:00+00:00",
              "content_hash": "abc"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    (manifest_root / "market_cap_daily_pit.json").write_text(
        """
        {
          "dataset": "ashare.market_cap_daily_pit",
          "path": "pit/market_cap_daily_pit",
          "storage_format": "delta",
          "row_count": 100,
          "symbol_count": 10,
          "start": "2016-01-01 00:00:00+00:00",
          "end": "2016-01-31 00:00:00+00:00",
          "content_hash": "abc",
          "planned_trade_date_count": 20,
          "progress_trade_date_count": 20
        }
        """,
        encoding="utf-8",
    )
    other_status = tmp_path / "other_status.json"
    other_status.write_text(
        """
        {
          "artifacts": {
            "market_cap_daily_pit": {
              "exists": true,
              "dataset": "ashare.market_cap_daily_pit",
              "row_count": 90,
              "symbol_count": 10,
              "start": "2016-01-01 00:00:00+00:00",
              "end": "2016-01-30 00:00:00+00:00",
              "content_hash": "xyz",
              "storage_format": "delta"
            }
          }
        }
        """,
        encoding="utf-8",
    )
    args = datactl.main_argparse_for_test(
        [
            "compare-status",
            "--data-root",
            str(local_root),
            "--other-status",
            str(other_status),
            "--left-label",
            "workstation",
            "--right-label",
            "rtx5090",
        ]
    )

    summary = datactl.compare_status(args)

    assert summary["mismatch_count"] >= 1
    mismatch = next(item for item in summary["comparisons"] if item["artifact"] == "market_cap_daily_pit")
    assert mismatch["artifact"] == "market_cap_daily_pit"
    assert mismatch["match"] is False
    assert {item["field"] for item in mismatch["diffs"]} >= {"row_count", "end", "content_hash"}


def test_migrate_sidecars_to_delta_dry_run_plans_delta_promotion(tmp_path):
    datactl = _load_datactl()
    args = datactl.main_argparse_for_test(
        [
            "migrate-sidecars-to-delta",
            "--data-root",
            str(tmp_path / "Ashare"),
            "--dry-run",
        ]
    )

    summary = datactl.migrate_sidecars_to_delta(args)

    assert summary["failed"] is False
    step = summary["steps"][0]
    assert step["name"] == "migrate-sidecars-to-delta"
    assert step["status"] == "planned"
    assert "migrate-ashare-sidecars-to-delta.py" in " ".join(step["command"])
