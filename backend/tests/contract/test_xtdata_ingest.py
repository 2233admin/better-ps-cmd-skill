from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_xtdata_script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "ingest-ashare-xtdata.py"
    spec = importlib.util.spec_from_file_location("ingest_ashare_xtdata", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_xtdata_normalizer_converts_mocked_local_daily_data_to_pit_rows():
    module = _load_xtdata_script()
    local_data = {
        "600000.SH": [
            {
                "time": 1775174400000,
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": 1010.0,
            }
        ],
        "sz000001": [
            {
                "time": "20260403",
                "open": 12.0,
                "high": 12.3,
                "low": 11.8,
                "close": 12.2,
                "volume": 120,
                "amount": 1464.0,
            }
        ],
    }

    frame = module.normalize_xtdata_local_data(local_data, period="1d", adjust="none")

    assert frame["symbol"].to_list() == ["000001.SZ", "600000.SH"]
    assert frame["market"].to_list() == ["SZ", "SH"]
    assert frame["event_time"].dt.strftime("%Y-%m-%d").to_list() == ["2026-04-03", "2026-04-03"]
    assert all(value.hour == 9 and value.minute == 30 for value in frame["available_at"].to_list())
    assert set(frame.columns) == {
        "symbol",
        "market",
        "event_time",
        "available_at",
        "source_updated_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }


def test_xtdata_promotes_only_daily_period():
    module = _load_xtdata_script()

    try:
        module.normalize_xtdata_local_data({}, period="1m", adjust="none")
    except ValueError as exc:
        assert "period='1d'" in str(exc)
    else:
        raise AssertionError("minute data must not enter daily PIT ingest")
