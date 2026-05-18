"""Tushare/ChinaData API probe."""

from __future__ import annotations

import pandas as pd

from tushare_client import fetch_tushare_dataframe, resolve_token


APIS = [
    ("limit_list_d", {"trade_date": "20240102", "limit_type": "U"}),
    ("top_list", {"trade_date": "20240102"}),
    ("namechange", {}),
    ("new_share", {}),
    ("suspend", {}),
    ("concept", {}),
    ("index_classify", {}),
    ("stk_rewards", {}),
    ("pledge_stat", {}),
    ("daily_basic", {"trade_date": "20240102"}),
    ("adj_factor", {"trade_date": "20240102"}),
    ("moneyflow", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"}),
    ("share_float", {}),
    ("margin", {}),
    ("moneyflow_hsgt", {"trade_date": "20240102"}),
    ("hsgt_top10", {"trade_date": "20240102", "market_type": "1"}),
    ("stock_basic", {"ts_code": "000001.SZ"}),
    ("index_daily", {"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20240110"}),
    ("bak_daily", {"trade_date": "20240102"}),
    ("hsgt_hold", {}),
    ("fund_basic", {"market": "E"}),
    ("stk_manager", {}),
    ("research_report", {"ts_code": "000001.SZ"}),
    ("sw_daily", {"ts_code": "801010.SI", "start_date": "20240101", "end_date": "20240110"}),
    ("stk_limit", {"trade_date": "20240102"}),
    ("margin_detail", {"trade_date": "20240102"}),
]


def main() -> None:
    token = resolve_token()
    print(f"using token prefix: {token[:8]}")
    print(f"{'API':<25} {'status':<8} {'rows':<8} {'fields'}")
    print("-" * 90)
    for api_name, params in APIS:
        try:
            frame = fetch_tushare_dataframe(api_name, params=params, retries=2)
            _print_ok(api_name, frame)
        except Exception as exc:
            print(f"ERR {api_name:<25} {type(exc).__name__}: {str(exc)[:50]}")


def _print_ok(api_name: str, frame: pd.DataFrame) -> None:
    preview = list(frame.columns[:4]) if not frame.empty else []
    print(f"OK  {api_name:<25} {len(frame):>8} {preview}")


if __name__ == "__main__":
    main()
