from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "report-ashare-source-gaps.py"
    spec = importlib.util.spec_from_file_location("report_ashare_source_gaps", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_source_gap_report_lists_tdx_and_chinadata_roles(tmp_path):
    module = _load_module()
    root = tmp_path / "Ashare"
    (root / "pit").mkdir(parents=True)
    (root / "_manifest").mkdir(parents=True)
    (root / "pit" / "kline_daily_pit.parquet").write_bytes(b"stub")
    (root / "_manifest" / "market_cap_daily_pit.json").write_text(
        '{"dataset":"ashare.market_cap_daily_pit"}',
        encoding="utf-8",
    )

    report = module.build_source_gap_report(root)

    assert report["dataset"] == "ashare.source_gap_report_v1"
    by_dataset = {item["dataset"]: item for item in report["items"]}
    assert by_dataset["daily_price_bars"]["tdxcli"]["status"] == "ready"
    assert by_dataset["daily_market_cap_and_float"]["chinadata"]["dataset"] == "ashare.market_cap_daily_pit"
    assert by_dataset["financial_statements_pit"]["action"] == "next_priority_pull"
