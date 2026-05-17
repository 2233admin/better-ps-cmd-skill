"""Contract tests for A-share morning package generation."""

from datetime import date

from app.research.manifest import sha256_json
from app.research.morning_package import (
    BacktestEvidence,
    CandidateSignal,
    EvidenceChain,
    EvidenceSource,
    ManualAction,
    PackageDecision,
    RiskBlock,
    build_morning_package,
    candidates_from_research_output,
)
from app.trading.adapters.qmt.morning_package_export import to_easyxt_bridge_payloads
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from app.research.morning_package.renderers import INTENT_FIELDS, render_markdown


def complete_candidate() -> CandidateSignal:
    return CandidateSignal(
        symbol="600000.SH",
        side="buy",
        thesis="PIT-safe momentum signal with bounded drawdown.",
        evidence_chain=EvidenceChain(
            sources=(
                EvidenceSource(
                    kind="dataset",
                    source_id="ashare.kline_daily_pit:2026-05-16",
                    description="PIT daily bar snapshot",
                ),
                EvidenceSource(
                    kind="backtest",
                    source_id="bt-001",
                    description="Walk-forward backtest",
                ),
            )
        ),
        backtest=BacktestEvidence(
            window="2024-01-01/2026-05-16",
            parameters={"lookback": 20},
            metrics={"sharpe": 1.1},
            max_drawdown=0.08,
        ),
        position_cap=0.05,
        failure_condition="Close below 20-day moving average.",
    )


def build_package(**overrides):
    payload = {
        "package_date": date(2026, 5, 17),
        "candidates": (complete_candidate(),),
        "dataset_version": "2026-05-16",
        "code_commit": "abc1234",
        "manifest_hash": sha256_json({"exp": "morning"}),
        "config_hash": sha256_json({"max": 3}),
        "risk": RiskBlock(data_integrity=("PIT lineage checked",)),
        "audit_context": {"risk_metrics": {"portfolio_var": 0.0}},
    }
    payload.update(overrides)
    return build_morning_package(**payload)


def test_missing_audit_identity_blocks_entire_package():
    package = build_package(dataset_version=None, code_commit=None, manifest_hash=None)

    assert package.decision == PackageDecision.DO_NOT_TRADE
    assert "missing dataset version" in package.do_not_trade_reasons
    assert "missing code commit" in package.do_not_trade_reasons
    assert "missing manifest hash" in package.do_not_trade_reasons
    assert package.intents[0].decision == PackageDecision.OBSERVE


def test_llm_auxiliary_text_cannot_be_evidence_source():
    candidate = CandidateSignal(
        symbol="600519.SH",
        side="buy",
        thesis="Narrative-only candidate.",
        evidence_chain=EvidenceChain(
            sources=(
                EvidenceSource(
                    kind="llm",
                    source_id="llm-note",
                    description="Model-written note",
                ),
            )
        ),
        backtest=complete_candidate().backtest,
        position_cap=0.03,
        failure_condition="Narrative invalidated.",
        auxiliary_notes="LLM can summarize but not prove the thesis.",
    )
    package = build_package(candidates=(candidate,))

    intent = package.intents[0]
    assert intent.decision == PackageDecision.OBSERVE
    assert intent.evidence_chain is None
    assert "LLM auxiliary text cannot be used as evidence" in intent.downgrade_reasons


def test_mixed_llm_evidence_is_stripped_without_downgrading_authoritative_chain():
    candidate = complete_candidate()
    mixed = CandidateSignal(
        symbol=candidate.symbol,
        side=candidate.side,
        thesis=candidate.thesis,
        evidence_chain=EvidenceChain(
            sources=(
                *candidate.evidence_chain.sources,
                EvidenceSource(
                    kind="llm",
                    source_id="llm-note",
                    description="Model-written note",
                ),
            )
        ),
        backtest=candidate.backtest,
        position_cap=candidate.position_cap,
        failure_condition=candidate.failure_condition,
    )
    package = build_package(candidates=(mixed,))
    intent = package.intents[0]

    assert intent.decision == PackageDecision.TRADE
    assert intent.evidence_chain is not None
    assert [source.kind.value for source in intent.evidence_chain.sources] == [
        "dataset",
        "backtest",
    ]
    assert "LLM evidence was ignored" in intent.auxiliary_notes


def test_trade_intent_requires_backtest_drawdown_failure_and_position_cap():
    package = build_package()
    intent = package.intents[0]

    assert intent.decision == PackageDecision.TRADE
    assert intent.backtest is not None
    assert intent.backtest.window
    assert intent.backtest.max_drawdown is not None
    assert intent.failure_condition
    assert intent.position_cap is not None


def test_incomplete_evidence_downgrades_candidate_to_observe():
    candidate = complete_candidate()
    incomplete = CandidateSignal(
        symbol=candidate.symbol,
        side=candidate.side,
        thesis=candidate.thesis,
        evidence_chain=candidate.evidence_chain,
        backtest=candidate.backtest,
        position_cap=candidate.position_cap,
        failure_condition="",
    )
    package = build_package(candidates=(incomplete,))

    assert package.decision == PackageDecision.OBSERVE
    assert package.intents[0].decision == PackageDecision.OBSERVE
    assert "missing failure condition" in package.intents[0].downgrade_reasons


def test_do_not_trade_section_always_renders():
    markdown = render_markdown(build_package())

    assert "## Why We Do Not Trade Today" in markdown
    assert "risk policy allows no forced trades" in markdown


def test_intent_table_fields_are_stable():
    row = build_package().intents[0].to_table_row()

    assert list(row.keys()) == INTENT_FIELDS
    assert row["manual_action"] == "pending"
    assert row["decision"] == "trade"


def test_audit_json_contains_traceability_fields():
    package = build_package()
    audit = package.to_audit_payload()

    assert audit["dataset_version"] == "2026-05-16"
    assert audit["code_commit"] == "abc1234"
    assert audit["manifest_hash"]
    assert audit["audit"]["backtests"][0]["parameters"] == {"lookback": 20}
    assert audit["audit"]["risk_metrics"] == {"portfolio_var": 0.0}


def test_easyxt_export_reuses_existing_trade_intent_contract_after_manual_confirm():
    package = build_package()

    assert to_easyxt_bridge_payloads(
        package,
        account="sim",
        qty_by_symbol={"600000.SH": 100},
    ) == []

    confirmed = replace(
        package,
        intents=(replace(package.intents[0], manual_action=ManualAction.CONFIRM),),
    )
    payloads = to_easyxt_bridge_payloads(
        confirmed,
        account="sim",
        qty_by_symbol={"600000.SH": 100},
    )

    assert payloads == [
        {
            "request_id": "morning_package-2026-05-17-600000.SH",
            "strategy": "morning_package",
            "account": "sim",
            "symbol": "600000.SH",
            "side": "buy",
            "qty": 100,
            "dry_run": True,
            "note": "PIT-safe momentum signal with bounded drawdown.",
        }
    ]


def test_research_output_converts_to_morning_package_candidate_without_execution_imports():
    from app.research.agent import ResearchAgentRequest, ResearchAgentRunner
    from app.research.backtest import LedgerBacktestResult
    from app.research.manifest import DatasetVersion, sha256_json
    from app.research.models import Frequency, Market
    import polars as pl
    from datetime import date, timedelta

    class FakePITStore:
        def query(self, request):
            days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
            closes = [10.0 + i * 0.1 for i in range(30)]
            return pl.DataFrame(
                {
                    "date": days,
                    "open": closes,
                    "high": [value + 0.05 for value in closes],
                    "low": [value - 0.05 for value in closes],
                    "close": closes,
                    "volume": [1_000_000 + i * 1000 for i in range(30)],
                    "amount": [10_000_000 + i * 10000 for i in range(30)],
                }
            )

    dataset_version = DatasetVersion(
        name="ashare.kline_daily_pit",
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        snapshot_id="ds-ashare-daily-20240201",
        schema_hash="a" * 64,
        data_hash="b" * 64,
        as_of=datetime(2024, 2, 1, tzinfo=UTC),
        version="2024-02-01",
    )
    runner = ResearchAgentRunner()
    output = runner.run(
        ResearchAgentRequest(
            name="ashare_momentum_research",
            symbols=("600000.SH",),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 31, tzinfo=UTC),
            as_of=datetime(2024, 2, 1, tzinfo=UTC),
            dataset_version=dataset_version,
            entry_threshold=0.01,
        ),
        FakePITStore(),
    )
    manifest = runner.promote_to_manifest(
        output,
        code_commit="abc1234",
        data_versions=(dataset_version,),
        config={"symbols": ("600000.SH",)},
        random_seed=42,
    )
    ledger_backtest = LedgerBacktestResult(
        symbol="600000.SH",
        window="2024-01-01/2024-01-31",
        initial_capital=1_000_000.0,
        final_equity=1_010_000.0,
        total_return=0.01,
        max_drawdown=0.02,
        parameters={"engine": "ledger"},
    )
    candidates = candidates_from_research_output(
        output,
        dataset_version=dataset_version,
        manifest_hash=manifest.manifest_hash(),
        position_caps={"600000.SH": 0.05},
        failure_conditions={"600000.SH": "Momentum falls below zero."},
        ledger_backtests={"600000.SH": ledger_backtest},
    )
    package = build_morning_package(
        package_date=date(2026, 5, 17),
        candidates=candidates,
        manifest=manifest,
        manifest_hash=manifest.manifest_hash(),
    )

    assert package.decision == PackageDecision.TRADE
    assert package.intents[0].backtest is not None
    assert package.intents[0].backtest.window == "2024-01-01/2024-01-31"
    assert package.intents[0].position_cap == 0.05
    assert package.intents[0].failure_condition == "Momentum falls below zero."
    assert package.audit["manifest_hash"] == manifest.manifest_hash()
    assert sha256_json({"symbols": ("600000.SH",)})


def test_morning_package_research_layer_has_no_trade_imports():
    package_dir = Path(__file__).resolve().parents[2] / "app" / "research" / "morning_package"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "app.trading" not in source
    assert "app.trade" not in source
    assert "quant_terminal.trade" not in source
