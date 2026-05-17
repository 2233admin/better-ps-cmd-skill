"""导出DuckDB全表结构到Markdown"""
import duckdb
import json

from app.data.paths import resolve_duckdb_path

conn = duckdb.connect(str(resolve_duckdb_path()), read_only=True)

schemas = conn.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema', 'pg_catalog') ORDER BY schema_name").fetchall()

lines = ["# Quant Terminal DuckDB 数据目录\n", f"生成时间: 2026-05-03\n\n"]

total_tables = 0
total_rows = 0

for (schema,) in schemas:
    tables = conn.execute(f"""
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = '{schema}'
        ORDER BY table_name
    """).fetchall()

    if not tables:
        continue

    lines.append(f"## {schema}\n")
    lines.append(f"| 表名 | 类型 | 行数 | 字段 |\n")
    lines.append(f"|------|------|------|------|\n")

    for (tbl, ttype) in tables:
        try:
            cols = conn.execute(f"PRAGMA table_info('{schema}.{tbl}')").fetchall()
            col_names = [c[1] for c in cols]
            row_count = conn.execute(f"SELECT COUNT(*) FROM {schema}.{tbl}").fetchone()[0]
            total_tables += 1
            total_rows += row_count
            lines.append(f"| `{schema}.{tbl}` | {ttype} | {row_count:,} | `{'`, `'.join(col_names)}` |\n")
        except Exception as e:
            lines.append(f"| `{schema}.{tbl}` | {ttype} | ERR | {e} |\n")

    lines.append("\n")

lines.append(f"---\n**汇总**: {total_tables} 张表, {total_rows:,} 行\n")

conn.close()

with open("C:/K-project/knowledge/04-Research/duckdb-catalog.md", "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"Done: {total_tables} tables, {total_rows:,} rows -> duckdb-catalog.md")
