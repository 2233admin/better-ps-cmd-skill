"""Contract tests for the end-to-end A-share research pipeline."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl


def _pit_frame(symbol: str = "600000.SH") -> pl.DataFrame:
    rows = []
    for idx in range(45):
        current = date(2026, 4, 3) + timedelta(days=idx)
        close = 10.0 + idx * 0.08
        rows.append(
            {
                "symbol": symbol,
                "market": "SH" if symbol.startswith("6") else "SZ",
                "event_time": datetime(current.year, current.month, current.day, tzinfo=UTC),
                "available_at": datetime(current.year, current.month, current.day, tzinfo=UTC)
                + timedelta(hours=16),
                "source_updated_at": datetime(current.year, current.month, current.day, tzinfo=UTC)
                + timedelta(hours=17),
                "open": close - 0.03,
                "high": close + 0.05,
                "low": close - 0.06,
                "close": close,
                "volume": 1_000_000 + idx * 1000,
                "amount": (1_000_000 + idx * 1000) * close,
            }
        )
    return pl.DataFrame(rows)


def test_pipeline_generates_manifest_backtest_and_morning_package(tmp_path):
    from app.research.pipeline import PipelineConfig, run_pipeline

    out_dir = tmp_path / "run"
    result = run_pipeline(
        PipelineConfig(
            package_date=date(2026, 5, 17),
            symbols=("600000.SH",),
            out_dir=out_dir,
            code_commit="abc1234",
        )
    )

    expected = [
        "manifest.json",
        "signals.parquet",
        "factors.parquet",
        "scan_results.json",
        "scan_results.csv",
        "scan_results.parquet",
        "backtest_summary.parquet",
        "backtest_orders.parquet",
        "backtest_trades.parquet",
        "backtest_equity_curve.parquet",
        "portfolio_orders.parquet",
        "portfolio_fills.parquet",
        "portfolio_positions.parquet",
        "portfolio_equity_curve.parquet",
        "backtest/orders.csv",
        "backtest/fills.csv",
        "backtest/daily_ledger.csv",
        "backtest/trades.csv",
        "backtest/metrics.json",
        "morning_package/morning_package.md",
        "morning_package/audit.json",
        "morning_package/trading_intents.csv",
        "morning_package/trading_intents.json",
        "morning_package/morning_package.pdf",
        "morning_package/control_report.json",
        "morning_package/control_report.md",
    ]
    for relative in expected:
        assert (out_dir / relative).exists(), relative
    assert not (out_dir / "signals.json").exists()
    assert not (out_dir / "factors.json").exists()
    assert not (out_dir / "backtest" / "600000.SH").exists()

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    scan_results = json.loads((out_dir / "scan_results.json").read_text(encoding="utf-8"))
    signal_rows = pl.read_parquet(out_dir / "signals.parquet")
    factor_rows = pl.read_parquet(out_dir / "factors.parquet")
    metrics = json.loads((out_dir / "backtest" / "metrics.json").read_text(encoding="utf-8"))
    summary_rows = pl.read_parquet(out_dir / "backtest_summary.parquet")
    audit = json.loads(
        (out_dir / "morning_package" / "audit.json").read_text(encoding="utf-8")
    )

    assert len(manifest["manifest_hash"]) == 64
    assert manifest["code_commit"] == "abc1234"
    assert signal_rows.height > 0
    assert factor_rows.height > 0
    assert len(scan_results) == 1
    assert scan_results[0]["symbol"] == "600000.SH"
    assert scan_results[0]["visible_rows"] > 0
    assert scan_results[0]["backtest_status"] == "completed"
    assert "momentum_20d" in scan_results[0]
    assert metrics["metrics"]["max_drawdown"] >= 0
    assert summary_rows["symbol"].to_list() == ["600000.SH"]
    assert audit["manifest_hash"] == manifest["manifest_hash"]
    assert audit["control_decision"] in {"promote", "observe", "reject", "halt"}
    assert audit["control_report_path"] == "control_report.json"
    assert audit["evidence_level"] == "fixture_smoke"
    assert result.control_report.decision == "observe"
    assert result.morning_package_dir == out_dir / "morning_package"
    assert result.control_report.manifest_hash == manifest["manifest_hash"]


def test_pipeline_cli_runs_fixture_chain(tmp_path):
    from app.research.pipeline.cli import main

    out_dir = tmp_path / "cli-run"
    exit_code = main(
        [
            "--date",
            "2026-05-17",
            "--symbols",
            "600000.SH,000001.SZ",
            "--out",
            str(out_dir),
            "--code-commit",
            "abc1234",
        ]
    )

    assert exit_code == 0
    intents = json.loads(
        (out_dir / "morning_package" / "trading_intents.json").read_text(encoding="utf-8")
    )
    assert 1 <= len(intents) <= 3
    assert {intent["manual_action"] for intent in intents} == {"pending"}
    assert len({intent["symbol"] for intent in intents}) == len(intents)


def test_pipeline_full_artifact_level_keeps_large_json_compatibility(tmp_path):
    from app.research.pipeline.cli import main

    out_dir = tmp_path / "cli-full-run"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--symbols",
                "600000.SH",
                "--out",
                str(out_dir),
                "--artifact-level",
                "full",
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    assert (out_dir / "signals.parquet").exists()
    assert (out_dir / "factors.parquet").exists()
    assert (out_dir / "signals.json").exists()
    assert (out_dir / "factors.json").exists()
    assert (out_dir / "backtest" / "600000.SH" / "metrics.json").exists()


def test_world_snapshot_filters_future_available_rows(tmp_path):
    from app.research.pipeline.world_snapshot import build_world_snapshot

    root = tmp_path / "lake"
    parquet = root / "pit" / "kline_daily_pit.parquet"
    parquet.parent.mkdir(parents=True)
    frame = pl.concat(
        [
            _pit_frame(),
            pl.DataFrame(
                {
                    "symbol": ["600000.SH"],
                    "market": ["SH"],
                    "event_time": [datetime(2026, 5, 17, tzinfo=UTC)],
                    "available_at": [datetime(2026, 5, 18, 9, tzinfo=UTC)],
                    "source_updated_at": [datetime(2026, 5, 18, 9, tzinfo=UTC)],
                    "open": [99.0],
                    "high": [100.0],
                    "low": [98.0],
                    "close": [99.0],
                    "volume": [100],
                    "amount": [9900.0],
                }
            ),
        ],
        how="vertical_relaxed",
    )
    frame.write_parquet(parquet)

    result = build_world_snapshot(
        data_root=root,
        as_of_date=date(2026, 5, 17),
        out_dir=tmp_path / "world",
    )
    rows = pl.read_parquet(result.parquet_path)

    assert result.snapshot_id == "ashare.world_snapshot_v1:2026-05-17"
    assert rows.height == 1
    assert rows["close"].to_list() == [13.52]
    assert rows.filter(pl.col("available_at") > pl.col("as_of")).is_empty()
    assert rows["research_eligible"].to_list() == [True]


def test_world_snapshot_merges_tradability_status_pit(tmp_path):
    from app.research.pipeline.world_snapshot import build_world_snapshot

    root = tmp_path / "lake"
    parquet = root / "pit" / "kline_daily_pit.parquet"
    status = root / "pit" / "tradability_status_pit.parquet"
    parquet.parent.mkdir(parents=True)
    _pit_frame().write_parquet(parquet)
    pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2026, 5, 17, tzinfo=UTC)],
            "available_at": [datetime(2026, 5, 17, 15, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 5, 17, 15, tzinfo=UTC)],
            "is_st": [True],
            "is_suspended": [False],
            "limit_up": [False],
            "limit_down": [False],
            "listed_days": [200],
            "is_tradable": [False],
            "reason": ["st_stock"],
        }
    ).write_parquet(status)

    result = build_world_snapshot(
        data_root=root,
        as_of_date=date(2026, 5, 17),
        out_dir=tmp_path / "world",
    )
    rows = pl.read_parquet(result.parquet_path)

    assert rows["research_eligible"].to_list() == [False]
    assert rows["tradable_reason"].to_list() == ["st_stock"]


def test_pipeline_can_use_world_snapshot_as_universe(tmp_path):
    from app.research.pipeline.cli import main
    from app.research.pipeline.world_snapshot import build_world_snapshot

    data_root = tmp_path / "lake"
    parquet_dir = data_root / "pit"
    parquet_dir.mkdir(parents=True)
    pl.concat([_pit_frame("600000.SH"), _pit_frame("000001.SZ")]).write_parquet(
        parquet_dir / "kline_daily_pit.parquet"
    )
    snapshot = build_world_snapshot(
        data_root=data_root,
        as_of_date=date(2026, 5, 17),
        out_dir=tmp_path / "world",
        symbols=("000001.SZ",),
    )

    out_dir = tmp_path / "snapshot-run"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--data-root",
                str(data_root),
                "--world-snapshot",
                str(snapshot.parquet_path),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    rows = json.loads((out_dir / "scan_results.json").read_text(encoding="utf-8"))
    assert [row["symbol"] for row in rows] == ["000001.SZ"]


def test_pipeline_as_of_matches_world_snapshot_day_end_visibility(tmp_path):
    from app.research.pipeline.cli import main
    from app.research.pipeline.world_snapshot import build_world_snapshot

    data_root = tmp_path / "lake"
    parquet_dir = data_root / "pit"
    parquet_dir.mkdir(parents=True)
    frame = pl.concat(
        [
            _pit_frame("600000.SH"),
            pl.DataFrame(
                {
                    "symbol": ["001393.SZ"],
                    "market": ["SZ"],
                    "event_time": [datetime(2026, 5, 16, tzinfo=UTC)],
                    "available_at": [datetime(2026, 5, 16, 16, tzinfo=UTC)],
                    "source_updated_at": [datetime(2026, 5, 16, 17, tzinfo=UTC)],
                    "open": [10.0],
                    "high": [10.2],
                    "low": [9.9],
                    "close": [10.1],
                    "volume": [100],
                    "amount": [1010.0],
                }
            ),
        ],
        how="vertical_relaxed",
    )
    frame.write_parquet(parquet_dir / "kline_daily_pit.parquet")
    snapshot = build_world_snapshot(
        data_root=data_root,
        as_of_date=date(2026, 5, 16),
        out_dir=tmp_path / "world",
    )

    out_dir = tmp_path / "snapshot-run"
    assert (
        main(
            [
                "--date",
                "2026-05-16",
                "--data-root",
                str(data_root),
                "--world-snapshot",
                str(snapshot.parquet_path),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    control = json.loads((out_dir / "morning_package" / "control_report.json").read_text(encoding="utf-8"))
    assert "001393.SZ" in control["observed_state"]["visible_symbols"]
    assert "001393.SZ" not in control["observed_state"]["empty_or_missing_symbols"]


def test_ashare_benchmark_writes_metrics(tmp_path):
    from app.research.pipeline.benchmark import main

    data_root = tmp_path / "lake"
    parquet_dir = data_root / "pit"
    manifest_dir = data_root / "_manifest"
    parquet_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    _pit_frame().write_parquet(parquet_dir / "kline_daily_pit.parquet")
    (manifest_dir / "coverage.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "dataset": "ashare.kline_daily_pit",
                        "path": "pit/kline_daily_pit.parquet",
                        "symbols": ["600000.SH"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "benchmark"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    benchmark = json.loads((out_dir / "benchmark.json").read_text(encoding="utf-8"))
    compact = json.loads((out_dir / "control_report.compact.json").read_text(encoding="utf-8"))
    assert benchmark["duration_ms"] >= 0
    assert benchmark["rows_per_sec"] >= 0
    assert benchmark["symbols_per_sec"] >= 0
    assert benchmark["requested_symbols"] == 1
    assert "requested_symbols" not in compact["observed_state"]
    assert compact["observed_state"]["requested_symbols_sample"] == ["600000.SH"]
    assert (out_dir / "benchmark.csv").exists()
    assert (out_dir / "control_report.compact.json").exists()


def test_pipeline_scan_results_cover_all_requested_symbols(tmp_path):
    from app.research.pipeline.cli import main

    out_dir = tmp_path / "scan-run"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--symbols",
                "600000.SH,000001.SZ",
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    rows = json.loads((out_dir / "scan_results.json").read_text(encoding="utf-8"))
    control = json.loads(
        (out_dir / "morning_package" / "control_report.json").read_text(encoding="utf-8")
    )
    csv_text = (out_dir / "scan_results.csv").read_text(encoding="utf-8")

    assert [row["symbol"] for row in rows] == ["600000.SH", "000001.SZ"]
    assert all(row["visible_rows"] > 0 for row in rows)
    assert all(row["backtest_status"] == "completed" for row in rows)
    assert control["observed_state"]["requested_symbols"] == ["000001.SZ", "600000.SH"]
    assert control["observed_state"]["visible_symbols"] == ["000001.SZ", "600000.SH"]
    assert control["observed_state"]["empty_or_missing_symbols"] == []
    assert control["observed_state"]["coverage_ratio"] == 1.0
    assert control["invariants"]["symbol_coverage_complete"] is True
    assert "symbol,visible_rows,latest_event_time" in csv_text


def test_pipeline_resolves_data_root_manifest_entry(tmp_path):
    from app.research.pipeline.cli import main

    data_root = tmp_path / "lake"
    parquet_dir = data_root / "pit"
    manifest_dir = data_root / "_manifest"
    parquet_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    parquet_path = parquet_dir / "kline_daily_pit.parquet"
    _pit_frame().write_parquet(parquet_path)
    (manifest_dir / "coverage.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "dataset": "kline_daily",
                        "path": "pit/kline_daily_pit.parquet",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "data-root-run"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--symbols",
                "600000.SH",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    snapshot_path = next(out_dir.glob("*.snapshot.json"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert manifest["data_versions"][0]["name"] == "ashare.kline_daily_pit"
    assert snapshot["artifact"]["uri"].endswith("visible_kline_daily_pit.parquet")


def test_pipeline_cli_can_scan_data_root_manifest_universe(tmp_path):
    from app.research.pipeline.cli import main

    data_root = tmp_path / "lake"
    parquet_dir = data_root / "pit"
    manifest_dir = data_root / "_manifest"
    parquet_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    parquet_path = parquet_dir / "kline_daily_pit.parquet"
    pl.concat([_pit_frame("600000.SH"), _pit_frame("000001.SZ")]).write_parquet(parquet_path)
    (manifest_dir / "coverage.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "dataset": "ashare.kline_daily_pit",
                        "path": "pit/kline_daily_pit.parquet",
                        "symbols": ["600000.SH", "000001.SZ"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "full-universe-run"
    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    rows = json.loads((out_dir / "scan_results.json").read_text(encoding="utf-8"))
    assert [row["symbol"] for row in rows] == ["000001.SZ", "600000.SH"]


def test_data_root_resolver_rejects_missing_pit_parquet(tmp_path):
    import pytest

    from app.research.pipeline.data_lake import DataLakeResolutionError
    from app.research.pipeline.runner import PipelineConfig, run_pipeline

    with pytest.raises(DataLakeResolutionError, match="no usable kline daily"):
        run_pipeline(
            PipelineConfig(
                package_date=date(2026, 5, 17),
                symbols=("600000.SH",),
                out_dir=tmp_path / "out",
                data_root=tmp_path,
                code_commit="abc1234",
            )
        )


def test_pipeline_normalizes_legacy_kline_daily_from_data_root(tmp_path):
    from app.research.pipeline.cli import main

    data_root = tmp_path / "legacy-lake"
    data_root.mkdir()
    legacy = pl.DataFrame(
        {
            "code": ["600000"] * 45,
            "market": [1] * 45,
            "date": [date(2026, 4, 3) + timedelta(days=i) for i in range(45)],
            "open": [10.0 + i * 0.08 for i in range(45)],
            "high": [10.1 + i * 0.08 for i in range(45)],
            "low": [9.9 + i * 0.08 for i in range(45)],
            "close": [10.05 + i * 0.08 for i in range(45)],
            "volume": [1_000_000 + i for i in range(45)],
            "amount": [10_000_000.0 + i for i in range(45)],
        }
    )
    legacy.write_parquet(data_root / "kline_daily.parquet")
    out_dir = tmp_path / "legacy-run"

    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--symbols",
                "600000.SH",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    normalized_path = out_dir / "normalized_kline_daily_pit.parquet"
    assert normalized_path.exists()
    normalized = pl.read_parquet(normalized_path)
    assert {"symbol", "event_time", "available_at", "source_updated_at"}.issubset(
        set(normalized.columns)
    )
    assert normalized["symbol"][0] == "600000.SH"
    snapshot_path = next(out_dir.glob("*.snapshot.json"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["artifact"]["uri"].endswith("visible_kline_daily_pit.parquet")


def test_ledger_backtester_records_rejected_limit_up_buy():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15), date(2026, 5, 16)],
            "symbol": ["600000.SH", "600000.SH"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [1_000_000, 1_000_000],
            "limit_up": [True, False],
        }
    )
    signals = pl.DataFrame({"date": [date(2026, 5, 15), date(2026, 5, 16)], "signal": [1, 0]})

    result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert result.orders[0].status == "rejected"
    assert result.orders[0].reject_reason == "limit_up"
    assert result.fills == ()


def test_ledger_backtester_is_long_only_for_empty_position_sell_signal():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15), date(2026, 5, 16)],
            "symbol": ["600000.SH", "600000.SH"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [1_000_000, 1_000_000],
        }
    )
    signals = pl.DataFrame({"date": [date(2026, 5, 15), date(2026, 5, 16)], "signal": [-1, 0]})

    result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert result.orders == ()
    assert result.fills == ()
    assert [row.position_qty for row in result.daily_ledger] == [0, 0]


def test_ledger_backtester_records_rejected_limit_down_sell():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15), date(2026, 5, 16)],
            "symbol": ["600000.SH", "600000.SH"],
            "open": [10.0, 9.5],
            "high": [10.2, 9.6],
            "low": [9.9, 9.4],
            "close": [10.1, 9.5],
            "volume": [1_000_000, 1_000_000],
            "limit_down": [False, True],
        }
    )
    signals = pl.DataFrame({"date": [date(2026, 5, 15), date(2026, 5, 16)], "signal": [1, -1]})

    result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert [order.status for order in result.orders] == ["filled", "rejected"]
    assert result.orders[1].side == "sell"
    assert result.orders[1].reject_reason == "limit_down"
    assert len(result.fills) == 1
    assert result.daily_ledger[-1].position_qty == result.fills[0].qty


def test_ledger_backtester_records_rejected_suspended_orders():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    buy_bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15)],
            "symbol": ["600000.SH"],
            "open": [10.0],
            "high": [10.2],
            "low": [9.9],
            "close": [10.1],
            "volume": [1_000_000],
            "is_suspended": [True],
        }
    )
    buy_signals = pl.DataFrame({"date": [date(2026, 5, 15)], "signal": [1]})

    buy_result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        buy_bars,
        buy_signals,
        symbol="600000.SH",
    )

    sell_bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15), date(2026, 5, 16)],
            "symbol": ["600000.SH", "600000.SH"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [1_000_000, 1_000_000],
            "is_suspended": [False, True],
        }
    )
    sell_signals = pl.DataFrame(
        {"date": [date(2026, 5, 15), date(2026, 5, 16)], "signal": [1, -1]}
    )

    sell_result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        sell_bars,
        sell_signals,
        symbol="600000.SH",
    )

    assert buy_result.orders[0].status == "rejected"
    assert buy_result.orders[0].reject_reason == "suspended"
    assert buy_result.fills == ()
    assert [order.status for order in sell_result.orders] == ["filled", "rejected"]
    assert sell_result.orders[1].reject_reason == "suspended"
    assert len(sell_result.fills) == 1


def test_ledger_backtester_records_rejected_not_tradable_orders():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15)],
            "symbol": ["600000.SH"],
            "open": [10.0],
            "high": [10.2],
            "low": [9.9],
            "close": [10.1],
            "volume": [1_000_000],
            "is_tradable": [False],
            "reason": ["delisted"],
        }
    )
    signals = pl.DataFrame({"date": [date(2026, 5, 15)], "signal": [1]})

    result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert result.orders[0].status == "rejected"
    assert result.orders[0].reject_reason == "delisted"


def test_ledger_backtester_rejects_same_day_t_plus_one_sell():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "event_time": [
                datetime(2026, 5, 15, 10, tzinfo=UTC),
                datetime(2026, 5, 15, 14, tzinfo=UTC),
            ],
            "symbol": ["600000.SH", "600000.SH"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [1_000_000, 1_000_000],
        }
    )
    signals = pl.DataFrame(
        {
            "event_time": [
                datetime(2026, 5, 15, 10, tzinfo=UTC),
                datetime(2026, 5, 15, 14, tzinfo=UTC),
            ],
            "signal": [1, -1],
        }
    )

    result = AShareLedgerBacktester(AShareBacktestConfig(enforce_t_plus_one=True)).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert [order.status for order in result.orders] == ["filled", "rejected"]
    assert result.orders[1].reject_reason == "t_plus_one"
    assert result.daily_ledger[-1].position_qty == result.fills[0].qty


def test_ledger_backtester_does_not_look_ahead_to_future_signals():
    import polars as pl

    from app.research.backtest import AShareBacktestConfig, AShareLedgerBacktester

    bars = pl.DataFrame(
        {
            "date": [date(2026, 5, 15), date(2026, 5, 16), date(2026, 5, 17)],
            "symbol": ["600000.SH", "600000.SH", "600000.SH"],
            "open": [10.0, 10.1, 10.2],
            "high": [10.2, 10.3, 10.4],
            "low": [9.9, 10.0, 10.1],
            "close": [10.1, 10.2, 10.3],
            "volume": [1_000_000, 1_000_000, 1_000_000],
        }
    )
    signals = pl.DataFrame({"date": [date(2026, 5, 17)], "signal": [1]})

    result = AShareLedgerBacktester(AShareBacktestConfig()).run(
        bars,
        signals,
        symbol="600000.SH",
    )

    assert [order.date for order in result.orders] == [date(2026, 5, 17)]
    assert [row.position_qty for row in result.daily_ledger[:2]] == [0, 0]
    assert result.daily_ledger[2].position_qty == result.fills[0].qty


def test_adjusted_factor_does_not_pollute_raw_execution_price(tmp_path):
    from app.research.pipeline import PipelineConfig, run_pipeline

    data_root = tmp_path / "lake"
    pit_dir = data_root / "pit"
    pit_dir.mkdir(parents=True)
    kline = _pit_frame()
    kline.write_parquet(pit_dir / "kline_daily_pit.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH"] * kline.height,
            "market": ["SH"] * kline.height,
            "event_time": kline["event_time"].to_list(),
            "available_at": kline["available_at"].to_list(),
            "source_updated_at": kline["source_updated_at"].to_list(),
            "adjustment_type": ["qfq"] * kline.height,
            "factor": [2.0] * kline.height,
        }
    ).write_parquet(pit_dir / "adjustment_factor_pit.parquet")

    out_dir = tmp_path / "adjusted-run"
    run_pipeline(
        PipelineConfig(
            package_date=date(2026, 5, 17),
            symbols=("600000.SH",),
            out_dir=out_dir,
            data_root=data_root,
            code_commit="abc1234",
        )
    )

    visible = pl.read_parquet(out_dir / "visible_kline_daily_pit.parquet")
    orders = pl.read_parquet(out_dir / "backtest_orders.parquet")

    assert "adjusted_close" in visible.columns
    assert visible["adjusted_close"][0] == visible["close"][0] * 2
    assert orders.filter(pl.col("status") == "filled")["intended_price"][0] in visible["close"].to_list()


def test_research_pipeline_layer_has_no_trading_imports():
    package_dir = Path(__file__).resolve().parents[2] / "app" / "research" / "pipeline"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "app.trading" not in source
    assert "app.trade" not in source
    assert "quant_terminal.trade" not in source


def test_ashare_control_plane_does_not_depend_on_duckdb_store():
    package_dir = Path(__file__).resolve().parents[2] / "app" / "research" / "pipeline"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "import duckdb" not in source
    assert "app.data.store" not in source
    assert "DuckDBStore" not in source


def test_pipeline_filters_sell_signals_for_buy_only_mvp(tmp_path):
    import polars as pl

    from app.research.pipeline.cli import main

    data_root = tmp_path / "sell-lake"
    data_root.mkdir()
    legacy = pl.DataFrame(
        {
            "code": ["600000"] * 45,
            "market": [1] * 45,
            "date": [date(2026, 4, 3) + timedelta(days=i) for i in range(45)],
            "open": [20.0 - i * 0.1 for i in range(45)],
            "high": [20.1 - i * 0.1 for i in range(45)],
            "low": [19.9 - i * 0.1 for i in range(45)],
            "close": [20.0 - i * 0.1 for i in range(45)],
            "volume": [1_000_000 + i for i in range(45)],
            "amount": [20_000_000.0 + i for i in range(45)],
        }
    )
    legacy.write_parquet(data_root / "kline_daily.parquet")
    out_dir = tmp_path / "sell-run"

    assert (
        main(
            [
                "--date",
                "2026-05-17",
                "--symbols",
                "600000.SH",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    intents = json.loads(
        (out_dir / "morning_package" / "trading_intents.json").read_text(encoding="utf-8")
    )
    audit = json.loads((out_dir / "morning_package" / "audit.json").read_text(encoding="utf-8"))
    assert intents == []
    assert audit["decision"] == "observe"


def test_parquet_pit_store_filters_future_available_rows():
    from app.research.pipeline.runner import ParquetPITStore
    from app.research.pit import PITDataset, PointInTimeQuery
    from app.research.models import Market

    as_of = datetime(2026, 5, 17, 12, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH"],
            "event_time": [
                datetime(2026, 5, 16, tzinfo=UTC),
                datetime(2026, 5, 17, tzinfo=UTC),
            ],
            "available_at": [
                datetime(2026, 5, 16, 16, tzinfo=UTC),
                datetime(2026, 5, 17, 16, tzinfo=UTC),
            ],
            "close": [10.0, 99.0],
        }
    )

    result = ParquetPITStore(frame).query(
        PointInTimeQuery(
            dataset=PITDataset.KLINE_DAILY,
            market=Market.ASHARE,
            symbol="600000.SH",
            as_of=as_of,
        )
    )

    assert result["close"].to_list() == [10.0]


def test_ashare_pipeline_control_counts_future_rows(tmp_path):
    from app.research.pipeline import PipelineConfig, run_pipeline

    frame = _pit_frame()
    future = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "market": ["SH"],
            "event_time": [datetime(2026, 5, 18, tzinfo=UTC)],
            "available_at": [datetime(2026, 5, 18, 16, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 5, 18, 17, tzinfo=UTC)],
            "open": [99.0],
            "high": [100.0],
            "low": [98.0],
            "close": [99.0],
            "volume": [1_000_000],
            "amount": [99_000_000.0],
        }
    )
    path = tmp_path / "ashare_with_future.parquet"
    pl.concat([frame, future]).write_parquet(path)

    result = run_pipeline(
        PipelineConfig(
            package_date=date(2026, 5, 17),
            symbols=("600000.SH",),
            out_dir=tmp_path / "out",
            pit_parquet=path,
            code_commit="abc1234",
        )
    )

    control = json.loads(
        (tmp_path / "out" / "morning_package" / "control_report.json").read_text(encoding="utf-8")
    )
    visible = pl.read_parquet(tmp_path / "out" / "visible_kline_daily_pit.parquet")

    assert control["observed_state"]["rejected_future_rows"] == 1
    assert visible["close"].to_list()[-1] != 99.0
    assert result.control_report.invariants["pit_visible_at_as_of"] is True


def test_ashare_control_rejects_drawdown_over_limit():
    from app.research.backtest import LedgerBacktestResult
    from app.research.pipeline.control import (
        AShareControlConfig,
        ASharePITValidationSummary,
        evaluate_ashare_control,
    )

    report = evaluate_ashare_control(
        config=AShareControlConfig(position_cap=0.05),
        pit_summary=ASharePITValidationSummary(
            input_rows=45,
            visible_rows=45,
            rejected_future_rows=0,
            duplicate_rows=0,
        ),
        factor_values=10,
        signals=1,
        ledger_results={
            "600000.SH": LedgerBacktestResult(
                symbol="600000.SH",
                window="2026-04-03/2026-05-16",
                initial_capital=1_000_000.0,
                final_equity=700_000.0,
                total_return=-0.3,
                max_drawdown=0.5,
            )
        },
        manifest_hash="a" * 64,
        dataset_version="2026-05-17",
        package_decision="trade",
    )

    assert report.decision == "reject"
    assert "max drawdown exceeds control limit" in report.error_terms["reject_reasons"]


def test_ashare_control_rejects_when_completed_backtests_do_not_make_money():
    from app.research.backtest import LedgerBacktestResult, TradeRecord
    from app.research.pipeline.control import (
        AShareControlConfig,
        ASharePITValidationSummary,
        evaluate_ashare_control,
    )

    report = evaluate_ashare_control(
        config=AShareControlConfig(position_cap=0.05),
        pit_summary=ASharePITValidationSummary(
            input_rows=45,
            visible_rows=45,
            rejected_future_rows=0,
            duplicate_rows=0,
        ),
        factor_values=10,
        signals=1,
        ledger_results={
            "000001.SZ": LedgerBacktestResult(
                symbol="000001.SZ",
                window="2026-04-03/2026-05-16",
                initial_capital=1_000_000.0,
                final_equity=997_000.0,
                total_return=-0.003,
                max_drawdown=0.01,
                trades=(
                    TradeRecord(
                        symbol="000001.SZ",
                        entry_date=date(2026, 5, 1),
                        exit_date=date(2026, 5, 2),
                        qty=100.0,
                        entry_price=10.0,
                        exit_price=9.9,
                        pnl=-10.0,
                        return_pct=-0.01,
                    ),
                ),
            )
        },
        manifest_hash="a" * 64,
        dataset_version="2026-05-17",
        package_decision="trade",
    )

    assert report.decision == "reject"
    assert "no completed backtest has positive return" in report.error_terms["reject_reasons"]


def test_ashare_control_observes_missing_symbols_between_thresholds():
    from app.research.pipeline.control import (
        AShareControlConfig,
        ASharePITValidationSummary,
        evaluate_ashare_control,
    )

    report = evaluate_ashare_control(
        config=AShareControlConfig(
            position_cap=0.05,
            min_symbol_coverage_ratio=1.0,
            reject_symbol_coverage_ratio=0.40,
        ),
        pit_summary=ASharePITValidationSummary(
            input_rows=45,
            visible_rows=45,
            rejected_future_rows=0,
            duplicate_rows=0,
        ),
        requested_symbols=("600000.SH", "000001.SZ"),
        visible_symbols=("600000.SH",),
        factor_values=10,
        signals=1,
        ledger_results={},
        manifest_hash="a" * 64,
        dataset_version="2026-05-17",
        package_decision="trade",
    )

    assert report.decision == "observe"
    assert report.observed_state["coverage_ratio"] == 0.5
    assert report.observed_state["empty_or_missing_symbols"] == ("000001.SZ",)
    assert "symbol coverage below observe threshold" in report.error_terms["observe_reasons"]
    assert report.invariants["symbol_coverage_complete"] is False


def test_ashare_control_rejects_low_symbol_coverage_but_preserves_duplicate_halt():
    from app.research.pipeline.control import (
        AShareControlConfig,
        ASharePITValidationSummary,
        evaluate_ashare_control,
    )

    report = evaluate_ashare_control(
        config=AShareControlConfig(position_cap=0.05),
        pit_summary=ASharePITValidationSummary(
            input_rows=45,
            visible_rows=45,
            rejected_future_rows=0,
            duplicate_rows=1,
        ),
        requested_symbols=("600000.SH", "000001.SZ"),
        visible_symbols=("600000.SH",),
        factor_values=10,
        signals=1,
        ledger_results={},
        manifest_hash="a" * 64,
        dataset_version="2026-05-17",
        package_decision="trade",
    )

    assert report.decision == "halt"
    assert "duplicate PIT event rows" in report.halt_reasons
    assert "symbol coverage below reject threshold" in report.error_terms["reject_reasons"]


def test_ashare_lake_scripts_validate_and_build_coverage(tmp_path):
    import importlib.util

    root = tmp_path / "lake"
    parquet = root / "pit" / "kline_daily_pit.parquet"
    parquet.parent.mkdir(parents=True)
    _pit_frame().write_parquet(parquet)

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    validate_module = _load_script(scripts_dir / "validate-ashare-lake.py")
    coverage_module = _load_script(scripts_dir / "build-ashare-coverage.py")

    assert validate_module.main(["--root", str(root)]) == 0
    assert coverage_module.main(["--root", str(root)]) == 0

    manifest = json.loads((root / "_manifest" / "coverage.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["dataset"] == "ashare.kline_daily_pit"
    assert manifest["items"][0]["path"] == "pit/kline_daily_pit.parquet"


def test_ashare_calendar_snapshot_is_built_from_lake_observed_days(tmp_path):
    root = tmp_path / "lake"
    parquet = root / "pit" / "kline_daily_pit.parquet"
    parquet.parent.mkdir(parents=True)
    pl.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": datetime(2026, 4, 3, tzinfo=UTC),
                "available_at": datetime(2026, 4, 3, 16, tzinfo=UTC),
                "source_updated_at": datetime(2026, 4, 3, 17, tzinfo=UTC),
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": 1010.0,
            },
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": datetime(2026, 4, 5, tzinfo=UTC),
                "available_at": datetime(2026, 4, 5, 16, tzinfo=UTC),
                "source_updated_at": datetime(2026, 4, 5, 17, tzinfo=UTC),
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.2,
                "volume": 100,
                "amount": 1020.0,
            },
        ]
    ).write_parquet(parquet)

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    calendar_module = _load_script(scripts_dir / "build-ashare-calendar.py")

    assert (
        calendar_module.main(
            [
                "--root",
                str(root),
                "--start",
                "2026-04-03",
                "--end",
                "2026-04-06",
            ]
        )
        == 0
    )

    calendar = pl.read_parquet(root / "calendar" / "trading_calendar.parquet")
    report = json.loads((root / "_manifest" / "trading_calendar.json").read_text(encoding="utf-8"))
    assert report["dataset"] == "ashare.trading_calendar"
    assert report["source"] == "tdx_lake_observed"
    assert report["row_count"] == 4
    assert calendar.filter(pl.col("is_trading_day")).height == 2
    assert report["anomalies"]["weekday_without_bars"] == [
        {"date": "2026-04-06", "market": "SH"}
    ]
    assert report["anomalies"]["weekend_with_bars"] == [
        {
            "date": "2026-04-05",
            "market": "SH",
            "observed_rows": 1,
            "observed_symbols": 1,
        }
    ]


def test_ingest_script_normalizes_tdxcli_scan_out_dir(tmp_path):
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    ingest_module = _load_script(scripts_dir / "ingest-ashare-bridge.py")
    coverage_module = _load_script(scripts_dir / "build-ashare-coverage.py")

    scan_dir = tmp_path / "scan" / "market=sh" / "year=2026"
    scan_dir.mkdir(parents=True)
    (tmp_path / "scan" / "universe_manifest.json").write_text(
        json.dumps(
            {
                "requested_symbols": ["600008.SH", "000001.SZ", "300999.SZ"],
                "processed_symbols": ["600008.SH", "000001.SZ", "300999.SZ"],
                "success_symbols": ["600008.SH"],
                "empty_symbols": ["000001.SZ"],
                "failed_symbols": [
                    {"symbol": "300999.SZ", "reason": "tdx scan failed"}
                ],
                "start": "2026-04-01",
                "end": "2026-05-16",
            }
        ),
        encoding="utf-8",
    )
    pl.DataFrame(
        {
            "market": ["sh"],
            "code": ["600008"],
            "name": ["sample"],
            "date": [date(2026, 4, 1)],
            "year": [2026],
            "open_i64": [31600],
            "high_i64": [31900],
            "low_i64": [31100],
            "close_i64": [31300],
            "amount_i64": [2081723360000],
            "volume": [66412520],
        }
    ).write_parquet(scan_dir / "part-000001.parquet")

    lake = tmp_path / "lake"
    assert (
        ingest_module.main(
            [
                "--scan-out-dir",
                str(tmp_path / "scan"),
                "--start",
                "2026-04-01",
                "--end",
                "2026-05-16",
                "--out-root",
                str(lake),
            ]
        )
        == 0
    )

    normalized = pl.read_parquet(lake / "pit" / "kline_daily_pit.parquet")
    row = normalized.row(0, named=True)
    assert row["symbol"] == "600008.SH"
    assert row["open"] == 3.16
    assert row["amount"] == 208172336.0

    universe = json.loads((lake / "_manifest" / "universe_manifest.json").read_text(encoding="utf-8"))
    assert universe["requested"]["symbols"] == ["000001.SZ", "300999.SZ", "600008.SH"]
    assert universe["processed"]["count"] == 3
    assert universe["success"]["symbols"] == ["600008.SH"]
    assert universe["empty"]["symbols"] == ["000001.SZ"]
    assert universe["failure"]["symbols"] == ["300999.SZ"]
    assert universe["failure"]["items"][0]["reason"] == "tdx scan failed"
    assert universe["pit_symbols"]["symbols"] == ["600008.SH"]
    assert universe["universe"]["symbols"] == ["000001.SZ", "300999.SZ", "600008.SH"]

    assert coverage_module.main(["--root", str(lake)]) == 0
    coverage = json.loads((lake / "_manifest" / "coverage.json").read_text(encoding="utf-8"))
    item = coverage["items"][0]
    assert item["requested_symbols"] == ["000001.SZ", "300999.SZ", "600008.SH"]
    assert item["success_symbols"] == ["600008.SH"]
    assert item["empty_symbols"] == ["000001.SZ"]
    assert item["failure_symbols"] == ["300999.SZ"]
    assert item["pit_symbols"] == ["600008.SH"]


def test_incremental_ingest_overwrites_same_symbol_event_time(tmp_path):
    root = tmp_path / "lake"
    parquet = root / "pit" / "kline_daily_pit.parquet"
    parquet.parent.mkdir(parents=True)
    original = _pit_frame().filter(pl.col("event_time") <= datetime(2026, 4, 5, tzinfo=UTC))
    original.write_parquet(parquet)
    batch = pl.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH"],
            "market": ["SH", "SH"],
            "event_time": [datetime(2026, 4, 5, tzinfo=UTC), datetime(2026, 4, 6, tzinfo=UTC)],
            "available_at": [datetime(2026, 4, 5, 16, tzinfo=UTC), datetime(2026, 4, 6, 16, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 4, 5, 17, tzinfo=UTC), datetime(2026, 4, 6, 17, tzinfo=UTC)],
            "open": [20.0, 21.0],
            "high": [20.5, 21.5],
            "low": [19.5, 20.5],
            "close": [20.25, 21.25],
            "volume": [200, 210],
            "amount": [4050.0, 4462.5],
        }
    )
    batch_path = tmp_path / "batch.parquet"
    batch.write_parquet(batch_path)
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    incremental_module = _load_script(scripts_dir / "ingest-ashare-incremental.py")

    assert (
        incremental_module.main(
            [
                "--out-root",
                str(root),
                "--batch-parquet",
                str(batch_path),
                "--start",
                "2026-04-05",
                "--end",
                "2026-04-06",
            ]
        )
        == 0
    )

    merged = pl.read_parquet(parquet)
    manifest = json.loads((root / "_manifest" / "incremental_batch.json").read_text(encoding="utf-8"))
    assert merged.height == 4
    assert merged.filter(pl.col("event_time") == datetime(2026, 4, 5, tzinfo=UTC))["close"].to_list() == [20.25]
    assert manifest["batch_rows"] == 2
    assert manifest["previous_content_hash"]
    assert manifest["new_content_hash"]
    assert (root / "_manifest" / "previous_content_hash").exists()
    assert (root / "_manifest" / "new_content_hash").exists()


def test_pipeline_normalize_legacy_uses_trading_calendar_for_available_at(tmp_path):
    from app.research.pipeline.calendar import build_calendar_snapshot
    from app.research.pipeline.cli import main

    data_root = tmp_path / "lake"
    data_root.mkdir()
    legacy = pl.DataFrame(
        {
            "code": ["600000"] * 45,
            "market": [1] * 45,
            "date": [date(2026, 4, 3) + timedelta(days=i) for i in range(45)],
            "open": [10.0 + i * 0.08 for i in range(45)],
            "high": [10.1 + i * 0.08 for i in range(45)],
            "low": [9.9 + i * 0.08 for i in range(45)],
            "close": [10.05 + i * 0.08 for i in range(45)],
            "volume": [1_000_000 + i for i in range(45)],
            "amount": [10_000_000.0 + i for i in range(45)],
        }
    )
    legacy.write_parquet(data_root / "kline_daily.parquet")

    calendar_seed = pl.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": datetime(2026, 5, 15, tzinfo=UTC),
                "available_at": datetime(2026, 5, 15, 16, tzinfo=UTC),
                "source_updated_at": datetime(2026, 5, 15, 17, tzinfo=UTC),
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 1_000_000,
                "amount": 10_100_000.0,
            },
            {
                "symbol": "600000.SH",
                "market": "SH",
                "event_time": datetime(2026, 5, 18, tzinfo=UTC),
                "available_at": datetime(2026, 5, 18, 16, tzinfo=UTC),
                "source_updated_at": datetime(2026, 5, 18, 17, tzinfo=UTC),
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.2,
                "volume": 1_100_000,
                "amount": 11_220_000.0,
            },
        ]
    )
    calendar_seed_path = tmp_path / "calendar_seed.parquet"
    calendar_seed.write_parquet(calendar_seed_path)
    build_calendar_snapshot(
        pit_path=calendar_seed_path,
        out_root=data_root,
        start=date(2026, 5, 15),
        end=date(2026, 5, 18),
    )

    out_dir = tmp_path / "run"
    assert (
        main(
            [
                "--date",
                "2026-05-18",
                "--symbols",
                "600000.SH",
                "--data-root",
                str(data_root),
                "--out",
                str(out_dir),
                "--code-commit",
                "abc1234",
            ]
        )
        == 0
    )

    normalized = pl.read_parquet(out_dir / "normalized_kline_daily_pit.parquet")
    fri_row = normalized.filter(pl.col("event_time") == datetime(2026, 5, 15, tzinfo=UTC))
    assert fri_row.height == 1
    assert fri_row["available_at"].to_list()[0] == datetime(2026, 5, 18, 9, 30, tzinfo=UTC)
    assert fri_row["source_updated_at"].to_list()[0] == datetime(2026, 5, 18, 9, 30, tzinfo=UTC)


def test_ashare_tradability_producer_writes_pit_parquet(tmp_path):
    from app.research.ashare_data_contract import (
        ASharePITDataset,
        validate_pit_columns,
    )

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    module = _load_script(scripts_dir / "ingest-ashare-tradability.py")

    sh_fixture = tmp_path / "sh.json"
    sh_fixture.write_text(
        json.dumps(
            {
                "market": "sh",
                "count": 3,
                "items": [
                    {"code": "600000", "name": "浦发银行", "market": "SH"},
                    {"code": "600519", "name": "ST长生", "market": "SH"},
                    {"code": "600730", "name": "中国高科退", "market": "SH"},
                ],
            }
        ),
        encoding="utf-8",
    )
    sz_fixture = tmp_path / "sz.json"
    sz_fixture.write_text(
        json.dumps(
            {
                "market": "sz",
                "count": 1,
                "items": [{"code": "000001", "name": "平安银行", "market": "SZ"}],
            }
        ),
        encoding="utf-8",
    )

    lake = tmp_path / "lake"
    rc = module.main(
        [
            "--security-list-json",
            str(sh_fixture),
            "--security-list-json",
            str(sz_fixture),
            "--out-root",
            str(lake),
            "--event-time",
            "2026-05-18",
        ]
    )
    assert rc == 0

    parquet = lake / "pit" / "tradability_status_pit.parquet"
    frame = pl.read_parquet(parquet)
    assert frame.height == 4

    check = validate_pit_columns(ASharePITDataset.TRADABILITY_STATUS, frame.columns)
    assert check.passed, f"missing required columns: {check.missing_columns}"

    by_symbol = {row["symbol"]: row for row in frame.iter_rows(named=True)}
    assert by_symbol["600519.SH"]["is_st"] is True
    assert "st_name_prefix" in by_symbol["600519.SH"]["reason"]
    assert by_symbol["600730.SH"]["is_tradable"] is False
    assert "delisted_name_marker" in by_symbol["600730.SH"]["reason"]
    assert by_symbol["600000.SH"]["is_st"] is False
    assert by_symbol["600000.SH"]["is_tradable"] is True
    assert by_symbol["000001.SZ"]["market"] == "SZ"

    universe = json.loads(
        (lake / "_manifest" / "tradability_universe.json").read_text(encoding="utf-8")
    )
    assert universe["per_market_counts"] == {"SH": 3, "SZ": 1}
    assert universe["rows"] == 4
    assert any("is_suspended" in gap for gap in universe["honest_gaps"])


def test_ashare_xdxr_producer_writes_adjustment_and_corporate_action(tmp_path):
    from app.research.ashare_data_contract import (
        ASharePITDataset,
        validate_pit_columns,
    )

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    module = _load_script(scripts_dir / "ingest-ashare-xdxr.py")

    payload_600000 = {
        "code": "600000",
        "market": "SH",
        "count": 2,
        "items": [
            {
                "market": 1,
                "code": "600000",
                "date": 20250604,
                "category": 1,
                "fh_qltp": 1.5,
                "pgj_qzgb": 0.0,
                "sg_hltp": 0.0,
                "pg_hzgb": 0.0,
            },
            {
                "market": 1,
                "code": "600000",
                "date": 20260612,
                "category": 1,
                "fh_qltp": 2.0,
                "pgj_qzgb": 3.5,
                "sg_hltp": 5.0,
                "pg_hzgb": 2.0,
            },
        ],
    }
    fixture = tmp_path / "600000.json"
    fixture.write_text(json.dumps(payload_600000), encoding="utf-8")

    lake = tmp_path / "lake"
    rc = module.main(
        ["--xdxr-json", str(fixture), "--out-root", str(lake)]
    )
    assert rc == 0

    adjustment = pl.read_parquet(lake / "pit" / "adjustment_factor_pit.parquet")
    check = validate_pit_columns(ASharePITDataset.ADJUSTMENT_FACTOR, adjustment.columns)
    assert check.passed, f"missing required columns: {check.missing_columns}"
    assert adjustment.height == 2

    rows = list(adjustment.iter_rows(named=True))
    cash_only = [r for r in rows if r["event_time"] == datetime(2025, 6, 4, tzinfo=UTC)][0]
    bonus_rights = [r for r in rows if r["event_time"] == datetime(2026, 6, 12, tzinfo=UTC)][0]
    assert cash_only["factor"] == 1.0
    assert bonus_rights["factor"] == 1.0 + 5.0 + 2.0
    assert cash_only["available_at"] == cash_only["event_time"]

    action = pl.read_parquet(lake / "pit" / "corporate_action_pit.parquet")
    assert action.height == 2
    second = action.filter(pl.col("event_time") == datetime(2026, 6, 12, tzinfo=UTC)).row(0, named=True)
    assert second["category"] == "chuquan_chuxi"
    assert second["cash_per_share"] == 2.0
    assert second["rights_price"] == 3.5
    assert second["bonus_ratio"] == 5.0
    assert second["rights_ratio"] == 2.0

    universe = json.loads(
        (lake / "_manifest" / "xdxr_universe.json").read_text(encoding="utf-8")
    )
    assert universe["rows"]["adjustment_factor"] == 2
    assert universe["per_symbol_counts"] == {"600000.SH": 2}


def _load_script(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
