import sys
sys.path.insert(0, r'C:\Program Files\Python313\Lib\site-packages')
from pathlib import Path
from anton.core.datasources.data_vault import LocalDataVault
import os

vault = LocalDataVault()
connections = vault.list_connections()
print("Connections:", connections)

for conn_info in connections:
    print(f"Testing {conn_info['engine']} - {conn_info['name']}...")
    creds = vault.load(conn_info['engine'], conn_info['name'])
    if creds:
        vault.inject_env(conn_info['engine'], conn_info['name'], flat=True)
        db_path = os.environ.get('DS_DATABASE', ':memory:')
        token = os.environ.get('DS_MOTHERDUCK_TOKEN', '')
        print(f"  DB path: {db_path}")
        print(f"  Token: {token[:10] + '...' if token else 'none'}")
        import duckdb
        conn = duckdb.connect(db_path, read_only=True)
        result = conn.execute('SELECT 1').fetchone()
        tables = conn.execute("SELECT COUNT(*) FROM information_schema.tables").fetchone()
        conn.close()
        print(f"  SELECT 1: {result}")
        print(f"  Tables in DB: {tables[0]}")
        vault.clear_ds_env()
    else:
        print("  No credentials found")
