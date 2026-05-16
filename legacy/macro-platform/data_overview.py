"""
量化数据库概况查询工具
快速了解数据库中的所有数据
"""
import duckdb
from pathlib import Path


DB_PATH = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"


def print_section(title):
    """打印分节标题"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def query_table_info(conn, table_name):
    """查询表的基本信息"""
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        columns = conn.execute(f"DESCRIBE {table_name}").fetchall()

        print(f"\n📊 {table_name}")
        print(f"   记录数: {count:,}")
        print(f"   字段: {', '.join([c[0] for c in columns[:10]])}")
        if len(columns) > 10:
            print(f"         ... 还有 {len(columns) - 10} 个字段")

        return count
    except:
        return 0


def main():
    print_section("量化数据库概况")

    conn = duckdb.connect(DB_PATH, read_only=True)

    # 1. 通达信数据
    print_section("1. 通达信日线数据 (tdx_daily)")

    count = conn.execute("SELECT COUNT(*) FROM tdx_daily").fetchone()[0]
    stocks = conn.execute("SELECT COUNT(DISTINCT symbol) FROM tdx_daily").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM tdx_daily").fetchone()

    print(f"\n总记录数: {count:,}")
    print(f"股票数量: {stocks:,}")
    print(f"时间范围: {date_range[0]} ~ {date_range[1]}")

    # 市场分布
    market_dist = conn.execute("""
        SELECT
            CASE
                WHEN symbol LIKE 'sh%' THEN '上海'
                WHEN symbol LIKE 'sz%' THEN '深圳'
                ELSE '其他'
            END as market,
            COUNT(DISTINCT symbol) as stocks,
            COUNT(*) as records
        FROM tdx_daily
        GROUP BY market
    """).fetchall()

    print("\n市场分布:")
    for row in market_dist:
        print(f"  {row[0]}: {row[1]:,} 只股票, {row[2]:,} 条记录")

    # 最新数据样本
    print("\n最新数据样本 (前5只):")
    sample = conn.execute("""
        SELECT symbol, date, open, high, low, close, volume
        FROM tdx_daily
        WHERE date = (SELECT MAX(date) FROM tdx_daily)
        ORDER BY symbol
        LIMIT 5
    """).fetchall()

    for row in sample:
        print(f"  {row[0]}: 开={row[2]:.2f}, 高={row[3]:.2f}, 低={row[4]:.2f}, 收={row[5]:.2f}, 量={row[6]:,}")

    # 2. 国泰安数据
    print_section("2. 国泰安数据 (CSMAR)")

    csmar_tables = [
        'stock_daily_raw',
        'stock_weekly_raw',
        'stock_monthly_raw',
        'financial_balance_raw_1',
        'financial_income_raw_1',
        'financial_cashflow_raw_1',
        'technical_indicators_raw'
    ]

    total_csmar = 0
    for table in csmar_tables:
        count = query_table_info(conn, table)
        total_csmar += count

    print(f"\n国泰安总记录数: {total_csmar:,}")

    # 3. 国研网数据
    print_section("3. 国研网宏观数据 (macro_indicators)")

    count = conn.execute("SELECT COUNT(*) FROM macro_indicators").fetchone()[0]
    indicators = conn.execute("SELECT COUNT(DISTINCT indicator_code) FROM macro_indicators").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM macro_indicators").fetchone()

    print(f"\n总记录数: {count:,}")
    print(f"指标数量: {indicators}")
    print(f"时间范围: {date_range[0]} ~ {date_range[1]}")

    # 主要指标
    print("\n主要宏观指标:")
    top_indicators = conn.execute("""
        SELECT indicator_code, indicator_name, COUNT(*) as cnt
        FROM macro_indicators
        WHERE indicator_code IN ('A01010101', 'A01010201', 'A0101')
        GROUP BY indicator_code, indicator_name
        ORDER BY cnt DESC
    """).fetchall()

    for row in top_indicators:
        print(f"  {row[0]}: {row[1]} ({row[2]} 条)")

    # 4. 数据库总览
    print_section("数据库总览")

    all_tables = conn.execute("SHOW TABLES").fetchall()
    print(f"\n总表数: {len(all_tables)}")

    total_records = conn.execute("""
        SELECT SUM(cnt) FROM (
            SELECT COUNT(*) as cnt FROM tdx_daily
            UNION ALL
            SELECT COUNT(*) FROM stock_daily_raw
            UNION ALL
            SELECT COUNT(*) FROM macro_indicators
        )
    """).fetchone()[0]

    print(f"总记录数: {total_records:,}")

    db_size = Path(DB_PATH).stat().st_size / (1024 * 1024 * 1024)
    print(f"数据库大小: {db_size:.2f} GB")

    # 5. 快速查询示例
    print_section("快速查询示例")

    print("""
# 查询某只股票的历史数据
SELECT * FROM tdx_daily
WHERE symbol = 'sh600000'
ORDER BY date DESC
LIMIT 10;

# 查询最新交易日的所有股票
SELECT * FROM tdx_daily
WHERE date = (SELECT MAX(date) FROM tdx_daily)
ORDER BY symbol;

# 查询CPI数据
SELECT * FROM macro_indicators
WHERE indicator_code = 'A01010101'
ORDER BY date DESC;

# 查询某只股票的财务数据
SELECT * FROM financial_balance_raw_1
WHERE symbol = '600000'
ORDER BY report_date DESC;
""")

    conn.close()

    print("\n" + "=" * 80)
    print("查询完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
