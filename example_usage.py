"""
量化数据使用示例
演示如何查询和分析数据
"""
from quant_api import QuantDataAPI
import pandas as pd

# 初始化API
api = QuantDataAPI()

print("=" * 80)
print("量化数据使用示例")
print("=" * 80)

# 示例1: 查询股票日线数据
print("\n【示例1: 查询上证指数最近10天数据】")
df = api.get_stock_daily('sh000001', limit=10)
print(df[['date', 'close', 'volume']].to_string(index=False))

# 示例2: 获取最新价格
print("\n【示例2: 获取平安银行最新价格】")
price = api.get_latest_price('sz000001')
if price:
    print(f"  日期: {price['date']}")
    print(f"  收盘价: {price['close']:.2f}")
    print(f"  成交量: {price['volume']:,}")

# 示例3: 涨幅榜
print("\n【示例3: 今日涨幅前5名】")
gainers = api.get_top_gainers(limit=5)
print(gainers[['symbol', 'close', 'change_pct']].to_string(index=False))

# 示例4: 宏观数据
print("\n【示例4: 最近CPI数据】")
cpi = api.get_cpi(limit=5)
print(cpi[['date', 'value']].to_string(index=False))

# 示例5: 计算技术指标
print("\n【示例5: 计算移动平均线】")
df = api.get_stock_daily('sh600000', limit=30)
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
print(df[['date', 'close', 'ma5', 'ma10']].tail(5).to_string(index=False))

print("\n" + "=" * 80)
print("完成！更多用法请参考 DATA_GUIDE.md")
print("=" * 80)
