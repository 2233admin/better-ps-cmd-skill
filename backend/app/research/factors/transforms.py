"""Cross-sectional transform primitives for factor construction.

Pure functions that operate on either a ``pl.Series`` (eager) or a ``pl.Expr``
(composable into the lazy pipeline used by ``pipeline/runner.py``). All
transforms preserve null entries and never mutate inputs.

These primitives are intentionally narrow: they take values plus the minimum
context (lower/upper bound, group key) and return the same shape they received.
Composition into factor families lives in :mod:`families`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

import polars as pl

ValuesLike = Union[pl.Series, pl.Expr]


@dataclass(frozen=True)
class WinsorizeBounds:
    """Quantile bounds used by :func:`winsorize`. Stored for audit metadata."""

    lower: float
    upper: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.lower < self.upper <= 1.0:
            raise ValueError(
                f"winsorize bounds must satisfy 0 <= lower < upper <= 1; "
                f"got lower={self.lower}, upper={self.upper}"
            )


def zscore(values: ValuesLike) -> ValuesLike:
    """Cross-sectional z-score: ``(x - mean(x)) / std(x)``.

    Nulls are skipped when computing mean/std and are preserved in the output.
    Returns the same type as the input (``Series`` -> ``Series``,
    ``Expr`` -> ``Expr``). When ``std == 0`` the result is all zeros for the
    non-null entries (avoids ``inf``/``nan``).
    """

    if isinstance(values, pl.Series):
        clean = values.drop_nulls()
        if clean.len() == 0:
            return values
        mean = float(clean.mean())
        std = float(clean.std(ddof=0)) if clean.len() > 0 else 0.0
        if std == 0.0:
            return (values - mean) * 0.0
        return (values - mean) / std

    mean_expr = values.mean()
    std_expr = values.std(ddof=0)
    return (
        pl.when(std_expr == 0)
        .then((values - mean_expr) * 0.0)
        .otherwise((values - mean_expr) / std_expr)
    )


def winsorize(
    values: ValuesLike,
    lower: float = 0.01,
    upper: float = 0.99,
) -> ValuesLike:
    """Clip ``values`` to the [lower, upper] quantile range, cross-sectionally.

    Quantiles are computed over the supplied vector (typically a single
    cross-section / single date slice). Nulls are preserved.
    """

    bounds = WinsorizeBounds(lower=lower, upper=upper)

    if isinstance(values, pl.Series):
        clean = values.drop_nulls()
        if clean.len() == 0:
            return values
        lo = float(clean.quantile(bounds.lower, interpolation="linear"))
        hi = float(clean.quantile(bounds.upper, interpolation="linear"))
        return values.clip(lo, hi)

    lo_expr = values.quantile(bounds.lower, interpolation="linear")
    hi_expr = values.quantile(bounds.upper, interpolation="linear")
    return values.clip(lo_expr, hi_expr)


def sector_neutralize(values: pl.Series, sectors: pl.Series) -> pl.Series:
    """Subtract per-sector mean from ``values`` so each sector has mean ~0.

    Eager-only (``Series`` in, ``Series`` out) because cross-group neutralization
    composes poorly with single-expression lazy plans -- the runner is expected
    to materialize a cross-section before calling this. Nulls in ``values`` are
    preserved; rows with null ``sectors`` are treated as a single residual
    bucket.

    Raises:
        ValueError: if the two series have different lengths.
    """

    if values.len() != sectors.len():
        raise ValueError(
            f"sector_neutralize length mismatch: values={values.len()}, "
            f"sectors={sectors.len()}"
        )
    if values.len() == 0:
        return values

    frame = pl.DataFrame(
        {
            "_value": values,
            "_sector": sectors.fill_null("__null__"),
        }
    )
    means = frame.group_by("_sector").agg(pl.col("_value").mean().alias("_sector_mean"))
    joined = frame.with_row_index("_row").join(means, on="_sector", how="left").sort("_row")
    residual = (joined["_value"] - joined["_sector_mean"]).alias(values.name or "")
    return residual


__all__ = ["zscore", "winsorize", "sector_neutralize", "WinsorizeBounds"]
