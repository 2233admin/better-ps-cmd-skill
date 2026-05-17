"""Contract tests for crypto PIT data and ledger backtests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest


def test_crypto_pit_spec_declares_24_7_and_swap_boundaries():
    doc = Path(__file__).resolve().parents[3] / "docs" / "CRYPTO_DATA_PIT_SPEC.md"
    text = doc.read_text(encoding="utf-8")

    assert "source/raw -> normalized -> point-in-time" in text
    assert "event_time" in text
    assert "available_at" in text
    assert "market_type" in text
    assert "funding" in text
    assert "live_test" in text


def test_crypto_wheel_admission_policy_defaults_unknown_wheels_to_reject():
    from app.trading.adapters.crypto import load_wheel_admission_policy

    policy = load_wheel_admission_policy()

    assert policy.default_decision == "reject"
    assert policy.decision_for("unknown-grid-bot") == "reject"
    assert policy.decision_for("requests") == "allow"
    assert policy.decision_for("ccxt") == "candidate"
    assert policy.decision_for("freqtrade") == "reference_only"
    assert "places_orders_without_katana_intent" in policy.banned_capabilities


def test_runtime_crypto_wheel_import_requires_allow_decision():
    from app.trading.adapters.crypto import load_wheel_admission_policy

    policy = load_wheel_admission_policy()

    policy.assert_allowed_for_runtime("requests")
    with pytest.raises(ValueError, match="runtime imports require allow"):
        policy.assert_allowed_for_runtime("freqtrade")


def test_crypto_kline_contract_requires_visibility_and_market_type():
    from app.research.crypto_data_contract import CryptoPITDataset, validate_crypto_pit_columns

    result = validate_crypto_pit_columns(
        CryptoPITDataset.KLINE,
        {
            "inst_id",
            "venue",
            "event_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        },
    )

    assert not result.passed
    assert result.missing_columns == ("available_at", "market_type", "source_updated_at")


def test_complete_crypto_kline_contract_passes():
    from app.research.crypto_data_contract import CryptoPITDataset, KLINE_COLUMNS
    from app.research.crypto_data_contract import validate_crypto_pit_columns

    result = validate_crypto_pit_columns(CryptoPITDataset.KLINE, KLINE_COLUMNS)

    assert result.passed
    assert result.missing_columns == ()


def test_okx_candle_normalizer_outputs_crypto_kline_pit_schema():
    from app.research.crypto_data_contract import CryptoPITDataset, validate_crypto_pit_columns
    from app.trading.adapters.crypto import normalize_okx_candles

    frame = normalize_okx_candles(
        inst_id="BTC-USDT",
        market_type="spot",
        source_updated_at=datetime(2026, 5, 17, 12, tzinfo=UTC),
        candles=[
            [
                "1779019200000",
                "100.0",
                "101.0",
                "99.0",
                "100.5",
                "10.0",
                "1000.0",
                "1005.0",
            ]
        ],
    )
    result = validate_crypto_pit_columns(CryptoPITDataset.KLINE, frame.columns)

    assert result.passed
    assert frame["inst_id"].to_list() == ["BTC-USDT"]
    assert frame["venue"].to_list() == ["okx"]
    assert frame["quote_volume"].to_list() == [1005.0]


def test_okx_swap_normalizers_output_required_pit_schemas():
    from app.research.crypto_data_contract import CryptoPITDataset, validate_crypto_pit_columns
    from app.trading.adapters.crypto import (
        normalize_okx_funding_rates,
        normalize_okx_mark_index_prices,
        normalize_okx_open_interest,
    )

    source_updated_at = datetime(2026, 5, 17, 12, tzinfo=UTC)
    funding = normalize_okx_funding_rates(
        inst_id="BTC-USDT-SWAP",
        source_updated_at=source_updated_at,
        funding_rates=[{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001", "fundingTime": "1779019200000"}],
    )
    mark = normalize_okx_mark_index_prices(
        inst_id="BTC-USDT-SWAP",
        market_type="swap",
        source_updated_at=source_updated_at,
        prices=[{"instId": "BTC-USDT-SWAP", "markPx": "100.2", "idxPx": "100.0", "ts": "1779019200000"}],
    )
    oi = normalize_okx_open_interest(
        inst_id="BTC-USDT-SWAP",
        source_updated_at=source_updated_at,
        open_interest=[{"instId": "BTC-USDT-SWAP", "oi": "10000", "oiCcy": "100", "ts": "1779019200000"}],
    )

    assert validate_crypto_pit_columns(CryptoPITDataset.FUNDING_RATE, funding.columns).passed
    assert validate_crypto_pit_columns(CryptoPITDataset.MARK_PRICE, mark.columns).passed
    assert validate_crypto_pit_columns(CryptoPITDataset.OPEN_INTEREST, oi.columns).passed
    assert funding["market_type"].to_list() == ["swap"]
    assert mark["mark_price"].to_list() == [100.2]
    assert oi["open_interest"].to_list() == [10000.0]


def test_crypto_rejects_unknown_pit_dataset():
    from app.research.crypto_data_contract import require_crypto_pit_dataset

    with pytest.raises(ValueError, match="unsupported crypto PIT dataset"):
        require_crypto_pit_dataset("crypto.twitter_sentiment")


def test_crypto_spot_ledger_supports_fractional_qty_without_shorting():
    from app.research.backtest import CryptoBacktestConfig, CryptoLedgerBacktester

    bars = _crypto_bars([100_000.0, 101_000.0, 102_000.0])
    signals = pl.DataFrame({"event_time": bars["event_time"], "signal": [1, 1, 1]})

    result = CryptoLedgerBacktester(
        CryptoBacktestConfig(
            initial_capital=1_000.0,
            position_cap=0.5,
            taker_fee_rate=0.0,
            slippage_rate=0.0,
            market_type="spot",
        )
    ).run(bars, signals, symbol="BTC-USDT")

    assert result.orders[0].side == "buy"
    assert 0 < result.fills[0].qty < 1
    assert result.daily_ledger[-1].position_qty == result.fills[0].qty
    assert result.parameters["market_type"] == "spot"


def test_crypto_swap_ledger_supports_short_and_funding_costs():
    from app.research.backtest import CryptoBacktestConfig, CryptoLedgerBacktester

    bars = _crypto_bars([100.0, 95.0, 90.0], funding_rate=[0.001, 0.001, 0.001])
    signals = pl.DataFrame({"event_time": bars["event_time"], "signal": [-1, -1, 0]})

    result = CryptoLedgerBacktester(
        CryptoBacktestConfig(
            initial_capital=10_000.0,
            position_cap=0.2,
            taker_fee_rate=0.0,
            slippage_rate=0.0,
            market_type="swap",
            leverage=2.0,
            allow_short=True,
        )
    ).run(bars, signals, symbol="BTC-USDT-SWAP")

    assert result.orders[0].side == "short"
    assert result.orders[1].side == "cover"
    assert result.trades[0].pnl > 0
    assert result.parameters["allow_short"] is True


def test_crypto_fixture_pipeline_generates_session_package(tmp_path):
    import json

    from app.research.crypto_pipeline import CryptoPipelineConfig, run_crypto_pipeline

    out_dir = tmp_path / "crypto-run"
    result = run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT", "ETH-USDT"),
            out_dir=out_dir,
            code_commit="abc1234",
            market_type="spot",
        )
    )

    expected = [
        "manifest.json",
        "signals.json",
        "trade_intents.json",
        "factors.json",
        "factor_snapshot.json",
        "paper_reconciliation.json",
        "backtest/orders.csv",
        "backtest/fills.csv",
        "backtest/daily_ledger.csv",
        "backtest/trades.csv",
        "backtest/metrics.json",
        "portfolio/portfolio_orders.parquet",
        "portfolio/portfolio_fills.parquet",
        "portfolio/portfolio_positions.parquet",
        "portfolio/portfolio_equity_curve.parquet",
        "session_package/session_package.md",
        "session_package/audit.json",
        "session_package/signals.json",
        "session_package/trade_intents.json",
        "session_package/paper_reconciliation.json",
        "session_package/control_report.json",
        "session_package/control_report.md",
    ]
    for relative in expected:
        assert (out_dir / relative).exists(), relative

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    signals = json.loads((out_dir / "signals.json").read_text(encoding="utf-8"))
    audit = json.loads((out_dir / "session_package" / "audit.json").read_text(encoding="utf-8"))
    reconciliation = json.loads((out_dir / "paper_reconciliation.json").read_text(encoding="utf-8"))

    assert manifest["code_commit"] == "abc1234"
    assert manifest["data_versions"][0]["name"] == "crypto.kline_pit"
    assert all(signal["side"] in {"buy", "sell-to-flat"} for signal in signals)
    assert audit["assumptions"]["market_type"] == "spot"
    assert audit["control_decision"] in {"promote", "observe", "reject", "halt"}
    assert audit["control_report_path"] == "control_report.json"
    assert audit["evidence_level"] == "fixture_smoke"
    assert "paper_reconciliation" in audit
    assert {"cash_diff", "position_diff", "fill_count", "reject_reasons"}.issubset(reconciliation)
    assert result.session_package_dir == out_dir / "session_package"
    assert result.control_report.manifest_hash == manifest["manifest_hash"]


def test_crypto_funding_basis_factors_use_visible_rows_only():
    from app.research.crypto_pipeline.factors import calculate_crypto_factors

    frame = _crypto_bars([100.0, 100.5, 101.0], funding_rate=[0.001, 0.002, 0.999])
    visible = frame.filter(pl.col("available_at") <= datetime(2026, 5, 17, 1, 1, tzinfo=UTC))
    factors = calculate_crypto_factors(
        visible.with_columns(
            pl.col("close").alias("mark_price"),
            pl.col("close").alias("index_price"),
            pl.lit(10_000.0).alias("open_interest"),
        )
    )

    assert factors.height == 2
    assert factors["funding_annualized"].to_list() == [pytest.approx(1.095), pytest.approx(2.19)]


def test_crypto_pipeline_rejects_missing_kline_pit_columns(tmp_path):
    from app.research.crypto_pipeline import CryptoPipelineConfig, run_crypto_pipeline

    path = tmp_path / "bad.parquet"
    pl.DataFrame(
        {
            "inst_id": ["BTC-USDT"],
            "event_time": [datetime(2026, 5, 17, tzinfo=UTC)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
        }
    ).write_parquet(path)

    with pytest.raises(ValueError, match="crypto.kline_pit is missing PIT columns"):
        run_crypto_pipeline(
            CryptoPipelineConfig(
                session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
                inst_ids=("BTC-USDT",),
                out_dir=tmp_path / "out",
                pit_parquet=path,
            )
        )


def test_crypto_spot_pipeline_refuses_short_configuration(tmp_path):
    from app.research.crypto_pipeline import CryptoPipelineConfig

    with pytest.raises(ValueError, match="spot crypto pipeline cannot allow_short"):
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT",),
            out_dir=tmp_path,
            market_type="spot",
            allow_short=True,
        )


def test_crypto_swap_pipeline_requires_allow_short_for_short_signals(tmp_path):
    import json

    from app.research.crypto_pipeline import CryptoPipelineConfig, run_crypto_pipeline

    no_short_out = tmp_path / "swap-no-short"
    run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT-SWAP",),
            out_dir=no_short_out,
            market_type="swap",
            allow_short=False,
        )
    )
    no_short_signals = json.loads((no_short_out / "signals.json").read_text(encoding="utf-8"))
    assert all(signal["side"] != "short" for signal in no_short_signals)

    short_out = tmp_path / "swap-short"
    run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT-SWAP",),
            out_dir=short_out,
            market_type="swap",
            allow_short=True,
            leverage=2.0,
        )
    )
    short_signals = json.loads((short_out / "signals.json").read_text(encoding="utf-8"))
    audit = json.loads((short_out / "session_package" / "audit.json").read_text(encoding="utf-8"))
    control = json.loads((short_out / "session_package" / "control_report.json").read_text(encoding="utf-8"))
    assert any(signal["side"] == "short" for signal in short_signals)
    assert audit["assumptions"]["leverage"] == 2.0
    assert "funding_rate" in audit["assumptions"]["funding"]
    assert control["invariants"]["swap_short_requires_allow_short"] is True


def test_crypto_control_report_counts_future_rows_and_observes_empty_trades(tmp_path):
    import json

    from app.research.crypto_pipeline import CryptoPipelineConfig, run_crypto_pipeline

    event_times = [datetime(2026, 5, 16, tzinfo=UTC) + timedelta(hours=i) for i in range(30)]
    frame = pl.DataFrame(
        {
            "inst_id": ["BTC-USDT"] * 31,
            "venue": ["okx"] * 31,
            "market_type": ["spot"] * 31,
            "event_time": event_times + [datetime(2026, 5, 18, tzinfo=UTC)],
            "available_at": [value + timedelta(seconds=1) for value in event_times]
            + [datetime(2026, 5, 18, 1, tzinfo=UTC)],
            "source_updated_at": [value + timedelta(seconds=1) for value in event_times]
            + [datetime(2026, 5, 18, 1, tzinfo=UTC)],
            "open": [100.0] * 31,
            "high": [101.0] * 31,
            "low": [99.0] * 31,
            "close": [100.0] * 31,
            "volume": [1000.0] * 31,
            "quote_volume": [100_000.0] * 31,
            "funding_rate": [0.0] * 31,
            "funding_time": event_times + [datetime(2026, 5, 18, tzinfo=UTC)],
        }
    )
    path = tmp_path / "flat_pit.parquet"
    frame.write_parquet(path)

    run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT",),
            out_dir=tmp_path / "out",
            pit_parquet=path,
            market_type="spot",
        )
    )

    control = json.loads(
        (tmp_path / "out" / "session_package" / "control_report.json").read_text(encoding="utf-8")
    )
    assert control["observed_state"]["rejected_future_rows"] == 1
    assert control["decision"] == "observe"
    assert "no signals generated" in control["error_terms"]["observe_reasons"]


def test_crypto_swap_without_funding_evidence_is_smoke_only(tmp_path):
    import json

    from app.research.crypto_pipeline import CryptoPipelineConfig, run_crypto_pipeline

    event_times = [datetime(2026, 5, 16, tzinfo=UTC) + timedelta(hours=i) for i in range(30)]
    closes = [100.0 - i for i in range(30)]
    frame = pl.DataFrame(
        {
            "inst_id": ["BTC-USDT-SWAP"] * 30,
            "venue": ["okx"] * 30,
            "market_type": ["swap"] * 30,
            "event_time": event_times,
            "available_at": [value + timedelta(seconds=1) for value in event_times],
            "source_updated_at": [value + timedelta(seconds=1) for value in event_times],
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [1000.0] * 30,
            "quote_volume": [100_000.0] * 30,
        }
    )
    path = tmp_path / "swap_without_funding.parquet"
    frame.write_parquet(path)

    run_crypto_pipeline(
        CryptoPipelineConfig(
            session_date=datetime(2026, 5, 17, tzinfo=UTC).date(),
            inst_ids=("BTC-USDT-SWAP",),
            out_dir=tmp_path / "out",
            pit_parquet=path,
            market_type="swap",
            allow_short=True,
        )
    )

    control = json.loads(
        (tmp_path / "out" / "session_package" / "control_report.json").read_text(encoding="utf-8")
    )
    assert control["evidence_level"] == "fixture_smoke"
    assert "swap session lacks funding evidence" in control["warnings"][0]


def test_crypto_control_rejects_drawdown_over_limit():
    from app.research.backtest import LedgerBacktestResult
    from app.research.crypto_pipeline.control import ControlConfig, PITValidationSummary, evaluate_control

    report = evaluate_control(
        config=ControlConfig(market_type="spot", allow_short=False, leverage=1.0),
        pit_summary=PITValidationSummary(
            input_rows=30,
            visible_rows=30,
            rejected_future_rows=0,
            duplicate_rows=0,
            has_funding_evidence=True,
        ),
        factors=(),
        signals=(),
        ledger_results={
            "BTC-USDT": LedgerBacktestResult(
                symbol="BTC-USDT",
                window="2026-05-16/2026-05-17",
                initial_capital=100_000.0,
                final_equity=80_000.0,
                total_return=-0.2,
                max_drawdown=0.5,
            )
        },
        manifest_hash="a" * 64,
        dataset_version="2026-05-17",
    )

    assert report.decision == "reject"
    assert "max drawdown exceeds control limit" in report.error_terms["reject_reasons"]


def _crypto_bars(closes: list[float], funding_rate: list[float] | None = None) -> pl.DataFrame:
    start = datetime(2026, 5, 17, tzinfo=UTC)
    event_times = [start + timedelta(hours=i) for i in range(len(closes))]
    payload = {
        "inst_id": ["BTC-USDT-SWAP"] * len(closes),
        "venue": ["okx"] * len(closes),
        "market_type": ["swap"] * len(closes),
        "event_time": event_times,
        "available_at": [value + timedelta(seconds=1) for value in event_times],
        "source_updated_at": [value + timedelta(seconds=1) for value in event_times],
        "open": closes,
        "high": [value * 1.01 for value in closes],
        "low": [value * 0.99 for value in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
        "quote_volume": [value * 100.0 for value in closes],
    }
    if funding_rate is not None:
        payload["funding_rate"] = funding_rate
    return pl.DataFrame(payload)
