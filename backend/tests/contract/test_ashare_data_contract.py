"""Contract tests for A-share PIT data specification."""

from pathlib import Path

import pytest


def test_ashare_pit_spec_declares_non_negotiable_boundaries():
    doc = Path(__file__).resolve().parents[3] / "docs" / "ASHARE_DATA_PIT_SPEC.md"
    text = doc.read_text(encoding="utf-8")

    assert "source/raw -> normalized -> point-in-time" in text
    assert "event_time" in text
    assert "available_at" in text
    assert "source_updated_at" in text
    assert "financial statements" in text
    assert "T+1" in text


def test_price_bar_pit_contract_requires_visibility_columns():
    from app.research.ashare_data_contract import ASharePITDataset, validate_pit_columns

    result = validate_pit_columns(
        ASharePITDataset.KLINE_DAILY,
        {
            "symbol",
            "market",
            "event_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        },
    )

    assert not result.passed
    assert result.missing_columns == ("available_at", "source_updated_at")


def test_complete_price_bar_pit_contract_passes():
    from app.research.ashare_data_contract import ASharePITDataset, PRICE_BAR_COLUMNS
    from app.research.ashare_data_contract import validate_pit_columns

    result = validate_pit_columns(ASharePITDataset.KLINE_DAILY, PRICE_BAR_COLUMNS)

    assert result.passed
    assert result.missing_columns == ()


def test_blocked_dataset_rejected_until_available_at_exists():
    from app.research.ashare_data_contract import require_pit_dataset

    with pytest.raises(ValueError, match="available_at"):
        require_pit_dataset("financial_statements")


def test_only_pit_tier_is_research_eligible():
    from app.research.ashare_data_contract import DatasetTier, is_research_eligible

    assert not is_research_eligible(DatasetTier.RAW)
    assert not is_research_eligible(DatasetTier.NORMALIZED)
    assert is_research_eligible(DatasetTier.PIT)
