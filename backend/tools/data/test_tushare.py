"""ChinaData-backed Tushare connectivity smoke."""

from __future__ import annotations

from tushare_client import fetch_tushare_dataframe, resolve_token


def main() -> None:
    token = resolve_token()
    print("测试 Tushare/ChinaData 直连...")
    print(f"token prefix: {token[:8]}")
    try:
        frame = fetch_tushare_dataframe(
            "trade_cal",
            params={"exchange": "SSE", "start_date": "20240101", "end_date": "20240101"},
            retries=2,
            token=token,
        )
        print(f"Token验证: rows={len(frame)} cols={list(frame.columns[:4])}")
    except Exception as exc:
        print(f"连接失败: {type(exc).__name__}: {str(exc)[:120]}")


if __name__ == "__main__":
    main()
