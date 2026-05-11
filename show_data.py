"""快速查看数据库概况"""
import duckdb

DB = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"
conn = duckdb.connect(DB, read_only=True)

print("=" * 80)
print("量化数据库概况")
print("=" * 80)

# 1. 通达信数据
print("\n【通达信日线数据】")
stats = conn.execute("""
    SELECT 
        COUNT(*) as records,
        COUNT(DISTINCT symbol) as stocks,
        MIN(date) as start_date,
        MAX(date) as end_date
    FROM tdx_daily
""").fetchone()
print(f"  记录数: {stats[0]:,}")
print(f"  股票数: {stats[1]:,}")
print(f"  时间范围: {stats[2]} ~ {stats[3]}")

# 市场分布
market = conn.execute("""
    SELECT 
        CASE WHEN symbol LIKE 'sh%' THEN '上海' ELSE '深圳' END as market,
        COUNT(DISTINCT symbol) as stocks
    FROM tdx_daily
    GROUP BY market
""").fetchall()
print(f"  上海: {market[0][1]:,} 只")
print(f"  深圳: {market[1][1]:,} 只")

# 2. 国泰安数据
print("\n【国泰安数据】")
tables = ['stock_daily_raw', 'stock_weekly_raw', 'stock_monthly_raw']
for t in tables:
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {cnt:,} 条")
    except:
        print(f"  {t}: 不存在")

# 3. 宏观数据
print("\n【宏观数据】")
stats = conn.execute("""
    SELECT 
        COUNT(*) as records,
        COUNT(DISTINCT indicator) as indicators,
        MIN(date) as start_date,
        MAX(date) as end_date
    FROM macro_indicators
""").fetchone()
print(f"  记录数: {stats[0]:,}")
print(f"  指标数: {stats[1]}")
print(f"  时间范围: {stats[2]} ~ {stats[3]}")

# 4. 所有表
print("\n【所有表】")
tables = conn.execute("SHOW TABLES").fetchall()
print(f"  总表数: {len(tables)}")
for t in sorted([x[0] for x in tables]):
    print(f"    - {t}")

conn.close()
print("\n" + "=" * 80)
