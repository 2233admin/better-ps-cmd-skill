"""Unit tests for app.research.factors.transforms."""

from __future__ import annotations

import math

import polars as pl
import pytest

from app.research.factors.transforms import (
    WinsorizeBounds,
    sector_neutralize,
    winsorize,
    zscore,
)


def _stats(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


class TestZscore:
    def test_series_mean_zero_std_one(self) -> None:
        s = pl.Series("x", [1.0, 2.0, 3.0, 4.0, 5.0])
        out = zscore(s)
        mean, std = _stats(out.to_list())
        assert abs(mean) < 1e-9
        assert abs(std - 1.0) < 1e-9

    def test_series_preserves_nulls(self) -> None:
        s = pl.Series("x", [1.0, None, 3.0, None, 5.0])
        out = zscore(s)
        # Nulls preserved at same positions
        nulls = [v is None for v in out.to_list()]
        assert nulls == [False, True, False, True, False]
        non_null = [v for v in out.to_list() if v is not None]
        mean, std = _stats(non_null)
        assert abs(mean) < 1e-9
        assert abs(std - 1.0) < 1e-9

    def test_series_constant_returns_zeros(self) -> None:
        s = pl.Series("x", [7.0, 7.0, 7.0])
        out = zscore(s)
        assert out.to_list() == [0.0, 0.0, 0.0]

    def test_expr_in_select_matches_series_path(self) -> None:
        frame = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0]})
        expr_out = frame.select(zscore(pl.col("x")).alias("z"))["z"].to_list()
        series_out = zscore(pl.Series("x", [1.0, 2.0, 3.0, 4.0, 5.0])).to_list()
        for a, b in zip(expr_out, series_out, strict=True):
            assert abs(a - b) < 1e-9


class TestWinsorize:
    def test_clips_outliers(self) -> None:
        s = pl.Series("x", [-100.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
        out = winsorize(s, lower=0.1, upper=0.9)
        values = out.to_list()
        # -100 must be clipped up, 100 must be clipped down
        assert min(values) > -100.0
        assert max(values) < 100.0
        # interior order untouched
        assert values[1:6] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_no_op_when_within_bounds(self) -> None:
        s = pl.Series("x", [1.0, 2.0, 3.0, 4.0, 5.0])
        out = winsorize(s, lower=0.0, upper=1.0)
        assert out.to_list() == s.to_list()

    def test_preserves_nulls(self) -> None:
        s = pl.Series("x", [None, 1.0, 2.0, 3.0, None, 100.0])
        out = winsorize(s, lower=0.0, upper=0.9)
        positions = [v is None for v in out.to_list()]
        assert positions == [True, False, False, False, True, False]

    def test_expr_path(self) -> None:
        frame = pl.DataFrame({"x": [-50.0, 1.0, 2.0, 3.0, 50.0]})
        out = frame.select(winsorize(pl.col("x"), 0.2, 0.8).alias("w"))["w"].to_list()
        assert min(out) > -50.0
        assert max(out) < 50.0

    def test_invalid_bounds_raise(self) -> None:
        with pytest.raises(ValueError):
            WinsorizeBounds(lower=0.9, upper=0.1)
        with pytest.raises(ValueError):
            WinsorizeBounds(lower=-0.1, upper=0.9)


class TestSectorNeutralize:
    def test_each_group_mean_zero(self) -> None:
        values = pl.Series("x", [1.0, 3.0, 10.0, 20.0])
        sectors = pl.Series("s", ["A", "A", "B", "B"])
        out = sector_neutralize(values, sectors)
        result = out.to_list()
        # A: [1,3] mean 2 -> [-1, 1]; B: [10,20] mean 15 -> [-5, 5]
        assert result == [-1.0, 1.0, -5.0, 5.0]

    def test_single_sector_zero_mean(self) -> None:
        values = pl.Series("x", [1.0, 2.0, 3.0, 4.0])
        sectors = pl.Series("s", ["X", "X", "X", "X"])
        out = sector_neutralize(values, sectors).to_list()
        assert abs(sum(out) / len(out)) < 1e-12

    def test_null_sector_grouped_together(self) -> None:
        values = pl.Series("x", [5.0, 7.0, 100.0])
        sectors = pl.Series("s", [None, None, "B"])
        out = sector_neutralize(values, sectors).to_list()
        # Nulls form their own sector with mean 6 -> [-1, 1]; B singleton -> 0
        assert out == [-1.0, 1.0, 0.0]

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            sector_neutralize(pl.Series("x", [1.0, 2.0]), pl.Series("s", ["A"]))

    def test_empty_input_returns_empty(self) -> None:
        values = pl.Series("x", [], dtype=pl.Float64)
        sectors = pl.Series("s", [], dtype=pl.Utf8)
        out = sector_neutralize(values, sectors)
        assert out.len() == 0
