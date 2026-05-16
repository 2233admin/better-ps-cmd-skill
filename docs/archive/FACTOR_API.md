# 量化因子回测数据接口文档

## 数据库位置
```
C:\Users\Administrator\quant-terminal\data\quant.duckdb
```

## 连接方式

### Python
```python
import duckdb

conn = duckdb.connect("C:/Users/Administrator/quant-terminal/data/quant.duckdb")
result = conn.execute("SELECT * FROM stock_daily_raw LIMIT 10").fetchall()
conn.close()
```

### SQL 直接查询
```bash
duckdb C:\Users\Administrator\quant-terminal\data\quant.duckdb
```

---

## 核心数据表

### 1. 日频个股数据 - `stock_daily_raw`

**用途**：计算动量、反转、波动率等日频因子

**字段**：
- `Stkcd` (VARCHAR): 股票代码
- `Trddt` (DATE): 交易日期
- `Opnprc` (DOUBLE): 开盘价
- `Hiprc` (DOUBLE): 最高价
- `Loprc` (DOUBLE): 最低价
- `Clsprc` (DOUBLE): 收盘价
- `Dnshrtrd` (BIGINT): 成交量
- `Dnvaltrd` (DOUBLE): 成交额
- `Dretwd` (DOUBLE): 考虑现金红利再投资的日个股回报率
- `Dretnd` (DOUBLE): 不考虑现金红利再投资的日个股回报率

**示例查询**：
```sql
-- 计算 20 日动量因子
SELECT
    Stkcd as symbol,
    Trddt as date,
    Clsprc as close,
    Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01'
ORDER BY Stkcd, Trddt;
```

---

### 2. 周频个股数据 - `stock_weekly_raw`

**用途**：计算周频因子，降低噪音

**字段**：
- `Stkcd` (VARCHAR): 股票代码
- `Trdwnt` (DATE): 周开始日期
- `Wretwd` (DOUBLE): 周收益率（考虑红利）
- `Wretnd` (DOUBLE): 周收益率（不考虑红利）

**示例查询**：
```sql
-- 计算 4 周动量
SELECT
    Stkcd,
    Trdwnt,
    Wretwd / LAG(Wretwd, 4) OVER (PARTITION BY Stkcd ORDER BY Trdwnt) - 1 as momentum_4w
FROM stock_weekly_raw
WHERE Trdwnt >= '2024-01-01';
```

---

### 3. 月频个股数据 - `stock_monthly_raw`

**用途**：计算月频因子，适合长周期策略

**字段**：
- `Stkcd` (VARCHAR): 股票代码
- `Trdmnt` (DATE): 月份
- `Mretwd` (DOUBLE): 月收益率（考虑红利）
- `Mretnd` (DOUBLE): 月收益率（不考虑红利）

---

### 4. 财务报表 - 利润表

**表名**：`financial_income_raw_2` (主表，一般企业)

**用途**：计算 PE、ROE、盈利增长等财务因子

**关键字段**：
- `Stkcd` (VARCHAR): 股票代码
- `Accper` (DATE): 报告期
- `Typrep` (VARCHAR): 报表类型 (A=年报, B=中报, C=一季报, D=三季报)
- `F050101B` (DOUBLE): 营业收入
- `F050201B` (DOUBLE): 营业成本
- `F050301B` (DOUBLE): 营业利润
- `F051001B` (DOUBLE): 净利润
- `F052101B` (DOUBLE): 基本每股收益 (EPS)

**示例查询**：
```sql
-- 计算 ROE (净利润 / 股东权益)
SELECT
    i.Stkcd,
    i.Accper,
    i.F051001B as net_profit,
    b.F051801B as equity,
    i.F051001B / NULLIF(b.F051801B, 0) as roe
FROM financial_income_raw_2 i
JOIN financial_balance_raw_3 b
    ON i.Stkcd = b.Stkcd
    AND i.Accper = b.Accper
WHERE i.Accper >= '2020-01-01'
  AND i.Typrep = 'A'  -- 只看年报
ORDER BY i.Stkcd, i.Accper;
```

---

### 5. 财务报表 - 资产负债表

**表名**：`financial_balance_raw_3` (主表)

**关键字段**：
- `Stkcd`, `Accper`, `Typrep` (同上)
- `F050101A` (DOUBLE): 总资产
- `F050201A` (DOUBLE): 总负债
- `F051801B` (DOUBLE): 股东权益
- `F050401A` (DOUBLE): 流动资产
- `F050501A` (DOUBLE): 流动负债

**示例查询**：
```sql
-- 计算资产负债率
SELECT
    Stkcd,
    Accper,
    F050201A / NULLIF(F050101A, 0) as debt_ratio,
    F050401A / NULLIF(F050501A, 0) as current_ratio
FROM financial_balance_raw_3
WHERE Accper >= '2020-01-01'
  AND Typrep = 'A';
```

---

### 6. 财务报表 - 现金流量表

**表名**：`financial_cashflow_raw_2` (主表)

**关键字段**：
- `Stkcd`, `Accper`, `Typrep` (同上)
- `F060101B` (DOUBLE): 经营活动现金流
- `F060201B` (DOUBLE): 投资活动现金流
- `F060301B` (DOUBLE): 筹资活动现金流

---

### 7. 技术指标 - `technical_indicators_raw`

**用途**：现成的技术指标，无需自己计算

**字段**：
- `Stkcd`, `Trddt` (同日频数据)
- `MA5`, `MA10`, `MA20`, `MA60` (DOUBLE): 移动平均线
- 其他技术指标 (需查看实际字段)

---

### 8. 指数数据 - `index_daily_raw`

**用途**：计算市场收益率，用于因子对冲

**字段**：
- `Indexcd` (VARCHAR): 指数代码
- `Trddt` (DATE): 交易日期
- `Clsindex` (DOUBLE): 收盘指数
- `Retindex` (DOUBLE): 指数收益率

**常用指数**：
- `000001`: 上证综指
- `399001`: 深证成指
- `399006`: 创业板指

---

### 9. 宏观经济指标 - `macro_indicators`

**用途**：分析因子在不同宏观环境下的表现

**字段**：
- `category` (VARCHAR): 类别 (CPI-上年同月, 工业出厂价格, 农产品价格)
- `indicator` (VARCHAR): 指标名称
- `date` (DATE): 月份 (每月1日)
- `value` (DOUBLE): 指标值

**示例查询**：
```sql
-- 获取 CPI 数据
SELECT date, value
FROM macro_indicators
WHERE category = 'CPI-上年同月'
  AND indicator = '居民消费价格指数 (上年同月=100)'
  AND date >= '2020-01-01'
ORDER BY date;
```

---

## 因子回测完整流程

### Step 1: 计算因子值

```sql
-- 示例：计算动量因子和反转因子
CREATE TABLE factor_momentum AS
SELECT
    Stkcd as symbol,
    Trddt as date,
    Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum_20d,
    LAG(Clsprc, 5) OVER (PARTITION BY Stkcd ORDER BY Trddt) / Clsprc - 1 as reversal_5d
FROM stock_daily_raw
WHERE Trddt >= '2020-01-01';
```

### Step 2: 因子分组

```sql
-- 按因子值分成 5 组
CREATE TABLE factor_groups AS
SELECT
    symbol,
    date,
    momentum_20d,
    NTILE(5) OVER (PARTITION BY date ORDER BY momentum_20d) as momentum_group
FROM factor_momentum
WHERE momentum_20d IS NOT NULL;
```

### Step 3: 计算组合收益

```sql
-- 计算每组的平均收益
SELECT
    g.date,
    g.momentum_group,
    AVG(s.Dretwd) as avg_return
FROM factor_groups g
JOIN stock_daily_raw s
    ON g.symbol = s.Stkcd
    AND g.date = s.Trddt
GROUP BY g.date, g.momentum_group
ORDER BY g.date, g.momentum_group;
```

### Step 4: 多空对冲

```sql
-- 做多高动量组，做空低动量组
WITH group_returns AS (
    SELECT
        g.date,
        g.momentum_group,
        AVG(s.Dretwd) as avg_return
    FROM factor_groups g
    JOIN stock_daily_raw s
        ON g.symbol = s.Stkcd
        AND g.date = s.Trddt
    GROUP BY g.date, g.momentum_group
)
SELECT
    date,
    MAX(CASE WHEN momentum_group = 5 THEN avg_return END) -
    MAX(CASE WHEN momentum_group = 1 THEN avg_return END) as long_short_return
FROM group_returns
GROUP BY date
ORDER BY date;
```

---

## 因子对抗测试示例

### 动量 vs 反转

```sql
-- 计算两个因子的相关性
WITH factors AS (
    SELECT
        Stkcd,
        Trddt,
        Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum,
        LAG(Clsprc, 5) OVER (PARTITION BY Stkcd ORDER BY Trddt) / Clsprc - 1 as reversal
    FROM stock_daily_raw
    WHERE Trddt >= '2024-01-01'
)
SELECT
    DATE_TRUNC('month', Trddt) as month,
    CORR(momentum, reversal) as correlation
FROM factors
WHERE momentum IS NOT NULL AND reversal IS NOT NULL
GROUP BY month
ORDER BY month;
```

### 价值 vs 成长

```sql
-- 结合财务数据
WITH value_growth AS (
    SELECT
        s.Stkcd,
        s.Trddt,
        s.Clsprc,
        i.F051001B / NULLIF(s.Clsprc, 0) as ep_ratio,  -- 价值因子 (E/P)
        i.F051001B / NULLIF(LAG(i.F051001B) OVER (PARTITION BY i.Stkcd ORDER BY i.Accper), 0) - 1 as profit_growth  -- 成长因子
    FROM stock_daily_raw s
    JOIN financial_income_raw_2 i
        ON s.Stkcd = i.Stkcd
        AND i.Accper = (
            SELECT MAX(Accper)
            FROM financial_income_raw_2
            WHERE Stkcd = s.Stkcd AND Accper <= s.Trddt
        )
    WHERE s.Trddt >= '2024-01-01'
)
SELECT
    DATE_TRUNC('month', Trddt) as month,
    CORR(ep_ratio, profit_growth) as value_growth_corr
FROM value_growth
WHERE ep_ratio IS NOT NULL AND profit_growth IS NOT NULL
GROUP BY month;
```

---

## 性能优化建议

### 1. 创建索引
```sql
CREATE INDEX idx_stock_daily_date ON stock_daily_raw(Trddt);
CREATE INDEX idx_stock_daily_symbol ON stock_daily_raw(Stkcd);
CREATE INDEX idx_financial_date ON financial_income_raw_2(Accper);
```

### 2. 使用临时表
```sql
-- 先筛选日期范围，减少计算量
CREATE TEMP TABLE recent_data AS
SELECT * FROM stock_daily_raw
WHERE Trddt >= '2024-01-01';

-- 然后在临时表上计算因子
SELECT ... FROM recent_data;
```

### 3. 批量计算
```python
# Python 批量处理
import duckdb

conn = duckdb.connect("quant.duckdb")

# 一次性计算多个因子
query = """
SELECT
    Stkcd,
    Trddt,
    Clsprc / LAG(Clsprc, 20) OVER w - 1 as momentum_20d,
    STDDEV(Dretwd) OVER (PARTITION BY Stkcd ORDER BY Trddt ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) as volatility_20d,
    AVG(Dnvaltrd) OVER (PARTITION BY Stkcd ORDER BY Trddt ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) as avg_volume_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01'
WINDOW w AS (PARTITION BY Stkcd ORDER BY Trddt)
"""

df = conn.execute(query).df()
```

---

## 常见问题

### Q1: 如何处理停牌数据？
A: 停牌日没有交易数据，窗口函数会自动跳过。如需填充，使用 `COALESCE` 或 `LAST_VALUE IGNORE NULLS`。

### Q2: 如何对齐财务数据和日频数据？
A: 使用子查询找到最近一期财务报表：
```sql
WHERE Accper = (
    SELECT MAX(Accper)
    FROM financial_income_raw_2
    WHERE Stkcd = s.Stkcd AND Accper <= s.Trddt
)
```

### Q3: 如何处理 ST 股票？
A: 股票代码或名称中包含 "ST" 的需要过滤，具体字段需查看 `stock_info` 表（如果有）。

### Q4: 数据更新频率？
A: 当前数据截止 2026-03，需要定期更新。

---

## 快速开始模板

```python
import duckdb
import pandas as pd

# 连接数据库
conn = duckdb.connect("C:/Users/Administrator/quant-terminal/data/quant.duckdb")

# 1. 计算因子
factor_query = """
SELECT
    Stkcd as symbol,
    Trddt as date,
    Clsprc as close,
    Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01'
  AND Clsprc IS NOT NULL
"""

factors = conn.execute(factor_query).df()

# 2. 因子分组
factors['group'] = factors.groupby('date')['momentum_20d'].transform(
    lambda x: pd.qcut(x, 5, labels=False, duplicates='drop')
)

# 3. 计算收益
returns_query = """
SELECT
    Stkcd as symbol,
    Trddt as date,
    Dretwd as return
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01'
"""

returns = conn.execute(returns_query).df()

# 4. 合并并分析
result = factors.merge(returns, on=['symbol', 'date'])
group_performance = result.groupby(['date', 'group'])['return'].mean()

print(group_performance)

conn.close()
```

---

## 联系方式

数据位置：`C:\Users\Administrator\quant-terminal\data\quant.duckdb`  
文档位置：本文件  
更新日期：2026-04-30
