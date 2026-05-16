"""把akshare_fetch目录下所有CSV文件批量入库DuckDB csv_raw schema"""
import os
import re
import glob
import duckdb
import pandas as pd
from pathlib import Path

CSV_DIR = Path("C:/Users/Administrator/quant-terminal/data/akshare_fetch")
DB_PATH = "C:/Users/Administrator/quant-terminal/data/quant.duckdb"

conn = duckdb.connect(DB_PATH, read_only=False)
conn.execute("CREATE SCHEMA IF NOT EXISTS csv_raw")

csv_files = sorted(CSV_DIR.glob("*.csv"))
print(f"Found {len(csv_files)} CSV files")

results = []
for fp in csv_files:
    name = fp.stem  # filename without extension
    # Clean table name: replace invalid chars
    clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Truncate if too long
    clean_name = clean_name[:120]

    size_mb = fp.stat().st_size / 1e6

    try:
        df = pd.read_csv(fp, low_memory=False)
        rows = len(df)
        cols = list(df.columns)
        col_str = ",".join(cols)

        # Create table
        col_defs = []
        for c in cols:
            c_clean = re.sub(r'[^a-zA-Z0-9_]', '_', str(c))[:120]
            col_defs.append(f'"{c_clean}" VARCHAR')

        create_sql = f'CREATE TABLE csv_raw."{clean_name}" ({",".join(col_defs)})'
        conn.execute(f"DROP TABLE IF EXISTS csv_raw.\"{clean_name}\"")
        conn.execute(create_sql)

        # Insert data in chunks
        chunk_size = 50000
        for i in range(0, len(df), chunk_size):
            chunk = df.iloc[i:i+chunk_size]
            # Rename columns for insert
            chunk.columns = [re.sub(r'[^a-zA-Z0-9_]', '_', str(c))[:120] for c in chunk.columns]
            conn.execute(f"INSERT INTO csv_raw.\"{clean_name}\" BY NAME SELECT * FROM chunk")

        actual_rows = conn.execute(f'SELECT COUNT(*) FROM csv_raw."{clean_name}"').fetchone()[0]
        status = "OK" if actual_rows == rows else f"WARN({actual_rows}/{rows})"
        results.append((clean_name, name, size_mb, rows, actual_rows, status, cols[:5]))
        print(f"  [{status}] {clean_name}: {actual_rows:,} rows, {size_mb:.1f}MB")
    except Exception as e:
        results.append((clean_name, name, size_mb, 0, 0, f"ERR: {e}", []))
        print(f"  [ERR] {clean_name}: {e}")

conn.close()

# Summary
ok = [r for r in results if r[5] == "OK"]
err = [r for r in results if r[5] != "OK"]
total_rows = sum(r[4] for r in ok)
print(f"\nDone: {len(ok)} OK, {len(err)} ERR, {total_rows:,} total rows")
if err:
    print("ERRORS:")
    for r in err:
        print(f"  {r[0]}: {r[5]}")
