# A-Share Source Gaps

This repo should not keep replaying the same full-market history from every
vendor. The rule is simple:

```text
Keep TDXCLI for canonical price bars.
Pull only the PIT-safe datasets that TDXCLI does not have.
After the first backfill, sync incrementally.
```

## TDXCLI Is Already Good For

- `ashare.kline_daily_pit`: canonical daily price bars
- `ashare.adjustment_factor_pit`: ex-rights/adjustment factors
- `ashare.corporate_action_pit`: corporate action evidence
- `normalized/tdx/finance_snapshot_*.parquet`: latest finance snapshot only
- `normalized/tdx/block_membership_*.parquet`: latest block snapshot only

The last two are not PIT-safe. They are evidence, not backtest facts.

## ChinaData/Tushare Should Fill These Gaps

1. `ashare.tradability_status_pit`
   - APIs: `namechange`, `suspend_d`, `stk_limit`
   - Why: TDXCLI does not provide PIT-safe historical ST, suspension, and
     official limit-price history.

2. `ashare.market_cap_daily_pit`
   - API: `daily_basic`
   - Why: TDX finance is only a latest snapshot. Market-cap neutralization and
     daily valuation factors need historical PIT rows.

3. `ashare.industry_daily_pit`
   - API: `bak_daily`
   - Why: TDX block files are current snapshots. Industry neutralization needs
     historical day-level classifications.

4. `ashare.share_float_event_pit`
   - API: `share_float`
   - Why: TDXCLI has no unlock-event history.

## Next PIT Targets

1. Financial statements
   - APIs: `disclosure_date`, `income_vip`, `balancesheet_vip`,
     `cashflow_vip`, `fina_indicator_vip`

2. Index constituents and weights
   - API: `index_weight`

3. Historical industry/block membership
   - APIs: `index_member_all`, `tdx_member`, `ths_member`

## Incremental Sync Rule

- `daily_basic` and `bak_daily`: sync by missing `trade_date` only.
- `share_float`: split date windows until each request is below the upstream
  `6000` row cap, then increment with a short overlap window.
- Do not re-pull TDX-covered daily price bars from ChinaData.

## Artifact

Generate the current source-gap report at:

```powershell
uv run --project backend python scripts\report-ashare-source-gaps.py `
  --data-root DATA\Ashare `
  --json
```

This writes `DATA/Ashare/_manifest/source_gap_report.json`.
