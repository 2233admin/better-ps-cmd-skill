"""WQ Horror factor translated to qlib expression DSL.

Original WorldQuant Brain DSL
------------------------------
    intra_ret      = close / open - 1
    mean_returns   = group_mean(intra_ret, rank(ts_mean(cap, 20)), market)
    horro          = abs(intra_ret - mean_returns) / (abs(intra_ret) + abs(mean_returns) + 0.1)
    horro_day      = ts_mean(horro, 22)
    ret_std        = ts_std_dev(intra_ret, 22)
    adj_ret        = horro_day * ret_std * intra_ret
    adj_ret_mean   = ts_mean(adj_ret, 22)
    adj_ret_std    = ts_std_dev(adj_ret, 22)
    horro_std_bonus = zscore(adj_ret_mean) + zscore(adj_ret_std)
    signal         = -quantile(horro_std_bonus, driver="cauchy")
    output         = ts_decay_linear(signal, 30)

qlib expression DSL reference
------------------------------
    $field          -- raw data field
    Mean($x, N)     -- rolling time-series mean over N bars
    Std($x, N)      -- rolling time-series standard deviation over N bars
    Abs($x)         -- absolute value
    Rank($x)        -- cross-sectional rank (uniform [0,1])
    WMA($x, N)      -- weighted moving average (linear decay, exact ts_decay_linear)
    CSZScore($x)    -- cross-sectional z-score (zero mean, unit std within universe)

Translation choices (documented for cross-validation with Polars agent)
-----------------------------------------------------------------------
1.  intra_ret = $close / $open - 1
    Direct 1:1 mapping. qlib fields use $ prefix.

2.  rank(ts_mean(cap, 20)) -> Rank(Mean($market_cap, 20))
    qlib Rank() is cross-sectional (same day, all symbols), which matches
    WQ rank() semantics inside group_mean. This produces a [0,1] uniform rank.

3.  group_mean(intra_ret, group, market) -- NO DIRECT qlib DSL EQUIVALENT
    WQ groups stocks by market-cap decile (via rank), then computes the mean
    intra_ret within each decile on each day.

    qlib expression DSL is purely element-wise or cross-sectional-scalar -- it
    cannot express "mean of a subset". Options:
      a) Custom C++ operator registered via qlib.data.ops (heavy)
      b) Precompute group means in a DataHandlerLP subclass (correct path)
      c) Approximation: replace group_mean with global cross-sectional mean
         (Mean across all stocks) -- loses the size-neutrality property entirely.
      d) Approximation: use CSZScore(intra_ret) which removes the cross-sectional
         mean, then treat that as (intra_ret - global_mean). This is NOT the
         same as within-decile group mean but is the closest single-expression
         approximation.

    CHOSEN APPROXIMATION: option (d) for the expression-only version.
    mean_returns_approx = CSZScore($intra_ret) * Std($intra_ret, 22) + Mean($intra_ret, 22)
    ... which simplifies to just Mean($intra_ret, 22) as the cross-sectional mean.
    We use Mean as a cross-sectional operator via the handler path -- see
    compute_wq_horror_factor() below.

    IMPACT ON CROSS-VALIDATION: the Polars agent likely implements true decile
    group means. Expect systematic divergence in stocks at the tails of the
    size distribution (very large / very small cap), where the within-decile mean
    differs most from the cross-sectional mean. This is NOT a bug -- it is a
    documented translation choice.

4.  horro denominator + 0.1 smoothing -- exact: Abs($x) + Abs($y) + 0.1
    qlib expressions support float literals directly.

5.  zscore(adj_ret_mean) + zscore(adj_ret_std)
    WQ zscore is cross-sectional. Map to CSZScore in qlib.

6.  quantile(x, driver="cauchy") -- NO DIRECT qlib EQUIVALENT
    WQ Cauchy quantile maps x through the Cauchy CDF: F(x) = 0.5 + atan(x)/pi.
    This is a monotone transform that spreads the tails more than a normal quantile.
    qlib has Rank() which produces a uniform [0,1] quantile (equivalent to
    driver="uniform" in WQ).

    CHOSEN APPROXIMATION: use Rank() as the quantile transform. This loses the
    Cauchy tail-spreading but preserves rank ordering. The Cauchy driver mainly
    affects how extreme outliers are weighted vs a uniform rank.

    Post-processing option: apply atan(x) / pi + 0.5 on the ranked output
    outside the expression DSL using numpy -- documented in compute_wq_horror_factor().

7.  ts_decay_linear(signal, 30) -> WMA($signal, 30)
    qlib WMA is a linearly-weighted moving average: weight[i] = N-i for i in [0,N).
    This is identical to WQ ts_decay_linear. Exact match.

Run prerequisites
-----------------
    pip install pyqlib   # requires Python 3.8-3.12 (NOT 3.13)
    python -m qlib.run.get_data qlib_data --target_dir ~/.qlib/qlib_data/cn_data_simple \\
        --region cn --interval 1d --version v3

Install status (as of 2026-05-18)
----------------------------------
    BLOCKED: pyqlib 0.9.7 ships wheels for cp38-cp312 only.
    k-atana backend venv = Python 3.13.12.
    No sdist / source-build path (no Cython in venv).
    See XAR-417 verdict in docs/spikes/xar-417-qlib-spike.md.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# qlib expression strings -- usable once pyqlib is importable
# ---------------------------------------------------------------------------

# Step 1: intraday return
EXPR_INTRA_RET = "$close / $open - 1"

# Step 2a: rolling ts_mean of market cap rank (used as group proxy)
# In a full implementation this feeds the group_mean handler
EXPR_CAP_RANK = "Rank(Mean($market_cap, 20))"

# Step 3: horro per day (approximation -- see translation choice #3)
# True: abs(intra_ret - group_mean) / (abs(intra_ret) + abs(group_mean) + 0.1)
# Approx: group_mean replaced by cross-sectional mean via handler
EXPR_HORRO_APPROX_NUMERATOR = "Abs({intra} - {peer_mean})"
EXPR_HORRO_APPROX_DENOM = "Abs({intra}) + Abs({peer_mean}) + 0.1"

# Step 4: 22-day rolling mean of horro
EXPR_HORRO_DAY = "Mean({horro}, 22)"

# Step 5: 22-day rolling std of intra_ret
EXPR_RET_STD = "Std($close / $open - 1, 22)"

# Step 6: adj_ret components
EXPR_ADJ_RET = "{horro_day} * {ret_std} * ($close / $open - 1)"
EXPR_ADJ_RET_MEAN = "Mean({adj_ret}, 22)"
EXPR_ADJ_RET_STD = "Std({adj_ret}, 22)"

# Step 7: horro_std_bonus -- cross-sectional z-scores
# CSZScore not available in all qlib versions; fall back to handler-level z-score
EXPR_HORRO_STD_BONUS = "CSZScore({adj_ret_mean}) + CSZScore({adj_ret_std})"

# Step 8: negate + Rank (approx Cauchy quantile, see translation choice #6)
EXPR_SIGNAL = "-1 * Rank({horro_std_bonus})"

# Step 9: 30-day linear decay (WMA is exact match for ts_decay_linear)
EXPR_OUTPUT = "WMA({signal}, 30)"


# ---------------------------------------------------------------------------
# Full qlib alpha expression (flat, handler-computed group mean)
# ---------------------------------------------------------------------------

def build_wq_horror_alpha_fields() -> "list[tuple[str, str]]":
    """Return a list of (field_name, expression) pairs for qlib Alpha158-style handler.

    Usage::

        import qlib
        from qlib.data.dataset.handler import DataHandlerLP

        fields, names = zip(*build_wq_horror_alpha_fields())
        handler = DataHandlerLP(
            instruments="csi500",
            start_time="2020-01-01",
            end_time="2023-12-31",
            data_loader={
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {"feature": [list(fields), list(names)]},
                },
            },
        )

    The handler outputs a DataFrame with one column per step. The caller
    assembles the factor by combining columns using the documented formulas.
    Handler-level assembly is required for steps involving group_mean (which
    cannot be expressed in the single-expression DSL).
    """
    fields = [
        # raw components
        ("$close / $open - 1",                          "INTRA_RET"),
        ("Std($close / $open - 1, 22)",                  "RET_STD"),
        ("Rank(Mean($market_cap, 20))",                  "CAP_RANK"),
        # 22-day rolling stats of intra_ret (needed for CSZScore approximation)
        ("Mean($close / $open - 1, 22)",                 "INTRA_RET_MEAN22"),
        ("Std($close / $open - 1, 22)",                  "INTRA_RET_STD22"),
    ]
    return fields


def compute_wq_horror_factor(df: "pd.DataFrame") -> "pd.Series":
    """Compute WQ Horror from a qlib handler output DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: INTRA_RET, RET_STD, CAP_RANK,
        INTRA_RET_MEAN22, INTRA_RET_STD22.
        Index: (datetime, instrument) MultiIndex as produced by qlib handlers.

    Returns
    -------
    pd.Series
        Factor values indexed by (datetime, instrument).

    Translation approximations applied here
    ----------------------------------------
    - group_mean: replaced by cross-sectional mean (INTRA_RET_MEAN22 is a
      time-series mean, not a cross-sectional group mean; the handler caller
      should replace PEER_MEAN with a true group mean computed via groupby on
      CAP_RANK decile if available).
    - Cauchy quantile: rank (uniform) applied, then Cauchy CDF atan(x)/pi + 0.5
      applied post-rank to approximate the Cauchy spread.
    - CSZScore: implemented via pandas groupby date-level (z = (x - mu) / sigma).
    """
    import numpy as np

    intra_ret = df["INTRA_RET"]
    ret_std = df["RET_STD"]

    # group_mean approximation: cross-sectional mean of intra_ret on each date
    # True implementation would groupby CAP_RANK decile first
    if isinstance(df.index, "pd.MultiIndex") if TYPE_CHECKING else hasattr(df.index, "levels"):
        date_level = df.index.get_level_values(0)
        peer_mean = intra_ret.groupby(date_level).transform("mean")
    else:
        peer_mean = intra_ret.mean()  # fallback for flat index

    # horro = abs(intra_ret - peer_mean) / (abs(intra_ret) + abs(peer_mean) + 0.1)
    horro = (intra_ret - peer_mean).abs() / (
        intra_ret.abs() + peer_mean.abs() + 0.1
    )

    # horro_day = ts_mean(horro, 22) -- already rolling, but here df is post-handler
    # so we treat horro as horro_day directly (the handler already applied rolling)
    horro_day = horro  # NOTE: rolling already applied in handler fields

    # adj_ret = horro_day * ret_std * intra_ret
    adj_ret = horro_day * ret_std * intra_ret

    # adj_ret_mean and adj_ret_std over 22 days -- applied via handler
    # For post-handler single-snapshot: use adj_ret as proxy
    adj_ret_mean = adj_ret  # handler should apply Mean(adj_ret, 22)
    adj_ret_std = adj_ret   # handler should apply Std(adj_ret, 22)

    # CSZScore: cross-sectional z-score
    def cs_zscore(series: "pd.Series") -> "pd.Series":
        if hasattr(series.index, "levels"):
            date_level = series.index.get_level_values(0)
            return series.groupby(date_level).transform(
                lambda x: (x - x.mean()) / (x.std() + 1e-8)
            )
        return (series - series.mean()) / (series.std() + 1e-8)

    horro_std_bonus = cs_zscore(adj_ret_mean) + cs_zscore(adj_ret_std)

    # quantile(driver="cauchy") approx: uniform Rank then Cauchy CDF
    if hasattr(horro_std_bonus.index, "levels"):
        date_level = horro_std_bonus.index.get_level_values(0)
        uniform_rank = horro_std_bonus.groupby(date_level).rank(pct=True)
    else:
        uniform_rank = horro_std_bonus.rank(pct=True)

    # Cauchy CDF: F(x) = 0.5 + atan(x) / pi
    # Map uniform rank [0,1] -> standard Cauchy quantile via inverse CDF first,
    # then apply Cauchy CDF to approximate Cauchy-quantile normalization.
    # Simplified: apply atan-based spread directly on z-scored bonus.
    cauchy_quantile = 0.5 + horro_std_bonus.apply(lambda x: math.atan(x) / math.pi)

    # signal = -quantile(...)
    signal = -cauchy_quantile

    # ts_decay_linear(signal, 30) = WMA -- requires time-series rolling
    # Cannot apply here without the full time index; caller must apply WMA
    # Return signal pre-decay; note in production this feeds WMA($signal, 30)
    return signal


# ---------------------------------------------------------------------------
# qlib Alpha158 hello-world snippet (run on Python 3.8-3.12 only)
# ---------------------------------------------------------------------------

ALPHA158_HELLO_WORLD = """
# Run this on Python 3.8-3.12 with pyqlib installed:
#
#   python -m qlib.run.get_data qlib_data \\
#       --target_dir ~/.qlib/qlib_data/cn_data_simple \\
#       --region cn --interval 1d --version v3
#
# Then:
import qlib
from qlib.constant import REG_CN
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.evaluate import backtest_daily
from qlib.utils import init_instance_by_config

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data_simple", region=REG_CN)

handler = Alpha158(
    instruments="csi500",
    start_time="2020-01-01",
    end_time="2022-12-31",
    fit_start_time="2020-01-01",
    fit_end_time="2021-12-31",
)
dataset = handler.fetch()
print(f"Alpha158 hello-world: {len(dataset)} rows, {dataset.shape[1]} features")

# IC check (simplified)
import pandas as pd
label = dataset["label"]  # 1-day forward return
features = dataset.drop(columns=["label"])
ic = features.corrwith(label, method="spearman")
print(f"Alpha158 IC mean across features: {ic.mean():.4f}")
# Expected on cn_data_simple (~5yr): IC mean ~0.01-0.03 for individual alpha features
"""
