# 量化数据库使用指南

## 📍 数据库位置
```
C:\Users\Administrator\quant-terminal\data\quant.duckdb
```

## 📊 数据概览

### 1. 通达信日线数据 (tdx_daily)
- **记录数**: 28,364,012 条
- **股票数**: 11,562 只
- **时间范围**: 1990-12-19 ~ 2026-04-29
- **字段**: symbol, date, open, high, low, close, volume, amount

**市场分布**:
- 上海市场 (sh*): 5,834 只股票
- 深圳市场 (sz*): 5,728 只股票

### 2. 国泰安数据 (CSMAR)
- **stock_daily_raw**: 日频个股数据（含复权因子）
- **stock_weekly_raw**: 周频数据
- **stock_monthly_raw**: 月频数据
- **financial_balance_raw_***: 资产负债表
- **financial_income_raw_***: 利润表
- **financial_cashflow_raw_***: 现金流量表
- **technical_indicators_raw**: 技术指标

### 3. 国研网宏观数据 (macro_indicators)
- **记录数**: 67,866 条
- **指标数**: 489 个
- **时间范围**: 2000-2026
- **主要指标**: CPI, PPI, GDP, M2, 利率等

## 🔍 快速查询示例

### Python 查询
```python
import duckdb

conn = duckdb.connect('C:/Users/Administrator/quant-terminal/data/quant.duckdb', read_only=True)

# 查询某只股票的最近数据
df = conn.execute("""
    SELECT * FROM tdx_daily
    WHERE symbol = 'sh600000'
    ORDER BY date DESC
    LIMIT 100
""").df()

# 查询最新交易日所有股票
df = conn.execute("""
    SELECT * FROM tdx_daily
    WHERE date = (SELECT MAX(date) FROM tdx_daily)
    ORDER BY symbol
""").df()

# 查询CPI数据
df = conn.execute("""
    SELECT * FROM macro_indicators
    WHERE indicator_code = 'A01010101'
    ORDER BY date DESC
""").df()

conn.close()
```

### SQL 查询
```sql
-- 查询涨幅前10的股票（最新交易日）
WITH latest_date AS (
    SELECT MAX(date) as max_date FROM tdx_daily
)
SELECT 
    symbol,
    close,
    (close - open) / open * 100 as change_pct
FROM tdx_daily
WHERE date = (SELECT max_date FROM latest_date)
ORDER BY change_pct DESC
LIMIT 10;

-- 查询某只股票的月度收益率
SELECT 
    DATE_TRUNC('month', date) as month,
    FIRST(close) as open_price,
    LAST(close) as close_price,
    (LAST(close) - FIRST(close)) / FIRST(close) * 100 as monthly_return
FROM tdx_daily
WHERE symbol = 'sh600000'
GROUP BY DATE_TRUNC('month', date)
ORDER BY month DESC;

-- 查询市场平均市盈率（需要财务数据）
SELECT 
    t1.date,
    AVG(t1.close / t2.eps) as avg_pe
FROM stock_daily_raw t1
JOIN financial_income_raw_1 t2 
    ON t1.symbol = t2.symbol
WHERE t2.eps > 0
GROUP BY t1.date
ORDER BY t1.date DESC;
```

## 🛠️ 工具脚本

### 1. 数据概况查询
```bash
python C:\Users\Administrator\quant-terminal\data_overview.py
```
显示所有表的统计信息、记录数、时间范围等。

### 2. 可视化面板
```bash
streamlit run C:\Users\Administrator\quant-terminal\stock_visualizer.py
```
交互式K线图，支持：
- K线图
- MACD、RSI、布林带
- 一阶/二阶导数分析
- 自定义日期范围

### 3. 因子研究面板
```bash
streamlit run C:\Users\Administrator\factor_dashboard.py
```
因子回测和分析工具。

## 📝 常用查询模板

### 技术分析
```python
# 计算移动平均线
SELECT 
    date,
    close,
    AVG(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
    AVG(close) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) as ma60
FROM tdx_daily
WHERE symbol = 'sh600000'
ORDER BY date DESC;

# 计算成交量变化
SELECT 
    date,
    volume,
    LAG(volume, 1) OVER (ORDER BY date) as prev_volume,
    (volume - LAG(volume, 1) OVER (ORDER BY date)) / LAG(volume, 1) OVER (ORDER BY date) * 100 as volume_change_pct
FROM tdx_daily
WHERE symbol = 'sh600000'
ORDER BY date DESC;
```

### 基本面分析
```python
# 查询财务指标趋势
SELECT 
    report_date,
    total_assets,
    total_liabilities,
    total_equity,
    total_liabilities / total_assets as debt_ratio
FROM financial_balance_raw_1
WHERE symbol = '600000'
ORDER BY report_date DESC;

# 查询盈利能力
SELECT 
    report_date,
    revenue,
    net_profit,
    net_profit / revenue as profit_margin
FROM financial_income_raw_1
WHERE symbol = '600000'
ORDER BY report_date DESC;
```

### 宏观分析
```python
# 查询多个宏观指标
SELECT 
    t1.date,
    t1.value as cpi,
    t2.value as ppi,
    t3.value as gdp
FROM 
    (SELECT date, value FROM macro_indicators WHERE indicator_code = 'A01010101') t1
LEFT JOIN 
    (SELECT date, value FROM macro_indicators WHERE indicator_code = 'A01010201') t2 ON t1.date = t2.date
LEFT JOIN 
    (SELECT date, value FROM macro_indicators WHERE indicator_code = 'A0101') t3 ON t1.date = t3.date
ORDER BY t1.date DESC;
```

## 🤖 Agent 使用建议

### 对于其他 Agent
1. **只读访问**: 使用 `read_only=True` 连接数据库
2. **查询优化**: 使用 WHERE 过滤减少数据量
3. **日期范围**: 明确指定日期范围避免全表扫描
4. **索引利用**: symbol 和 date 是主键，查询时优先使用

### 示例代码模板
```python
import duckdb
import pandas as pd

def query_stock_data(symbol, start_date, end_date):
    """查询股票数据的标准模板"""
    conn = duckdb.connect(
        'C:/Users/Administrator/quant-terminal/data/quant.duckdb',
        read_only=True
    )
    
    query = f"""
        SELECT date, open, high, low, close, volume
        FROM tdx_daily
        WHERE symbol = '{symbol}'
          AND date >= '{start_date}'
          AND date <= '{end_date}'
        ORDER BY date
    """
    
    df = conn.execute(query).df()
    conn.close()
    
    return df
```

## 📚 API 文档
详细的 API 文档请参考：
- `C:\Users\Administrator\quant-terminal\FACTOR_API.md`
- `C:\Users\Administrator\quant-terminal\QUICK_REF.md`

## ⚠️ 注意事项
1. **数据库锁定**: DuckDB 同时只允许一个写入连接，多个只读连接
2. **内存使用**: 大规模查询时注意内存限制，使用 LIMIT 或分批查询
3. **日期格式**: 统一使用 'YYYY-MM-DD' 格式
4. **股票代码**: 保留市场前缀（sh/sz）以区分不同市场

## 🔗 相关资源
- DuckDB 文档: https://duckdb.org/docs/
- Plotly 文档: https://plotly.com/python/
- Streamlit 文档: https://docs.streamlit.io/
