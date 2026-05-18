# XAR-430 — OKX Auth Endpoint Smoke (read-only)

Date: 2026-05-18
Status: PASS (read-only, write paths logic-level only)
Predecessor: XAR-423 spike, XAR-426 rollout

## Result

`scripts/xar430-auth-smoke.py` against live OKX account (`KATANA_OKX_LIVE_*`),
`simulated=False`:

```
[PASS] adapter.has_credentials
[PASS] check_auth.code: 0
[PASS] check_auth.data_is_list: len=1
[PASS] get_balance.is_list: len=1
[PASS] get_balance.has_rows: len=1
[PASS] get_positions.is_list: len=2
[PASS] get_orders.is_list: len=0
[PASS] get_order_history.is_list: len=0

SUMMARY: 8/8 read-only checks passed
```

## What this verifies

- Credential loading from env vars (`KATANA_OKX_LIVE_*` mapped to `OKX_API_KEY` /
  `OKX_SECRET_KEY` / `OKX_PASSPHRASE`)
- `OKXCCXTAdapter` instantiation + ccxt + python-okx initialization
- HMAC signing, timestamp, passphrase headers all reach OKX correctly
- `check_auth` → `/api/v5/account/balance` round-trips cleanly
- `get_balance`, `get_positions` return real account state
- `get_orders` / `get_order_history` return list shape (empty currently)
- The adapter's exception-swallowing pattern does NOT mask auth failures: read
  paths returning empty lists from `check_auth.code=0` confirms genuine empty
  state, not silent error

## What this does NOT verify

Write paths (`place_order`, `cancel_order`) intentionally not executed against
live. Logic-level coverage exists in `backend/tests/unit/test_okx_ccxt_adapter.py`:

- Gate-token rejection (wrong token returns `katana_gate_required` BEFORE
  exchange call) — verified
- Missing-credentials path → returns `{"error": ..., "code": "-1"}` — verified

The remaining live-write paths (server-side rejection on bad params, ordId
extraction, sCode==0 success path) remain at logic level only. Recommend a
write-smoke against actual **Demo Trading** keys when those are generated.

## Adapter fix shipped during this ticket

Commit `c6cc236`: replaced `config["headers"] = {"x-simulated-trading": "1"}`
with `ex.set_sandbox_mode(True)`. The header-only approach was unreliable
through ccxt's per-request signing path. `set_sandbox_mode(True)` is the
canonical ccxt OKX sandbox toggle.

Even though XAR-430 ended up running with `simulated=False`, the fix is still
correct for the eventual demo smoke and for any caller that constructs the
adapter with `simulated=True`.

## Probe finding (security note)

Keys originally added to vault as `KATANA_OKX_SANDBOX_*` were probe-confirmed
as **live trading** keys, not demo:

- `flag=0` (live) → `code=0`, real balance returned
- `flag=1` (demo) → `code=50101` "APIKey does not match current environment"

Renamed to `KATANA_OKX_LIVE_*` in vault (comrade-cortex-secrets `cb9dc16`).
Future write smokes must target real demo keys (separate OKX namespace,
generated via Demo Trading tab → API).

## Followups (not done here)

- Generate real OKX demo keys → vault `KATANA_OKX_DEMO_*` → rerun write smoke
  (`place_order` correct/wrong gate, `cancel_order`)
- After demo write smoke passes: legacy `okx_client.py` delete sweep
  (1-week parallel-run window from XAR-426, target 2026-05-25)
