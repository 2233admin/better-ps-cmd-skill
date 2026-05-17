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
        "signals.json",
        "factors.json",
        "scan_results.json",
        "scan_results.csv",
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

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    scan_results = json.loads((out_dir / "scan_results.json").read_text(encoding="utf-8"))
    metrics = json.loads((out_dir / "backtest" / "metrics.json").read_text(encoding="utf-8"))
    audit = json.loads(
        (out_dir / "morning_package" / "audit.json").read_text(encoding="utf-8")
    )

    assert len(manifest["manifest_hash"]) == 64
    assert manifest["code_commit"] == "abc1234"
    assert len(scan_results) == 1
    assert scan_results[0]["symbol"] == "600000.SH"
    assert scan_results[0]["visible_rows"] > 0
    assert scan_results[0]["backtest_status"] == "completed"
    assert "momentum_20d" in scan_results[0]
    assert metrics["metrics"]["max_drawdown"] >= 0
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


def test_research_pipeline_layer_has_no_trading_imports():
    package_dir = Path(__file__).resolve().parents[2] / "app" / "research" / "pipeline"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "app.trading" not in source
    assert "app.trade" not in source
    assert "quant_terminal.trade" not in source


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
            "event_time": [datetime(2026, 5, 17, tzinfo=UTC)],
            "available_at": [datetime(2026, 5, 17, 16, tzinfo=UTC)],
            "source_updated_at": [datetime(2026, 5, 17, 17, tzinfo=UTC)],
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

    assert control["observed_state"]["rejected_future_rows"] == 2
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


def _load_script(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
