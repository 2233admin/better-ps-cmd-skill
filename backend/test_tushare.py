"""Tushare 连接测试"""
import requests
token = "c8c7d9ef93bdcf19fd48104716bec17f84443558faf197b54ba624a8"
base_url = "http://tsy.xiaodefa.cn"

print("测试 Tushare 连接...")
try:
    r = requests.post(base_url, json={
        "api_name": "trade_cal",
        "token": token,
        "params": {"exchange": "SSE", "start_date": "20240101", "end_date": "20240101"},
        "fields": ""
    }, timeout=30)
    j = r.json()
    print(f"Token验证: code={j.get('code')} msg={j.get('msg','')[:50]}")
except Exception as e:
    print(f"连接失败: {e}")
