# Performance Hotspots -- gplearn Mining Runner

Logged during PR-3 B-axis rewrite (2026-05-20). DO NOT optimize in this PR.
These are candidates for the future Rust rewrite lane.

---

## 1. `_run_vbt_simple` -- per-fold pivot materialization

Location: `mine.py::_run_vbt_simple`

Pattern:
```python
signal_df.pivot(index="event_time", on="asset_id", values="close")
    .sort("event_time")
    .to_pandas()
    .set_index("event_time")
```

Called once per fold per top-K program. With `n_folds=6` and `top_k=5` that
is up to 30 pivot + to_pandas materializations per run. Each pivot produces a
wide DataFrame (n_timestamps x n_assets) that is immediately copied to pandas.

Rust path: stay columnar/Arrow throughout; compute pairwise Sharpe without
pivot by maintaining running returns per asset bucket. Eliminate the pandas
bridge entirely.

---

## 2. `get_top_k_programs` + program re-scan in `run_mining`

Location: `mine.py::get_top_k_programs` and the re-find-by-string loop
inside `run_mining`.

Pattern:
```python
# get_top_k_programs: O(generations * population_size) scan
for gen in est._programs:
    for prog in gen: ...

# run_mining: second O(generations * population_size) scan per top-K candidate
for gen in est._programs:
    for p in gen:
        if str(p) == expr: ...
```

With `population_size=500, generations=20` this is 10 000 iterations per
`get_top_k_programs` call, then 10 000 * top_k iterations for the re-find
loop. String comparison on expression trees is not free.

Fix: carry the program object directly in the dict returned by
`get_top_k_programs`. Eliminates the re-find loop entirely.

Rust path: maintain a fixed-size max-heap (size = top_k) during evolution
rather than scanning `_programs` post-hoc. O(population_size * log(top_k))
total instead of O(population_size * generations * top_k).

---

## 3. `_to_numpy` -- redundant float64 copy

Location: `mine.py::_to_numpy`

Pattern:
```python
X = df_clean.select(feature_cols).to_numpy().astype(np.float64)
```

`.to_numpy()` on a polars Float64 DataFrame already returns float64 in most
cases, but `.astype(np.float64)` forces a second full-array copy regardless.

Fix: check dtype first, skip astype if already float64.
```python
arr = df_clean.select(feature_cols).to_numpy()
X = arr if arr.dtype == np.float64 else arr.astype(np.float64)
```

Rust path: use Arrow `RecordBatch` -> numpy zero-copy view
(`pyarrow.RecordBatch.to_pydict` + `np.frombuffer`) or polars
`to_numpy(zero_copy_only=True)` where the dtype allows it.

---

## 4. `evaluate_factor_callable` -- linear `is_in` filter per fold

Location: `mine.py::evaluate_factor_callable`

Pattern:
```python
df_oos = df_eval.filter(pl.col("event_time").is_in(oos_times.to_list()))
```

Called once per fold. `is_in` on a large panel is O(n_rows * n_oos_times)
without an index. With 6 folds and a 3-year panel this runs 6 full scans.

Fix: add an integer time-index column to df_eval (rank of event_time), then
use range filtering instead of set membership. O(n_rows) with a sorted scan.

Rust path: partition-by-time with a bitmap index; fold boundaries computed
once and stored as byte offsets into the Arrow buffer.

---

## 5. Cross-sectional z-score recomputed on every `add_*` call

Location: `markets/crypto/features.py::_cs_zscore_expr` and
`markets/ashare/features.py::_cs_zscore_expr`

Pattern: `pl.col(col).mean().over("event_time")` + `pl.col(col).std(ddof=0).over("event_time")`

Each group z-score computes mean and std independently. If multiple columns
share the same grouping key (`event_time`), polars may not fuse these into a
single pass. For large cross-sections (A-share: 5000 symbols x 2000 dates)
this is 2 passes per feature column per z-score group.

Fix: pass a pre-computed stats frame into the z-score helper to avoid
redundant aggregations. Or rely on polars CSE (common subexpression
elimination) -- verify CSE is active for `over()` expressions in the polars
version in use.

Rust path: compute all cross-sectional stats in a single vectorized pass per
timestamp using SIMD mean+var accumulator; apply normalization in a second
fused map pass.

---

## 6. A-share macro join -- `join_asof` on full unique-dates frame

Location: `markets/ashare/features.py::add_a4_macro_features`

Pattern:
```python
dates_df = df.select(...).unique().sort(...)
dates_joined = dates_df.join_asof(macro_for_join, ...)
df = df.join(dates_joined, on="_trade_date", how="left")
```

Two scans of df (unique extraction + final join). For a 5000-symbol x
2000-day panel this is 10M row-operations just for macro join.

Fix: pre-sort df on event_time and use `join_asof` directly between df and
macro_for_join without the intermediate unique step. Requires polars
`join_asof` to handle duplicate left keys (check polars version support).

Rust path: Arrow-native as-of join with binary search on sorted timestamps;
no intermediate materialization.
