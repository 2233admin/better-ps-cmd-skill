# Account Reader Source Matrix (XAR-414)

A-share account state can be read from three sources. This document records
**what each source can produce**, so `MultiSourceAccountReader` can compose
them with explicit per-field fallback rules.

Status legend:
- `Y` — implemented and verified against a real account
- `S` — schema/code scaffolded, awaiting calibration on a real environment
- `?` — not probed yet; needs field-by-field walk
- `N` — confirmed not available from this source

## Sources

| Source  | Transport                | Process boundary    | Probe script |
|---------|--------------------------|---------------------|--------------|
| TDX     | pywinauto on `TdxW.exe`  | local UI scrape     | `scripts/probe-tdx-controls.py` |
| EasyXT  | HTTP bridge (off-host)   | remote service      | (next: HTTP readback probe) |
| QMT     | HTTP bridge (off-host)   | remote service      | (next: HTTP readback probe) |

EasyXT and QMT both ride the same HTTP boundary
(`backend/app/trading/adapters/qmt/bridge.py`). The bridge currently only
exposes the *intent submit* endpoint; the readback endpoints (positions /
trades / entrusts / balance) are the work this matrix is driving.

## Field matrix

| Dataset / field                                | TDX | EasyXT | QMT | Notes |
|------------------------------------------------|-----|--------|-----|-------|
| **PositionRecord**                             |     |        |     |       |
| `symbol`                                       | ?   | ?      | ?   | canonical CODE.MARKET (e.g. `600000.SH`) |
| `quantity` (持仓数量)                          | ?   | ?      | ?   | total |
| `available_quantity` (可用)                    | ?   | ?      | ?   | T+1 locked qty subtracted |
| `avg_cost` (成本)                              | ?   | ?      | ?   | per share |
| `market_value`                                 | ?   | ?      | ?   | qty * market price |
| **EntrustRecord** (today entrusts)             |     |        |     |       |
| `request_id` (委托编号)                        | ?   | ?      | ?   | unique per order |
| `symbol`                                       | ?   | ?      | ?   |       |
| `side` (buy/sell)                              | ?   | ?      | ?   |       |
| `qty` (委托数量)                               | ?   | ?      | ?   |       |
| `price` (委托价)                               | ?   | ?      | ?   |       |
| `status` (已报/部成/全成/已撤)                  | ?   | ?      | ?   |       |
| `timestamp`                                    | ?   | ?      | ?   | TZ-aware UTC |
| **TradeRecord** (today fills)                  |     |        |     |       |
| `request_id` linkage                           | ?   | ?      | ?   | needed for journal join |
| `fill_qty` / `fill_price`                      | ?   | ?      | ?   |       |
| `timestamp`                                    | ?   | ?      | ?   |       |
| **BalanceRecord**                              |     |        |     |       |
| `available_cash` (可用资金)                    | S*  | ?      | ?   | TDX `get_balance` scrapes F4 panel; brittle |
| `frozen_cash`                                  | N?  | ?      | ?   |       |
| `total_assets` (总资产)                        | ?   | ?      | ?   |       |
| **Historical**                                 |     |        |     |       |
| historical trade range (>= 5 day)              | ?   | ?      | ?   | TDX usually only shows today; check |
| historical entrust range                       | ?   | ?      | ?   |       |
| **Account metadata**                           |     |        |     |       |
| `account_id` / broker name                     | ?   | Y      | ?   | EasyXT bridge knows `account_id` |
| `permissions` (融资/期权/北交所开通)             | ?   | ?      | ?   |       |

`*` = scaffolded but un-calibrated for current TdxW build (treat as fragile
until probe replay confirms).

## Reconciliation strategy

- **Primary:** TDX (Curry's rule — do not drop)
- **Secondary cross-check:** EasyXT and QMT (whichever is online)
- **Per-field fallback rule:** if TDX returns the field, use it; otherwise
  fall through to the secondary that has it
- **Mismatch handling:** divergence between TDX and EasyXT/QMT on the same
  `request_id` blocks reconciliation pass — surfaced via
  `app.trading.adapters.qmt.reconciliation` (XAR-412 state machine)

## Calibration workflow

1. With TdxW open + logged in, run:
   ```
   python scripts/probe-tdx-controls.py --panel positions --out probe-positions.json
   python scripts/probe-tdx-controls.py --panel entrusts  --out probe-entrusts.json
   python scripts/probe-tdx-controls.py --panel trades    --out probe-trades.json
   python scripts/probe-tdx-controls.py --panel balance   --out probe-balance.json
   ```
2. Walk each JSON, identify the `class_name` + `control_id` of the list
   control holding rows + the header row (column names).
3. Fill in TDX column of this matrix; replace each `?` with one of:
   - field name in the row dict (e.g. `证券代码 -> col[1]`)
   - `N` if confirmed missing
4. Land the implementation against the captured paths in
   `backend/app/trade/account_reader.py::TDXAccountReader.get_positions()`
   (and siblings). Replace `_uncalibrated()` calls.
5. Repeat for EasyXT / QMT once the HTTP readback endpoints exist on the
   bridge (separate ticket).

## Known unknowns

- TdxW often hides the entrust list behind a tab control; control walk may
  need to send `{TAB}` or click a tab before the rows appear. The probe
  script supports `--wait` to let the UI settle; if rows are still empty
  after probe, the panel hotkey may need adjustment.
- EasyXT/QMT bridges run off-host. Confirming what fields they can serve
  needs a probe call against the actual bridge URL — separate task once
  the HTTP readback endpoints are speced.
