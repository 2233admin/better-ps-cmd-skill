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
    assert "DuckDB tables are legacy cache/tooling only" in text


def test_research_architecture_marks_duckdb_pit_as_legacy():
    doc = Path(__file__).resolve().parents[3] / "docs" / "RESEARCH_ARCHITECTURE.md"
    text = doc.read_text(encoding="utf-8")

    assert "legacy DuckDB compatibility facade" in text
    assert "control-plane truth for A-share is the PIT parquet lake" in text


def test_runtime_and_active_scripts_do_not_hardcode_old_duckdb_root():
    root = Path(__file__).resolve().parents[3]
    targets = [
        root / "backend" / "app" / "data" / "store.py",
        root / "backend" / "app" / "data" / "paths.py",
        root / "backend" / "app" / "macro" / "knowledge.py",
        root / "backend" / "tools" / "data" / "import_csv_to_duckdb.py",
        root / "backend" / "tools" / "data" / "duckdb_schema.py",
        root / "backend" / "tools" / "data" / "duckdb_catalog.py",
        root / "backend" / "run_backtest.py",
        root / "scripts" / "ingest-ashare-aquant-tdx.py",
    ]

    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "C:/Users/Administrator/quant-terminal/data" not in text, path


def test_data_paths_resolve_from_env_or_repo_local(monkeypatch):
    from app.data.paths import (
        default_ashare_data_dir,
        default_ashare_lake_root,
        default_data_dir,
        project_root,
        resolve_ashare_data_dir,
        resolve_ashare_duckdb_path,
        resolve_ashare_lake_root,
        resolve_chroma_dir,
        resolve_data_dir,
        resolve_duckdb_path,
    )

    monkeypatch.delenv("KATANA_DATA_DIR", raising=False)
    monkeypatch.delenv("KATANA_DUCKDB_PATH", raising=False)
    monkeypatch.delenv("KATANA_CHROMA_DIR", raising=False)
    monkeypatch.delenv("KATANA_ASHARE_DATA_DIR", raising=False)
    monkeypatch.delenv("KATANA_ASHARE_LAKE_ROOT", raising=False)
    monkeypatch.delenv("KATANA_ASHARE_DUCKDB_PATH", raising=False)

    assert default_data_dir() == project_root() / "DATA"
    assert default_ashare_data_dir() == project_root() / "DATA" / "Ashare"
    assert default_ashare_lake_root() == project_root() / "DATA" / "Ashare"
    assert resolve_data_dir() == default_data_dir().resolve()
    assert resolve_ashare_data_dir() == default_ashare_data_dir().resolve()
    assert resolve_ashare_lake_root() == default_ashare_lake_root().resolve()
    assert resolve_duckdb_path().parent == default_data_dir().resolve()
    assert resolve_chroma_dir().parent == default_data_dir().resolve()
    assert resolve_ashare_duckdb_path().parent == default_ashare_data_dir().resolve()

    monkeypatch.setenv("KATANA_DATA_DIR", str(Path("C:/tmp/katana-data")))
    monkeypatch.setenv("KATANA_DUCKDB_PATH", str(Path("C:/tmp/custom.duckdb")))
    monkeypatch.setenv("KATANA_CHROMA_DIR", str(Path("C:/tmp/chroma-cache")))
    monkeypatch.setenv("KATANA_ASHARE_DATA_DIR", str(Path("C:/tmp/katana-ashare")))
    monkeypatch.setenv("KATANA_ASHARE_LAKE_ROOT", str(Path("C:/tmp/katana-ashare-lake")))
    monkeypatch.setenv("KATANA_ASHARE_DUCKDB_PATH", str(Path("C:/tmp/Aquant.duckdb")))

    assert resolve_data_dir() == Path("C:/tmp/katana-data").resolve()
    assert resolve_duckdb_path() == Path("C:/tmp/custom.duckdb").resolve()
    assert resolve_chroma_dir() == Path("C:/tmp/chroma-cache").resolve()
    assert resolve_ashare_data_dir() == Path("C:/tmp/katana-ashare").resolve()
    assert resolve_ashare_lake_root() == Path("C:/tmp/katana-ashare-lake").resolve()
    assert resolve_ashare_duckdb_path() == Path("C:/tmp/Aquant.duckdb").resolve()


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


def test_market_cap_and_industry_pit_contracts_pass():
    from app.research.ashare_data_contract import (
        ASharePITDataset,
        INDUSTRY_DAILY_COLUMNS,
        MARKET_CAP_DAILY_COLUMNS,
        validate_pit_columns,
    )

    assert validate_pit_columns(ASharePITDataset.MARKET_CAP_DAILY, MARKET_CAP_DAILY_COLUMNS).passed
    assert validate_pit_columns(ASharePITDataset.INDUSTRY_DAILY, INDUSTRY_DAILY_COLUMNS).passed


def test_tradability_status_contract_requires_execution_reality_columns():
    from app.research.ashare_data_contract import ASharePITDataset, validate_pit_columns

    result = validate_pit_columns(
        ASharePITDataset.TRADABILITY_STATUS,
        {"symbol", "event_time", "available_at", "source_updated_at", "is_st"},
    )

    assert not result.passed
    assert {
        "market",
        "is_suspended",
        "limit_up",
        "limit_down",
        "listed_days",
        "is_tradable",
        "reason",
    }.issubset(set(result.missing_columns))


def test_blocked_dataset_rejected_until_available_at_exists():
    from app.research.ashare_data_contract import require_pit_dataset

    with pytest.raises(ValueError, match="available_at"):
        require_pit_dataset("financial_statements")


def test_only_pit_tier_is_research_eligible():
    from app.research.ashare_data_contract import DatasetTier, is_research_eligible

    assert not is_research_eligible(DatasetTier.RAW)
    assert not is_research_eligible(DatasetTier.NORMALIZED)
    assert is_research_eligible(DatasetTier.PIT)
