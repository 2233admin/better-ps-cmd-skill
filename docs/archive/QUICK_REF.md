# 因子回测快速参考

## 数据库连接
```python
import duckdb
conn = duckdb.connect("C:/Users/Administrator/quant-terminal/data/quant.duckdb")
```

## 核心表速查

| 表名 | 行数 | 用途 | 关键字段 |
|------|------|------|----------|
| `stock_daily_raw` | 1M | 日频价格 | Stkcd, Trddt, Clsprc, Dretwd |
| `stock_weekly_raw` | 1M | 周频收益 | Stkcd, Trdwnt, Wretwd |
| `stock_monthly_raw` | 920K | 月频收益 | Stkcd, Trdmnt, Mretwd |
| `financial_income_raw_2` | 682K | 利润表 | Stkcd, Accper, F051001B (净利润) |
| `financial_balance_raw_3` | 680K | 资产负债表 | Stkcd, Accper, F051801B (权益) |
| `financial_cashflow_raw_2` | 658K | 现金流量表 | Stkcd, Accper, F060101B (经营现金流) |
| `macro_indicators` | 68K | 宏观数据 | category, indicator, date, value |
| `index_daily_raw` | 96K | 指数数据 | Indexcd, Trddt, Retindex |

## 常用因子计算

### 1. 动量因子 (20日)
```sql
SELECT
    Stkcd,
    Trddt,
    Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01';
```

### 2. 波动率因子 (20日)
```sql
SELECT
    Stkcd,
    Trddt,
    STDDEV(Dretwd) OVER (
        PARTITION BY Stkcd
        ORDER BY Trddt
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) as volatility_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01';
```

### 3. 成交量因子 (20日平均)
```sql
SELECT
    Stkcd,
    Trddt,
    AVG(Dnshrtrd) OVER (
        PARTITION BY Stkcd
        ORDER BY Trddt
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) as avg_volume_20d
FROM stock_daily_raw
WHERE Trddt >= '2024-01-01';
```

### 4. ROE 因子
```sql
SELECT
    i.Stkcd,
    i.Accper,
    i.F051001B / NULLIF(b.F051801B, 0) as roe
FROM financial_income_raw_2 i
JOIN financial_balance_raw_3 b
    ON i.Stkcd = b.Stkcd AND i.Accper = b.Accper
WHERE i.Typrep = 'A'  -- 年报
  AND i.Accper >= '2020-01-01';
```

### 5. 市盈率因子 (需要市值数据)
```sql
-- 简化版：用收盘价代替市值
SELECT
    s.Stkcd,
    s.Trddt,
    s.Clsprc / NULLIF(i.F052101B, 0) as pe_ratio  -- 价格 / EPS
FROM stock_daily_raw s
JOIN financial_income_raw_2 i
    ON s.Stkcd = i.Stkcd
    AND i.Accper = (
        SELECT MAX(Accper)
        FROM financial_income_raw_2
        WHERE Stkcd = s.Stkcd AND Accper <= s.Trddt
    )
WHERE s.Trddt >= '2024-01-01';
```

## 回测模板

### 单因子回测
```sql
-- Step 1: 计算因子
CREATE TEMP TABLE factors AS
SELECT
    Stkcd,
    Trddt,
    Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as factor_value,
    LEAD(Dretwd, 1) OVER (PARTITION BY Stkcd ORDER BY Trddt) as next_return
FROM stock_daily_raw
WHERE Trddt >= '2023-01-01';

-- Step 2: 分组
CREATE TEMP TABLE groups AS
SELECT
    Stkcd,
    Trddt,
    factor_value,
    next_return,
    NTILE(5) OVER (PARTITION BY Trddt ORDER BY factor_value) as quintile
FROM factors
WHERE factor_value IS NOT NULL;

-- Step 3: 计算组合收益
SELECT
    Trddt,
    quintile,
    AVG(next_return) as avg_return,
    COUNT(*) as stock_count
FROM groups
WHERE next_return IS NOT NULL
GROUP BY Trddt, quintile
ORDER BY Trddt, quintile;
```

### 多空对冲
```sql
WITH group_returns AS (
    SELECT
        Trddt,
        quintile,
        AVG(next_return) as avg_return
    FROM groups
    WHERE next_return IS NOT NULL
    GROUP BY Trddt, quintile
)
SELECT
    Trddt,
    MAX(CASE WHEN quintile = 5 THEN avg_return END) as long_return,
    MAX(CASE WHEN quintile = 1 THEN avg_return END) as short_return,
    MAX(CASE WHEN quintile = 5 THEN avg_return END) -
    MAX(CASE WHEN quintile = 1 THEN avg_return END) as long_short_return
FROM group_returns
GROUP BY Trddt
ORDER BY Trddt;
```

## 因子对抗测试

### 相关性分析
```sql
WITH factors AS (
    SELECT
        Stkcd,
        Trddt,
        Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as momentum,
        STDDEV(Dretwd) OVER (
            PARTITION BY Stkcd ORDER BY Trddt
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) as volatility
    FROM stock_daily_raw
    WHERE Trddt >= '2024-01-01'
)
SELECT
    DATE_TRUNC('month', Trddt) as month,
    CORR(momentum, volatility) as correlation
FROM factors
WHERE momentum IS NOT NULL AND volatility IS NOT NULL
GROUP BY month
ORDER BY month;
```

### IC (信息系数) 计算
```sql
-- 因子值与未来收益的相关性
WITH factor_return AS (
    SELECT
        Stkcd,
        Trddt,
        Clsprc / LAG(Clsprc, 20) OVER (PARTITION BY Stkcd ORDER BY Trddt) - 1 as factor,
        LEAD(Dretwd, 1) OVER (PARTITION BY Stkcd ORDER BY Trddt) as next_return
    FROM stock_daily_raw
    WHERE Trddt >= '2024-01-01'
)
SELECT
    DATE_TRUNC('month', Trddt) as month,
    CORR(factor, next_return) as ic
FROM factor_return
WHERE factor IS NOT NULL AND next_return IS NOT NULL
GROUP BY month
ORDER BY month;
```

## 宏观环境分层

### 高通胀 vs 低通胀期的因子表现
```sql
WITH cpi_data AS (
    SELECT
        date,
        value as cpi,
        CASE
            WHEN value > 102 THEN 'high_inflation'
            WHEN value < 100 THEN 'deflation'
            ELSE 'normal'
        END as regime
    FROM macro_indicators
    WHERE category = 'CPI-上年同月'
      AND indicator = '居民消费价格指数 (上年同月=100)'
),
factor_returns AS (
    SELECT
        s.Trddt,
        s.Stkcd,
        s.Clsprc / LAG(s.Clsprc, 20) OVER (PARTITION BY s.Stkcd ORDER BY s.Trddt) - 1 as momentum,
        LEAD(s.Dretwd, 1) OVER (PARTITION BY s.Stkcd ORDER BY s.Trddt) as next_return
    FROM stock_daily_raw s
    WHERE s.Trddt >= '2020-01-01'
)
SELECT
    c.regime,
    CORR(f.momentum, f.next_return) as ic,
    COUNT(*) as sample_size
FROM factor_returns f
JOIN cpi_data c
    ON DATE_TRUNC('month', f.Trddt) = c.date
WHERE f.momentum IS NOT NULL AND f.next_return IS NOT NULL
GROUP BY c.regime;
```

## 性能优化提示

1. **使用临时表**：复杂查询分步执行
2. **添加过滤条件**：WHERE Trddt >= '2024-01-01' 减少数据量
3. **避免全表扫描**：在 Stkcd, Trddt 上有隐式索引
4. **批量处理**：一次计算多个因子，避免重复扫描

## 告诉其他 Agent

> 数据库路径：`C:/Users/Administrator/quant-terminal/data/quant.duckdb`
> 
> 包含 6.5M 行股票数据 (2000-2026) 和 68K 行宏观数据
> 
> 核心表：`stock_daily_raw` (日频), `financial_income_raw_2` (财务), `macro_indicators` (宏观)
> 
> 详细文档：`C:/Users/Administrator/quant-terminal/FACTOR_API.md`
