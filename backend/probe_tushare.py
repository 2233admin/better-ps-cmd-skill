"""Tushare 全API探测脚本"""
import requests
import sys

TOKEN = "c8c7d9ef93bdcf19fd48104716bec17f84443558faf197b54ba624a8"
BASE_URL = "http://tsy.xiaodefa.cn"

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
    ("hsgt_top10", {"trade_date": "20240102", "market": "SH"}),
    ("stock_basic", {"ts_code": "000001.SZ"}),
    ("index_daily", {"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20240110"}),
    ("bak_daily", {"trade_date": "20240102"}),
    ("hsgt_hold", {}),
    ("fund_etf", {}),
    ("stk_manager", {}),
    ("research_list", {}),
    ("sw_index_daily", {}),
    ("stock_pledge_stat", {}),
    ("money_supply", {}),
    ("m2", {}),
    ("stock_daily", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"}),
    ("stock_tick", {"trade_date": "20240102", "ts_code": "000001.SZ"}),
    ("stock_orderbook", {"ts_code": "000001.SZ"}),
    ("futures_basic", {}),
    ("futures_daily", {}),
    ("options_basic", {}),
    ("bond_basic", {}),
    ("cb_basic", {}),
    ("dj_index_daily", {}),
    ("hsi_daily", {}),
    ("index_weight", {"index_code": "000001.SH"}),
    ("concept_detail", {}),
    ("report", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"}),
    ("forecast", {"ts_code": "000001.SZ"}),
    ("express", {"ts_code": "000001.SZ"}),
    ("writable", {}),
    ("stk_rewards", {}),
    ("stk_limit_list", {"trade_date": "20240102"}),
    ("margin_detail", {}),
    ("rzrq_detail", {}),
    ("rzrq", {}),
]

print(f"{'API':<25} {'状态':<8} {'行数':<8} {'首行字段'}")
print("-" * 70)

for api, params in APIS:
    try:
        r = requests.post(BASE_URL, json={
            "api_name": api,
            "token": TOKEN,
            "params": params,
            "fields": ""
        }, timeout=(10, 30))
        j = r.json()
        c = j.get("code")
        if c == 0:
            items = j.get("data", {}).get("items", [])
            fields = j.get("data", {}).get("fields", [])[:4]
            print(f"OK  {api:<25} {len(items):>8}行  {fields}")
        elif c == 40101:
            print(f"NO  {api:<25}  无权限")
        elif c == -1:
            print(f"SYS {api:<25}  系统错误")
        else:
            msg = j.get("msg", "")[:40]
            print(f"ERR {api:<25}  code={c}  {msg}")
    except Exception as e:
        print(f"EX  {api:<25}  {str(e)[:50]}")
