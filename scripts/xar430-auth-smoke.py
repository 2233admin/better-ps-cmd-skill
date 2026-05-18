"""XAR-430 read-only auth smoke against OKX live account.

Verifies the authenticated read paths on OKXCCXTAdapter without placing or
cancelling any orders. Uses KATANA_OKX_LIVE_* from the vault.

Run:
    # PowerShell:
    Get-Content ~/.secrets/api-keys.env | Where-Object { $_ -match '^export KATANA_OKX_LIVE_' } |
        ForEach-Object { if ($_ -match '^export ([A-Z_]+)="([^"]+)"') {
            Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] } }
    cd D:/projects/k-atana/backend
    uv run python ../scripts/xar430-auth-smoke.py
"""

from __future__ import annotations

import json
import os
import sys


REQUIRED = {
    "KATANA_OKX_LIVE_KEY": "OKX_API_KEY",
    "KATANA_OKX_LIVE_SECRET": "OKX_SECRET_KEY",
    "KATANA_OKX_LIVE_PASSPHRASE": "OKX_PASSPHRASE",
}


def _map_env() -> None:
    for src, dst in REQUIRED.items():
        v = os.environ.get(src, "")
        if not v:
            print(f"FAIL: {src} not set in env")
            sys.exit(1)
        os.environ[dst] = v


_map_env()
sys.path.insert(0, r"D:\projects\k-atana\backend")
from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter  # noqa: E402

# simulated=False => live endpoint. We are deliberately running against the
# real account; XAR-430 confirmed the keys are live-namespace, not demo.
cli = OKXCCXTAdapter(simulated=False)
results: dict = {"mode": "live_read_only", "checks": []}


def _check(name: str, ok: bool, detail: object = "") -> None:
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {name}: {detail}")
    results["checks"].append({"name": name, "ok": ok, "detail": str(detail)[:300]})


_check("adapter.has_credentials", cli.has_credentials(), "")

auth = cli.check_auth()
_check("check_auth.code", auth.get("code") == "0", auth.get("code"))
_check("check_auth.data_is_list", isinstance(auth.get("data"), list),
       f"len={len(auth.get('data', []))}")

bal = cli.get_balance()
_check("get_balance.is_list", isinstance(bal, list), f"len={len(bal)}")
# A live account that recently authenticated should have at least one ccy row;
# if it's empty, the adapter swallowed an error.
_check("get_balance.has_rows", len(bal) > 0 if isinstance(bal, list) else False,
       f"len={len(bal) if isinstance(bal, list) else '?'}")

pos = cli.get_positions()
_check("get_positions.is_list", isinstance(pos, list), f"len={len(pos)}")

orders = cli.get_orders(inst_type="SPOT")
_check("get_orders.is_list", isinstance(orders, list), f"len={len(orders)}")

history = cli.get_order_history(inst_type="SPOT", limit=5)
_check("get_order_history.is_list", isinstance(history, list), f"len={len(history)}")

n_pass = sum(1 for c in results["checks"] if c["ok"])
n_total = len(results["checks"])
print(f"\nSUMMARY: {n_pass}/{n_total} read-only checks passed")
print(json.dumps({"summary": f"{n_pass}/{n_total}", "checks": [c["name"] for c in results["checks"] if not c["ok"]]}, indent=2))
sys.exit(0 if n_pass == n_total else 1)
