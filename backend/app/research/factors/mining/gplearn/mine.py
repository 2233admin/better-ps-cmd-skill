"""gplearn symbolic regression factor miner -- universe-agnostic (PR-3 B-axis).

Rewrites the archive/crypto-w1-complete runner against the market-adapter
pattern introduced in PR-1.  Key changes vs the archive:

  - Universe routing via ``_universe_loader.load_for_mining()`` instead of
    the old ``_get_inst_col(universe)`` gate.
  - Feature construction delegated to ``markets/{universe}/features.py``
    (called indirectly via ``_universe_loader``).  No feature math here.
  - Canonical schema (asset_id, event_time, market) after adapter rename;
    the ``symbol -> inst_id`` rename hack in the old A-share path is gone.
  - Single ``run_mining()`` function; the separate ``run_mining_ashare()``
    is eliminated.
  - CLI surface identical to archive (same flags, same defaults, same
    output artifacts).

Usage (crypto, unchanged from archive):
    uv run python -m app.research.factors.mining.gplearn.mine \\
        --combined-parquet D:\\projects\\k-atana\\.data\\lake\\crypto\\kline_pit_combined.parquet \\
        --out-dir D:\\projects\\k-atana\\artifacts\\group-d-gplearn-run

Usage (A-share):
    uv run python -m app.research.factors.mining.gplearn.mine \\
        --universe ashare \\
        --ashare-kline D:\\projects\\k-atana\\.data\\lake\\pit\\kline_daily_pit.parquet \\
        --ashare-valuation D:\\projects\\k-atana\\.data\\lake\\pit\\valuation_daily_pit.parquet \\
        --ashare-industry D:\\projects\\k-atana\\.data\\lake\\pit\\industry_classification_pit.parquet \\
        --ashare-macro D:\\projects\\k-atana\\.data\\lake\\pit\\macro_cn_monthly_pit.parquet \\
        --out-dir D:\\projects\\k-atana\\artifacts\\ashare-mining-2026-05-19

Design notes:
    - PIT safety: all lags use .shift(N).over("asset_id") inside the loader
      and features modules so windows never cross instrument boundaries.
    - gplearn sees a flat numpy feature matrix -- no knowledge of asset_id or
      timestamp; PIT semantics enforced in polars before materialization.
    - Training targets: crypto=fwd_ret24 (24-bar), ashare=fwd_ret_5d (5 days).
    - gplearn function_set: built-in safe set (add/sub/mul/div/sqrt/log/abs/neg).
    - Wheel decision: gplearn chosen over PySR (Julia dependency) and DEAP
      (no sklearn API). See archive final_report.md for full audit.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy import stats

from app.research.factors.mining._universe_loader import (
    UniverseBundle,
    load_for_mining,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# ---------------------------------------------------------------------------
# Numpy extraction (universe-agnostic)
# ---------------------------------------------------------------------------

def _to_numpy(
    bundle: UniverseBundle,
) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    """Extract X, y arrays and a cleaned DataFrame from a UniverseBundle.

    Drops rows with nulls or non-finite values in feature_cols + target_col.
    Returns (X, y, df_clean) where df_clean retains all columns for downstream
    re-join after predict().

    PERF-NOTE: .to_numpy().astype(np.float64) creates a full copy even when
    the polars dtype is already Float64.  Consider pl.DataFrame.to_numpy()
    with zero_copy_only=True or moving to Arrow-backed tensors in a Rust
    rewrite.
    """
    df = bundle["df_feat"]
    feature_cols = bundle["feature_cols"]
    target_col = bundle["target_col"]

    df_clean = df.drop_nulls(subset=feature_cols + [target_col])
    for col in feature_cols + [target_col]:
        df_clean = df_clean.filter(pl.col(col).is_finite())

    X = df_clean.select(feature_cols).to_numpy().astype(np.float64)
    y = df_clean[target_col].to_numpy().astype(np.float64)
    return X, y, df_clean


# ---------------------------------------------------------------------------
# gplearn training
# ---------------------------------------------------------------------------

def train_symbolic_regressor(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    population_size: int = 500,
    generations: int = 20,
    parsimony_coefficient: float = 0.001,
    random_state: int = 42,
    n_jobs: int = -1,
) -> Any:
    """Train a SymbolicRegressor on (X, y).

    function_set uses gplearn built-ins only (element-wise, NaN-safe internals).
    Custom ts_lag is NOT used -- lags are pre-computed feature columns.
    feature_names: human-readable names passed to gplearn.
    """
    from gplearn.genetic import SymbolicRegressor  # noqa: PLC0415 -- lazy: optional dep

    est = SymbolicRegressor(
        population_size=population_size,
        generations=generations,
        parsimony_coefficient=parsimony_coefficient,
        function_set=("add", "sub", "mul", "div", "sqrt", "log", "abs", "neg"),
        metric="spearman",           # maximise rank correlation ~ IC
        tournament_size=20,
        stopping_criteria=1.0,       # Spearman bounded at 1.0 -> never early-stops
        const_range=(-1.0, 1.0),
        init_depth=(2, 6),
        init_method="half and half",
        max_samples=1.0,
        verbose=1,
        random_state=random_state,
        n_jobs=n_jobs,
        feature_names=feature_names,
    )
    est.fit(X, y)
    return est


# ---------------------------------------------------------------------------
# Walk-forward evaluation helpers
# ---------------------------------------------------------------------------

def _compute_ic(sig_arr: np.ndarray, ret_arr: np.ndarray) -> tuple[float, float]:
    """Spearman rank-IC and t-stat. Returns (0.0, 0.0) if fewer than 10 pairs."""
    valid = np.isfinite(sig_arr) & np.isfinite(ret_arr)
    n = int(valid.sum())
    if n < 10:
        return 0.0, 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ic, _ = stats.spearmanr(sig_arr[valid], ret_arr[valid])
    ic = float(ic)
    if not math.isfinite(ic):
        return 0.0, 0.0
    denom = math.sqrt(max(1 - ic ** 2, 1e-12))
    ic_t = ic * math.sqrt(n - 2) / denom
    return ic, ic_t


def _run_vbt_simple(
    signal_df: pl.DataFrame,
    freq: str = "1h",
    init_cash: float = 100_000.0,
    top_k: int = 1,
) -> float:
    """Long-short Sharpe via vectorbt on a slice [event_time, asset_id, close, signal].

    Returns 0.0 on any failure.

    PERF-NOTE: pivot() + to_pandas() materializes a wide in-memory DataFrame
    every fold for every program.  With many assets or folds this is the
    dominant allocator.  A future Rust path should stay in columnar/Arrow
    throughout and compute pairwise Sharpe without pivot.
    """
    try:
        import vectorbt as vbt  # noqa: PLC0415

        close_wide = (
            signal_df.select(["event_time", "asset_id", "close"])
            .pivot(index="event_time", on="asset_id", values="close")
            .sort("event_time")
            .to_pandas()
            .set_index("event_time")
        )
        sig_wide = (
            signal_df.select(["event_time", "asset_id", "signal"])
            .pivot(index="event_time", on="asset_id", values="signal")
            .sort("event_time")
            .to_pandas()
            .set_index("event_time")
        )

        if len(close_wide) < 20:
            return 0.0

        sig_rank = sig_wide.rank(axis=1, method="average", na_option="keep")
        n_assets = sig_wide.shape[1]
        if n_assets < 2:
            return 0.0

        k = max(1, min(int(top_k), n_assets // 2))
        long_bucket = sig_rank >= (n_assets - k + 1)
        short_bucket = sig_rank <= k

        prev_long_bucket = long_bucket.shift(1, fill_value=False)
        prev_short_bucket = short_bucket.shift(1, fill_value=False)
        long_entries = long_bucket & ~prev_long_bucket
        short_entries = short_bucket & ~prev_short_bucket
        long_exits = ~long_bucket & prev_long_bucket
        short_exits = ~short_bucket & prev_short_bucket

        pf = vbt.Portfolio.from_signals(
            close=close_wide,
            entries=long_entries,
            exits=long_exits,
            short_entries=short_entries,
            short_exits=short_exits,
            fees=0.0005,
            slippage=0.0005,
            freq=freq,
            group_by=True,
            cash_sharing=True,
            init_cash=init_cash,
        )
        sharpe = float(pf.sharpe_ratio())
        return sharpe if np.isfinite(sharpe) else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def evaluate_factor_callable(
    predict_fn: Any,
    bundle: UniverseBundle,
    df_clean: pl.DataFrame,
    X_full: np.ndarray,
    n_folds: int = 6,
    embargo_bars: int = 24,
    top_k: int = 1,
) -> dict[str, Any]:
    """Walk-forward IC IR with a positive-Sharpe gate for a callable signal factory.

    predict_fn: callable(X: np.ndarray) -> np.ndarray (1-D signal)
    bundle:     UniverseBundle (for df_feat and asset_col)
    df_clean:   NaN-dropped frame aligned row-for-row with X_full (gplearn-eligible rows)
    X_full:     feature matrix aligned with df_clean

    P0-1 fix: NaN/inf filtering happens PER FOLD on the full panel (not globally
    pre-split), preventing future-cleanliness leakage into evaluation.
    P0-4 fix: Uses 1-bar forward return (shift(-1) per asset) as IC scoring target,
    not backward/current return. Evaluation target = 1-bar forward.
    Training target = configurable fwd_ret_<N>d.

    PERF-NOTE: df_eval.filter(pl.col("event_time").is_in(oos_times.to_list()))
    on a large panel triggers a linear scan per fold.  An integer-indexed
    time axis with range-filter would be O(1) instead of O(n).
    """
    try:
        from sklearn.model_selection import TimeSeriesSplit  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "scikit-learn is required for walk-forward evaluation. "
            "Install it with: uv add scikit-learn  "
            "or install the 'ml' extra: uv add 'k-atana[ml]'"
        ) from exc

    asset_col = bundle["asset_col"]  # always "asset_id" after adapter rename
    df_feat = bundle["df_feat"]

    # P0-4 FIX: Build forward_ret on the FULL unfiltered panel (df_feat), not df_clean.
    # forward_ret[t] = log(close[t+1]) - log(close[t]), grouped per asset.
    # Using shift(-1) on _log_close to avoid chaining diff().shift() confusion.
    # Last row per asset will be NaN; per-fold is_finite filter handles it.
    # Evaluation target = 1-bar forward. Training target = configurable fwd_ret_<N>d.
    # (The old "next_ret" was current/backward: log(close[t]) - log(close[t-1]).)
    df_full = df_feat.sort([asset_col, "event_time"]).with_columns(
        pl.col("close").log(base=math.e).alias("_log_close")
    ).with_columns(
        (pl.col("_log_close").shift(-1).over(asset_col) - pl.col("_log_close")).alias("forward_ret")
    ).drop("_log_close")

    # P0-1 FIX: Signals are predicted on df_clean (the NaN/inf-eligible rows used
    # for gplearn training). Attach signals back via join so df_full rows outside
    # df_clean (future-null or non-finite) stay in the full panel with signal=null.
    # Per-fold NaN/inf filtering happens INSIDE each fold (not globally pre-split),
    # preventing future-cleanliness leakage into the training fold.
    signals = predict_fn(X_full)
    # Build a signal lookup keyed on (asset_col, event_time) from df_clean rows.
    df_signal_map = df_clean.select([asset_col, "event_time"]).with_columns(
        pl.Series("signal", signals.astype(np.float64))
    )
    df_eval = df_full.join(df_signal_map, on=[asset_col, "event_time"], how="left")

    all_times = df_eval["event_time"].unique().sort()
    n_times = len(all_times)

    test_size = min(336, (n_times - embargo_bars * n_folds) // (n_folds + 1))
    test_size = max(test_size, 20)

    tscv = TimeSeriesSplit(n_splits=n_folds, test_size=test_size, gap=embargo_bars)

    per_fold: list[dict[str, Any]] = []
    fold_scores: list[float] = []
    ic_vals: list[float] = []
    sharpe_vals: list[float] = []

    for fold_idx, (_, test_idx) in enumerate(tscv.split(range(n_times))):
        oos_times = all_times[list(test_idx)]
        # P0-1: drop_nulls and finite filter PER FOLD (not globally pre-split).
        df_oos = df_eval.filter(
            pl.col("event_time").is_in(oos_times.to_list())
        ).drop_nulls(subset=["signal", "forward_ret"]).filter(
            pl.col("signal").is_finite() & pl.col("forward_ret").is_finite()
        )

        sig_arr = df_oos["signal"].to_numpy()
        ret_arr = df_oos["forward_ret"].to_numpy()
        ic, ic_t = _compute_ic(sig_arr, ret_arr)

        # Sharpe gate uses asset_id column name (canonical after adapter)
        sharpe = _run_vbt_simple(
            df_oos.select(["event_time", asset_col, "close", "signal"]).rename(
                {asset_col: "asset_id"}
            ),
            top_k=top_k,
        )

        fold_scores.append(ic)
        ic_vals.append(ic)
        sharpe_vals.append(sharpe)
        per_fold.append({
            "fold": fold_idx,
            "oos_start": str(oos_times[0]),
            "oos_end": str(oos_times[-1]),
            "ic": round(ic, 6),
            "ic_t_stat": round(ic_t, 4),
            "sharpe": round(sharpe, 6),
            "fold_sharpe": round(sharpe, 6),
            "n_oos_rows": len(df_oos),
            "fold_score": round(ic, 6),
        })

    if fold_scores:
        mean_ic = float(np.mean(ic_vals))
        std_ic = float(np.std(ic_vals))
        mean_sharpe = float(np.mean(sharpe_vals))
        std_sharpe = float(np.std(sharpe_vals))
        information_ratio = mean_ic / max(std_ic, 1e-6)
        positive_sharpe_gate = mean_sharpe > 0
        combined_score = information_ratio if positive_sharpe_gate else -abs(information_ratio)
    else:
        mean_ic = 0.0
        std_ic = 0.0
        mean_sharpe = 0.0
        std_sharpe = 0.0
        information_ratio = -999.0
        positive_sharpe_gate = False
        combined_score = -999.0

    return {
        "combined_score": combined_score,
        "metric": "fitness_wf_ir_sharpe_gated",
        "details": {
            "n_folds": len(per_fold),
            "embargo_bars": embargo_bars,
            "top_k": top_k,
            "mean_ic": round(mean_ic, 6),
            "std_ic": round(std_ic, 6),
            "information_ratio": round(information_ratio, 6),
            "mean_sharpe": round(mean_sharpe, 6),
            "std_sharpe": round(std_sharpe, 6),
            "positive_sharpe_gate": positive_sharpe_gate,
            "worst_fold_score": round(float(min(fold_scores)), 6) if fold_scores else -999.0,
            "per_fold": per_fold,
        },
    }


# ---------------------------------------------------------------------------
# Top-K program extraction
# ---------------------------------------------------------------------------

def get_top_k_programs(
    est: SymbolicRegressor,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Extract top-K unique programs across all generations by fitness.

    PERF-NOTE: O(generations * population_size) linear scan.  With
    population_size=500 and generations=20 this is 10k iterations per call.
    In a Rust rewrite, maintain a fixed-size max-heap during evolution
    rather than scanning _programs post-hoc.
    """
    all_programs = est._programs  # noqa: SLF001
    if not all_programs:
        return []

    seen: dict[str, Any] = {}
    for gen in all_programs:
        if gen is None:
            continue
        for prog in gen:
            if prog is None:
                continue
            expr_str = str(prog)
            if expr_str not in seen or prog.fitness_ > seen[expr_str].fitness_:
                seen[expr_str] = prog

    unique_sorted = sorted(seen.values(), key=lambda p: p.fitness_, reverse=True)
    results = []
    for rank, prog in enumerate(unique_sorted[:k]):
        results.append({
            "rank": rank + 1,
            "expression": str(prog),
            "fitness_train": round(float(prog.fitness_), 6),
            "length": prog.length_,
            "depth": prog.depth_,
        })
    return results


# ---------------------------------------------------------------------------
# Main mining pipeline (universe-agnostic)
# ---------------------------------------------------------------------------

def run_mining(
    universe: str,
    out_dir: str,
    population_size: int = 500,
    generations: int = 20,
    parsimony_coefficient: float = 0.001,
    top_k: int = 5,
    n_jobs: int = -1,
    # crypto
    combined_parquet: str | None = None,
    # ashare
    ashare_kline: str | None = None,
    ashare_valuation: str | None = None,
    ashare_industry: str | None = None,
    ashare_macro: str | None = None,
    ashare_target_days: int = 5,
    ashare_sample_symbols: int | None = None,
) -> None:
    """Universe-agnostic gplearn mining pipeline.

    Loads data via _universe_loader, trains SymbolicRegressor, evaluates
    top-K programs with walk-forward IC/Sharpe, writes artifacts to out_dir.

    Artifacts produced (same as archive):
      - top_factors.json
      - dsr_filter_result.json  (if dsr_pbo available)
      - final_report.md
      - ashare_mining_summary.json  (ashare only)
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # ---- Load data + build features via adapter ----
    print(f"[gplearn miner] Universe: {universe}")
    if universe == "crypto":
        if combined_parquet is None:
            raise ValueError("--combined-parquet required for crypto universe.")
        print(f"[gplearn miner] Loading parquet: {combined_parquet}")
        bundle = load_for_mining(
            universe="crypto",
            combined_parquet=combined_parquet,
        )
    else:  # ashare
        if ashare_kline is None or ashare_valuation is None:
            raise ValueError("--ashare-kline and --ashare-valuation required for ashare universe.")
        print(f"[gplearn miner] Loading kline: {ashare_kline}")
        print(f"[gplearn miner] Loading valuation: {ashare_valuation}")
        bundle = load_for_mining(
            universe="ashare",
            kline_parquet=ashare_kline,
            valuation_parquet=ashare_valuation,
            industry_parquet=ashare_industry,
            macro_parquet=ashare_macro,
            target_days=ashare_target_days,
            sample_symbols=ashare_sample_symbols,
        )

    df_feat = bundle["df_feat"]
    feature_cols = bundle["feature_cols"]
    feature_names = bundle["feature_names"]
    target_col = bundle["target_col"]

    asset_col = bundle["asset_col"]  # "asset_id" for both universes
    n_assets = df_feat[asset_col].n_unique() if asset_col in df_feat.columns else "?"
    print(f"[gplearn miner] Feature panel: {df_feat.shape}, assets: {n_assets}")
    print(f"[gplearn miner] Features ({len(feature_cols)}): {feature_names}")

    # ---- Extract numpy arrays ----
    X, y, df_clean = _to_numpy(bundle)
    print(f"[gplearn miner] After NaN/inf drop: X={X.shape}, y={y.shape}")

    if X.shape[0] < 100:
        raise RuntimeError(f"Too few rows after NaN drop: {X.shape[0]}. Check data coverage.")

    # ---- Train SymbolicRegressor ----
    print(
        f"[gplearn miner] Training SymbolicRegressor "
        f"pop={population_size} gens={generations}..."
    )
    est = train_symbolic_regressor(
        X,
        y,
        feature_names=feature_names,
        population_size=population_size,
        generations=generations,
        parsimony_coefficient=parsimony_coefficient,
        n_jobs=n_jobs,
    )
    print("[gplearn miner] Training complete.")
    print(f"[gplearn miner] Best expression: {est._program}")  # noqa: SLF001

    # ---- Extract top-K programs ----
    print(f"[gplearn miner] Extracting top-{top_k} programs...")
    top_programs = get_top_k_programs(est, k=top_k)

    # ---- Evaluate with WF IC/Sharpe gate ----
    print("[gplearn miner] Evaluating top-K on Walk-Forward IR with Sharpe gate...")
    top_factors: list[dict[str, Any]] = []

    for prog_info in top_programs:
        rank = prog_info["rank"]
        expr = prog_info["expression"]
        print(f"  [{rank}/{top_k}] Evaluating: {expr[:80]}...")

        # PERF-NOTE: O(generations * population_size) re-scan to find the
        # program object by string.  Could be avoided by carrying the program
        # object in get_top_k_programs().
        prog_obj = None
        for gen in est._programs:  # noqa: SLF001
            if gen is None:
                continue
            for p in gen:
                if p is not None and str(p) == expr:
                    if prog_obj is None or p.fitness_ > prog_obj.fitness_:
                        prog_obj = p

        if prog_obj is None:
            print(f"  [{rank}] Program object not found, skipping.")
            continue

        def make_predict_fn(p: Any):
            def predict_fn(X_arr: np.ndarray) -> np.ndarray:
                return p.execute(X_arr)
            return predict_fn

        wf_result = evaluate_factor_callable(
            predict_fn=make_predict_fn(prog_obj),
            bundle=bundle,
            df_clean=df_clean,
            X_full=X,
        )

        top_factors.append({
            "rank": rank,
            "expression": expr,
            "train_fitness": prog_info["fitness_train"],
            "complexity": prog_info["length"],
            "depth": prog_info["depth"],
            "wf_combined_score": wf_result["combined_score"],
            "wf_mean_ic": wf_result["details"]["mean_ic"],
            "wf_std_ic": wf_result["details"]["std_ic"],
            "wf_information_ratio": wf_result["details"]["information_ratio"],
            "wf_mean_sharpe": wf_result["details"]["mean_sharpe"],
            "wf_std_sharpe": wf_result["details"]["std_sharpe"],
            "wf_positive_sharpe_gate": wf_result["details"]["positive_sharpe_gate"],
            "wf_worst_fold_score": wf_result["details"]["worst_fold_score"],
            "wf_per_fold": wf_result["details"]["per_fold"],
        })
        print(
            f"  [{rank}] WF IC={wf_result['details']['mean_ic']:+.4f} "
            f"Sharpe={wf_result['details']['mean_sharpe']:+.3f} "
            f"Score={wf_result['combined_score']:+.4f}"
        )

    top_factors.sort(key=lambda x: x["wf_combined_score"], reverse=True)

    # ---- Write top_factors.json ----
    top_json_path = out_path / "top_factors.json"
    top_json_path.write_text(json.dumps(top_factors, indent=2))
    print(f"[gplearn miner] Wrote {top_json_path}")

    # ---- DSR / BH multiple-testing filter ----
    try:
        from app.research.methodology.dsr_pbo import dsr_filter as _dsr_filter  # noqa: PLC0415
        _n_trials = population_size * generations
        dsr_result = _dsr_filter(
            top_factors,
            n_trials=_n_trials,
            sr_key="wf_information_ratio",
            folds_key="wf_per_fold",
        )
        dsr_path = out_path / "dsr_filter_result.json"
        dsr_path.write_text(json.dumps(dsr_result, indent=2, default=str))
        print(
            f"[gplearn miner] DSR/BH filter: "
            f"{dsr_result['n_surviving']}/{dsr_result['n_input']} survived "
            f"(n_trials={_n_trials}) -> {dsr_path}"
        )
    except Exception as _e:  # noqa: BLE001
        print(f"[gplearn miner] DSR filter error (non-fatal): {_e}")

    # ---- Write final_report.md ----
    _write_report(
        top_factors=top_factors,
        universe=universe,
        feature_names=feature_names,
        out_path=out_path,
        est=est,
        X_shape=X.shape,
        y_shape=y.shape,
    )

    # ---- A-share summary JSON (mirrors archive artifact) ----
    if universe == "ashare":
        summary = {
            "universe": "ashare",
            "target_col": target_col,
            "feature_cols": feature_cols,
            "X_shape": list(X.shape),
            "y_shape": list(y.shape),
            "population_size": population_size,
            "generations": generations,
            "top_factors": top_factors[:5],
        }
        summary_path = out_path / "ashare_mining_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2))
        print(f"[gplearn miner] Wrote {summary_path}")

    print(f"[gplearn miner] Done. Artifacts in {out_path}")


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(
    top_factors: list[dict[str, Any]],
    universe: str,
    feature_names: list[str],
    out_path: Path,
    est: SymbolicRegressor,
    X_shape: tuple,
    y_shape: tuple,
) -> None:
    lines: list[str] = [
        f"# gplearn Factor Mining -- Final Report ({universe})",
        "",
        "## Run configuration",
        "",
        f"- universe: {universe}",
        f"- population_size: {est.population_size}",
        f"- generations: {est.generations}",
        f"- parsimony_coefficient: {est.parsimony_coefficient}",
        f"- function_set: {est.function_set}",
        f"- metric: {est.metric}",
        f"- feature matrix shape: X={X_shape}, y={y_shape}",
        "",
        "## Feature columns",
        "",
        "| # | Name |",
        "|---|------|",
    ]
    for i, name in enumerate(feature_names, 1):
        lines.append(f"| {i} | {name} |")

    lines += [
        "",
        f"## Top-{len(top_factors)} discovered factors (sorted by WF IR with Sharpe gate)",
        "",
    ]

    for f in top_factors:
        lines += [
            f"### Rank {f['rank']}: {f['expression'][:120]}",
            "",
            f"- **Expression**: `{f['expression']}`",
            f"- **Complexity (length)**: {f['complexity']}  **Depth**: {f['depth']}",
            f"- **Train fitness (Spearman)**: {f['train_fitness']:+.4f}",
            f"- **WF mean IC**: {f['wf_mean_ic']:+.4f} +/- {f['wf_std_ic']:.4f}",
            f"- **WF IR**: {f['wf_information_ratio']:+.4f}",
            f"- **WF mean Sharpe**: {f['wf_mean_sharpe']:+.3f} +/- {f.get('wf_std_sharpe', 0.0):.3f}",
            f"- **WF positive-Sharpe gate**: {f['wf_positive_sharpe_gate']}",
            f"- **WF combined score**: {f['wf_combined_score']:+.4f}",
            f"- **WF worst fold IC**: {f['wf_worst_fold_score']:+.4f}",
            "",
            "| Fold | OOS start | IC | IC t-stat | Sharpe | Fold score (IC) |",
            "|------|-----------|-----|-----------|--------|-------|",
        ]
        for fold in f.get("wf_per_fold", []):
            lines.append(
                f"| {fold['fold']} | {fold['oos_start'][:10]} "
                f"| {fold['ic']:+.4f} | {fold['ic_t_stat']:+.2f} "
                f"| {fold['sharpe']:+.3f} | {fold['fold_score']:+.4f} |"
            )
        lines.append("")

    lines += [
        "## Architecture note (PR-3 B-axis)",
        "",
        "Feature construction is fully delegated to `markets/{universe}/features.py`",
        "(PR-1 stable artifacts).  This runner owns only:",
        "  - universe dispatch via `_universe_loader.load_for_mining()`",
        "  - numpy extraction, gplearn fit, program extraction, WF evaluation",
        "  - artifact serialization",
        "",
        "## Wheel search note",
        "",
        "gplearn (pure Python, sklearn-idiom) chosen over:",
        "- PySR: requires Julia -- installation failed on this machine.",
        "- PyGAD: general GA, would require reimplementing expression trees.",
        "- DEAP: flexible but requires manual toolbox assembly (no sklearn API).",
        "",
        "gplearn 0.4.3 is the most direct drop-in for this task.",
    ]

    report_path = out_path / "final_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gplearn miner] Wrote {report_path}")


# ---------------------------------------------------------------------------
# CLI (identical surface to archive mine.py)
# ---------------------------------------------------------------------------

_LAKE_ROOT = Path(__file__).parent.parent.parent.parent.parent / ".data" / "lake"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="gplearn symbolic regression factor miner (crypto + ashare)."
    )
    parser.add_argument(
        "--universe",
        choices=["crypto", "ashare"],
        default="crypto",
        help="Universe to mine: 'crypto' (default) or 'ashare'.",
    )
    # Crypto args
    parser.add_argument(
        "--combined-parquet", "--combined_parquet",
        dest="combined_parquet",
        default=str(_LAKE_ROOT / "crypto" / "kline_pit_combined.parquet"),
        help="[crypto] Path to kline_pit_combined.parquet",
    )
    # A-share args
    parser.add_argument(
        "--ashare-kline", dest="ashare_kline",
        default=str(_LAKE_ROOT / "pit" / "kline_daily_pit.parquet"),
        help="[ashare] Path to kline_daily_pit.parquet",
    )
    parser.add_argument(
        "--ashare-valuation", dest="ashare_valuation",
        default=str(_LAKE_ROOT / "pit" / "valuation_daily_pit.parquet"),
        help="[ashare] Path to valuation_daily_pit.parquet",
    )
    parser.add_argument(
        "--ashare-industry", dest="ashare_industry",
        default=str(_LAKE_ROOT / "pit" / "industry_classification_pit.parquet"),
        help="[ashare] Path to industry_classification_pit.parquet",
    )
    parser.add_argument(
        "--ashare-macro", dest="ashare_macro",
        default=str(_LAKE_ROOT / "pit" / "macro_cn_monthly_pit.parquet"),
        help="[ashare] Path to macro_cn_monthly_pit.parquet",
    )
    parser.add_argument(
        "--ashare-sample-symbols", dest="ashare_sample_symbols", type=int, default=None,
        help="[ashare] Randomly sample N symbols (for smoke runs).",
    )
    parser.add_argument(
        "--ashare-target-days", dest="ashare_target_days", type=int, default=5,
        help="[ashare] Forward return horizon in trading days (default 5).",
    )
    # Shared args
    parser.add_argument(
        "--out-dir", "--out_dir",
        dest="out_dir",
        default=str(Path(__file__).parent.parent.parent.parent.parent / "artifacts" / "group-d-gplearn-run"),
        help="Directory to write top_factors.json + final_report.md",
    )
    parser.add_argument("--population-size", type=int, default=500)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--parsimony-coefficient", type=float, default=0.001)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--n-jobs", type=int, default=-1)
    args = parser.parse_args()

    # Resolve optional file paths: pass None when the default path doesn't exist
    ashare_industry = args.ashare_industry
    if ashare_industry and not Path(ashare_industry).exists():
        ashare_industry = None

    ashare_macro = args.ashare_macro
    if ashare_macro and not Path(ashare_macro).exists():
        ashare_macro = None

    run_mining(
        universe=args.universe,
        out_dir=args.out_dir,
        population_size=args.population_size,
        generations=args.generations,
        parsimony_coefficient=args.parsimony_coefficient,
        top_k=args.top_k,
        n_jobs=args.n_jobs,
        combined_parquet=args.combined_parquet,
        ashare_kline=args.ashare_kline,
        ashare_valuation=args.ashare_valuation,
        ashare_industry=ashare_industry,
        ashare_macro=ashare_macro,
        ashare_target_days=args.ashare_target_days,
        ashare_sample_symbols=args.ashare_sample_symbols,
    )


if __name__ == "__main__":
    main()
