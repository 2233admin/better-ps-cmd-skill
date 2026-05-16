# 量化数据平台使用指南

## 🎉 数据已就绪

### 数据概况
- **通达信日线**: 28,364,012 条记录，11,562 只股票 (1990-2026)
- **国泰安数据**: 2,000,000+ 条记录（日/周/月频 + 财务数据）
- **宏观数据**: 67,866 条记录，489 个指标

### 数据库位置
```
C:\Users\Administrator\quant-terminal\data\quant.duckdb
```

---

## 📊 快速开始

### 1. 查看数据概况
```bash
python -c "
import duckdb
conn = duckdb.connect('C:/Users/Administrator/quant-terminal/data/quant.duckdb', read_only=True)
print('通达信:', conn.execute('SELECT COUNT(*), COUNT(DISTINCT symbol) FROM tdx_daily').fetchone())
print('国泰安:', conn.execute('SELECT COUNT(*) FROM stock_daily_raw').fetchone())
print('宏观:', conn.execute('SELECT COUNT(*) FROM macro_indicators').fetchone())
conn.close()
"
```

### 2. 启动可视化面板
```bash
streamlit run C:\Users\Administrator\quant-terminal\stock_visualizer.py
```

**功能**:
- ✅ K线图
- ✅ MACD、RSI、布林带
- ✅ 一阶/二阶导数分析
- ✅ 自定义日期范围
- ✅ 交互式图表

**访问地址**: http://localhost:8501

---

## 🔍 数据查询示例

### Python 查询
```python
import duckdb
import pandas as pd

# 连接数据库
conn = duckdb.connect('C:/Users/Administrator/quant-terminal/data/quant.duckdb', read_only=True)

# 查询股票数据
df = conn.execute("""
    SELECT date, open, high, low, close, volume
    FROM tdx_daily
    WHERE symbol = 'sh600000'
      AND date >= '2025-01-01'
    ORDER BY date DESC
""").df()

print(df.head())

# 计算技术指标
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()

# 计算MACD
exp1 = df['close'].ewm(span=12, adjust=False).mean()
exp2 = df['close'].ewm(span=26, adjust=False).mean()
df['macd'] = exp1 - exp2
df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()

conn.close()
```

### 常用查询

#### 1. 获取最新交易日所有股票
```python
df = conn.execute("""
    SELECT symbol, close, volume,
           (close - open) / open * 100 as change_pct
    FROM tdx_daily
    WHERE date = (SELECT MAX(date) FROM tdx_daily)
    ORDER BY change_pct DESC
""").df()
```

#### 2. 查询涨幅榜
```python
df = conn.execute("""
    WITH latest AS (
        SELECT MAX(date) as max_date FROM tdx_daily
    )
    SELECT symbol, close,
           (close - open) / open * 100 as change_pct
    FROM tdx_daily
    WHERE date = (SELECT max_date FROM latest)
    ORDER BY change_pct DESC
    LIMIT 10
""").df()
```

#### 3. 查询宏观数据
```python
# 查看所有指标
indicators = conn.execute("""
    SELECT DISTINCT category, indicator
    FROM macro_indicators
    ORDER BY category, indicator
""").df()

# 查询特定指标
df = conn.execute("""
    SELECT date, value
    FROM macro_indicators
    WHERE indicator = 'CPI'
    ORDER BY date DESC
""").df()
```

#### 4. 计算月度收益率
```python
df = conn.execute("""
    SELECT
        DATE_TRUNC('month', date) as month,
        FIRST(close) as open_price,
        LAST(close) as close_price,
        (LAST(close) - FIRST(close)) / FIRST(close) * 100 as return_pct
    FROM tdx_daily
    WHERE symbol = 'sh600000'
    GROUP BY DATE_TRUNC('month', date)
    ORDER BY month DESC
""").df()
```

---

## 📈 技术指标计算

### MACD
```python
def calculate_macd(df, fast=12, slow=26, signal=9):
    exp1 = df['close'].ewm(span=fast, adjust=False).mean()
    exp2 = df['close'].ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram
```

### RSI
```python
def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
```

### 布林带
```python
def calculate_bollinger_bands(df, period=20, std_dev=2):
    sma = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return sma, upper, lower
```

---

## 🤖 给其他 Agent 的说明

### 数据库连接
```python
import duckdb

# 只读连接（推荐）
conn = duckdb.connect(
    'C:/Users/Administrator/quant-terminal/data/quant.duckdb',
    read_only=True
)

# 查询数据
df = conn.execute("SELECT * FROM tdx_daily LIMIT 10").df()

# 关闭连接
conn.close()
```

### 主要数据表

| 表名 | 说明 | 主要字段 |
|------|------|----------|
| `tdx_daily` | 通达信日线 | symbol, date, open, high, low, close, volume, amount |
| `stock_daily_raw` | 国泰安日频 | Stkcd, Trddt, Opnprc, Hiprc, Loprc, Clsprc |
| `macro_indicators` | 宏观指标 | category, indicator, date, value |
| `financial_balance_raw_1` | 资产负债表 | symbol, report_date, ... |
| `financial_income_raw_1` | 利润表 | symbol, report_date, ... |

### 查询优化建议
1. **使用 WHERE 过滤**: 明确指定 symbol 和 date 范围
2. **使用 LIMIT**: 避免一次性加载大量数据
3. **只读连接**: 使用 `read_only=True` 避免锁定
4. **关闭连接**: 查询完成后及时关闭

---

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `stock_visualizer.py` | 可视化面板（Streamlit） |
| `quant_api.py` | 数据查询 API |
| `DATA_GUIDE.md` | 详细使用指南 |
| `FACTOR_API.md` | 因子 API 文档 |
| `data/quant.duckdb` | 数据库文件 |

---

## 🎯 下一步

1. **探索数据**: 运行可视化面板查看不同股票
2. **因子研究**: 使用数据进行因子分析和回测
3. **策略开发**: 基于数据开发量化策略

---

## ⚠️ 注意事项

1. **数据库锁定**: DuckDB 同时只允许一个写入连接
2. **内存管理**: 大规模查询时使用分批处理
3. **日期格式**: 统一使用 'YYYY-MM-DD'
4. **股票代码**: 保留市场前缀（sh/sz）

---

## 📞 获取帮助

- 查看详细文档: `DATA_GUIDE.md`
- API 参考: `FACTOR_API.md`
- 示例代码: 本文档中的代码片段

**数据平台已就绪，开始您的量化研究之旅！** 🚀
