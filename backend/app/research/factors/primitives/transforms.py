"""Cross-sectional transform primitives for factor construction.

Pure functions that operate on either a ``pl.Series`` (eager) or a ``pl.Expr``
(composable into the lazy pipeline used by ``pipeline/runner.py``). All
transforms preserve null entries and never mutate inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

import polars as pl

ValuesLike = Union[pl.Series, pl.Expr]


@dataclass(frozen=True)
class WinsorizeBounds:
    """Quantile bounds used by :func:`winsorize`."""

    lower: float
    upper: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.lower < self.upper <= 1.0:
            raise ValueError(
                f"winsorize bounds must satisfy 0 <= lower < upper <= 1; "
                f"got lower={self.lower}, upper={self.upper}"
            )


def zscore(values: ValuesLike) -> ValuesLike:
    """Cross-sectional z-score: ``(x - mean(x)) / std(x)``."""

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
    """Clip ``values`` to the [lower, upper] quantile range, cross-sectionally."""

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
    """Subtract per-sector mean from ``values`` so each sector has mean ~0."""

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
