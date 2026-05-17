"""
Tushare 缺口数据完整采集脚本 v2
采集所有可用接口数据并入库 DuckDB
"""

import os
import time
import datetime
import requests
import duckdb
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.data.paths import resolve_data_dir, resolve_duckdb_path

# ============ 配置 ============
TOKEN = "c8c7d9ef93bdcf19fd48104716bec17f84443558faf197b54ba624a8"
BASE_URL = "http://tsy.xiaodefa.cn"
DATA_DIR = resolve_data_dir()
DB_PATH = resolve_duckdb_path()
CSV_DIR = DATA_DIR / "akshare_fetch"
TODAY = datetime.date.today().strftime("%Y%m%d")
# ==============================

os.makedirs(CSV_DIR, exist_ok=True)

# 股票列表缓存
_STOCK_LIST = None


def get_stock_list():
    global _STOCK_LIST
    if _STOCK_LIST is None:
        r = requests.post(BASE_URL, json={
            "api_name": "stock_basic",
            "token": TOKEN,
            "params": {"ts_code": "", "list_status": "L"},
            "fields": "ts_code"
        }, timeout=60)
        j = r.json()
        if j.get("code") == 0:
            _STOCK_LIST = [row[0] for row in j["data"]["items"]]
        else:
            _STOCK_LIST = []
    return _STOCK_LIST


def gen_trade_dates(start_date, end_date):
    """生成两个日期间的每个工作日"""
    dates = []
    current = datetime.datetime.strptime(start_date, "%Y%m%d")
    end = datetime.datetime.strptime(end_date, "%Y%m%d")
    delta = datetime.timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))
        current += delta
    return dates


# Tushare Pro 接口映射表（优化：按日期批量，不用逐股票）
TUSHARE_APIS = {
    # P0: 涨跌停 + 龙虎榜
    "limit_list_d": {
        "desc": "涨跌停列表",
        "params_fn": lambda: [
            {"trade_date": d, "limit_type": t}
            for d in gen_trade_dates("20201228", TODAY)
            for t in ["U", "D"]
        ],
    },
    "top_list": {
        "desc": "龙虎榜",
        "params_fn": lambda: [
            {"trade_date": d}
            for d in gen_trade_dates("20200101", TODAY)
        ],
    },
    # P1: 按日期批量（支持 start_date/end_date）
    "moneyflow": {
        "desc": "个股资金流",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    "daily_basic": {
        "desc": "每日行情指标(PE/PB/换手率)",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    "adj_factor": {
        "desc": "复权因子",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    # P2: 一次拉全部（不按股票）
    "pledge_stat": {
        "desc": "股权质押统计",
        "params_fn": lambda: [{}],
    },
    "share_float": {
        "desc": "流通股本变动",
        "params_fn": lambda: [{}],
    },
    "margin": {
        "desc": "融资融券汇总",
        "params_fn": lambda: [{}],
    },
    "cb_basic": {
        "desc": "可转债列表",
        "params_fn": lambda: [{}],
    },
    "margin_detail": {
        "desc": "融资融券明细",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    # P3: 按日批量
    "moneyflow_hsgt": {
        "desc": "沪深港通资金流向",
        "params_fn": lambda: [
            {"trade_date": d}
            for d in gen_trade_dates("20200101", TODAY)
        ],
    },
    "hsgt_top10": {
        "desc": "沪深港通前10持仓",
        "params_fn": lambda: [
            {"trade_date": d}
            for d in gen_trade_dates("20200101", TODAY)
        ],
    },
    "bak_daily": {
        "desc": "备用行情(鲍行情等)",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    "index_weight": {
        "desc": "指数成分权重",
        "params_fn": lambda: [
            {"start_date": sd, "end_date": ed}
            for sd, ed in _gen_month_ranges("20200101", TODAY)
        ],
    },
    # P4: 无参数
    "namechange": {"desc": "股票曾用名", "params_fn": lambda: [{}]},
    "new_share": {"desc": "新股IPO", "params_fn": lambda: [{}]},
    "suspend": {"desc": "停复牌", "params_fn": lambda: [{}]},
    "concept": {"desc": "概念板块", "params_fn": lambda: [{}]},
    "index_classify": {"desc": "指数分类", "params_fn": lambda: [{}]},
    "forecast": {"desc": "业绩预告", "params_fn": lambda: [{}]},
    "express": {"desc": "业绩快报", "params_fn": lambda: [{}]},
    # P5: 涨跌停/龙虎榜历史
    "limit_list_d": {
        "desc": "涨跌停列表",
        "params_fn": lambda: [
            {"trade_date": d, "limit_type": t}
            for d in gen_trade_dates("20201228", TODAY)
            for t in ["U", "D"]
        ],
    },
    "top_list": {
        "desc": "龙虎榜",
        "params_fn": lambda: [
            {"trade_date": d}
            for d in gen_trade_dates("20200101", TODAY)
        ],
    },
}


def _gen_month_ranges(start_date, end_date):
    """生成年月区间列表 [(start, end), ...]"""
    ranges = []
    current = datetime.datetime.strptime(start_date, "%Y%m%d")
    end = datetime.datetime.strptime(end_date, "%Y%m%d")
    while current <= end:
        y, m = current.year, current.month
        month_start = datetime.date(y, m, 1)
        # 月末
        if m == 12:
            month_end = datetime.date(y, 12, 31)
        else:
            month_end = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
        if month_end > end.date():
            month_end = end.date()
        ranges.append((month_start.strftime("%Y%m%d"), month_end.strftime("%Y%m%d")))
        # 下月
        if m == 12:
            current = datetime.datetime(y + 1, 1, 1)
        else:
            current = datetime.datetime(y, m + 1, 1)
    return ranges


def fetch_tushare(api_name, params=None, fields="", retries=5):
    """通用 Tushare API 调用"""
    for i in range(retries):
        try:
            r = requests.post(
                BASE_URL,
                json={"api_name": api_name, "token": TOKEN, "params": params or {}, "fields": fields},
                timeout=(10, 60),
            )
            j = r.json()
            if j.get("code") == 0:
                data = j.get("data", {})
                df = pd.DataFrame(data.get("items", []), columns=data.get("fields", []))
                return df
            elif j.get("code") == 40101:
                return pd.DataFrame()  # 无权限
            else:
                return pd.DataFrame()
        except Exception as e:
            if i < retries - 1:
                time.sleep(3)
    return pd.DataFrame()


def save_duckdb(df, table_name):
    """保存到 DuckDB tushare schema"""
    if df.empty:
        print(f"  [SKIP] {table_name}: 无数据")
        return
    conn = duckdb.connect(str(DB_PATH), read_only=False)
    try:
        conn.execute(f"DROP TABLE IF EXISTS tushare.{table_name}")
        conn.execute(f"CREATE TABLE tushare.{table_name} AS SELECT * FROM df")
        print(f"  [DB] tushare.{table_name}: {len(df)} 行")
    finally:
        conn.close()


def save_csv(df, name):
    """追加保存 CSV"""
    if df.empty:
        return
    path = CSV_DIR / f"tushare_{name}.csv"
    if path.exists():
        existing = pd.read_csv(path)
        df = pd.concat([existing, df]).drop_duplicates()
    df.to_csv(path, index=False)


def fetch_api(api_name, params_list, desc, max_workers=3):
    """并发采集单个 API"""
    print(f"\n[{api_name}] {desc} ...")
    all_dfs = []

    def fetch_one(params):
        df = fetch_tushare(api_name, params)
        if not df.empty:
            return df
        return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, p): p for p in params_list}
        for future in as_completed(futures):
            df = future.result()
            if not df.empty:
                all_dfs.append(df)

    if all_dfs:
        result = pd.concat(all_dfs, ignore_index=True)
        # 去重
        if "trade_date" in result.columns and "ts_code" in result.columns:
            result = result.drop_duplicates(subset=["trade_date", "ts_code"])
        elif "ts_code" in result.columns:
            result = result.drop_duplicates(subset=["ts_code"])
        print(f"  [{api_name}] 获取 {len(result)} 行")
        save_duckdb(result, api_name)
        save_csv(result, api_name)
        return result
    else:
        print(f"  [{api_name}] 无数据")
        return pd.DataFrame()


def main():
    print("=" * 60)
    print("Tushare 缺口数据完整采集 v2")
    print(f"DB: {DB_PATH}")
    print("=" * 60)

    # 检查 token
    print("\n[Token 验证]")
    r = requests.post(BASE_URL, json={
        "api_name": "trade_cal",
        "token": TOKEN,
        "params": {"exchange": "SSE", "start_date": "20240101", "end_date": "20240101"},
        "fields": ""
    }, timeout=60)
    j = r.json()
    if j.get("code") == 0:
        print("  Token 验证通过")
    else:
        print(f"  Token 错误: {j.get('msg', '')}")
        return

    # 无参数接口（一次拉完）
    for api_name in ["namechange", "concept", "index_classify", "new_share",
                      "pledge_stat", "share_float", "margin", "cb_basic",
                      "forecast", "express"]:
        if api_name in TUSHARE_APIS:
            desc = TUSHARE_APIS[api_name]["desc"]
            params_list = TUSHARE_APIS[api_name]["params_fn"]()
            fetch_api(api_name, params_list, desc, max_workers=4)

    # 按月批量接口
    for api_name in ["daily_basic", "adj_factor", "moneyflow", "margin_detail",
                      "bak_daily", "index_weight"]:
        if api_name in TUSHARE_APIS:
            desc = TUSHARE_APIS[api_name]["desc"]
            params_list = TUSHARE_APIS[api_name]["params_fn"]()
            fetch_api(api_name, params_list, desc, max_workers=8)

    # 按日批量接口
    for api_name in ["moneyflow_hsgt", "hsgt_top10"]:
        if api_name in TUSHARE_APIS:
            desc = TUSHARE_APIS[api_name]["desc"]
            params_list = TUSHARE_APIS[api_name]["params_fn"]()
            fetch_api(api_name, params_list, desc, max_workers=8)

    # 涨跌停/龙虎榜
    for api_name in ["limit_list_d", "top_list"]:
        if api_name in TUSHARE_APIS:
            desc = TUSHARE_APIS[api_name]["desc"]
            params_list = TUSHARE_APIS[api_name]["params_fn"]()
            fetch_api(api_name, params_list, desc, max_workers=8)

    print("\n" + "=" * 60)
    print("全部采集完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
