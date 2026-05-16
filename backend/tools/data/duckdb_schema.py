"""DuckDB 全表结构导出"""
import duckdb

conn = duckdb.connect("C:/Users/Administrator/quant-terminal/data/quant.duckdb", read_only=True)

# 所有schema
schemas = conn.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name").fetchall()
print("SCHEMAS:", [s[0] for s in schemas])

# 所有表
tables = conn.execute("""
    SELECT table_schema, table_name, table_type
    FROM information_schema.tables
    WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
    ORDER BY table_schema, table_name
""").fetchall()
print("TABLES_COUNT:", len(tables))

for t in tables:
    schema, name, ttype = t
    cols = conn.execute(f"PRAGMA table_info('{schema}.{name}')").fetchall()
    col_names = [c[1] for c in cols]
    row_count = conn.execute(f"SELECT COUNT(*) FROM {schema}.{name}").fetchone()[0]
    print(f"TABLE:{schema}.{name}|{ttype}|ROWS:{row_count}|COLS:{col_names}")

conn.close()
