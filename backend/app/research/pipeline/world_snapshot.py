"""Build a PIT-safe A-share world snapshot from kline lake inputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from .data_lake import resolve_kline_daily_parquet, resolve_tradability_status_parquet


@dataclass(frozen=True)
class WorldSnapshotResult:
    snapshot_id: str
    out_dir: Path
    parquet_path: Path
    manifest_path: Path
    state_path: Path
    frame: pl.DataFrame
    market_state: dict[str, Any]


def build_world_snapshot(
    *,
    data_root: Path,
    as_of_date: date,
    out_dir: Path,
    symbols: tuple[str, ...] = (),
) -> WorldSnapshotResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = resolve_kline_daily_parquet(data_root, symbols)
    frame = _normalize_input(pl.read_parquet(source_path), symbols)
    as_of = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    visible = frame.filter(pl.col("available_at") <= as_of)
    snapshot = _latest_rows(visible, as_of=as_of)
    status_path = resolve_tradability_status_parquet(data_root)
    if status_path is not None:
        status = _latest_status_rows(pl.read_parquet(status_path), as_of=as_of, symbols=symbols)
        snapshot = _merge_tradability_status(snapshot, status)

    market_state = summarize_market_state(visible, snapshot, as_of=as_of)
    snapshot = snapshot.with_columns(
        pl.lit(str(market_state["regime"])).alias("market_state"),
        pl.lit(float(market_state["risk_score"])).alias("risk_score"),
    )
    snapshot_id = f"ashare.world_snapshot_v1:{as_of_date.isoformat()}"
    parquet_path = out_dir / "world_snapshot.parquet"
    snapshot.write_parquet(parquet_path)
    state_path = out_dir / "market_state.json"
    state_path.write_text(json.dumps(market_state, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    manifest = {
        "dataset": "ashare.world_snapshot_v1",
        "snapshot_id": snapshot_id,
        "as_of": as_of.isoformat(),
        "source": str(source_path),
        "tradability_status_source": str(status_path) if status_path is not None else "",
        "rows": snapshot.height,
        "symbols": sorted(snapshot["symbol"].to_list()) if snapshot.height else [],
        "parquet": parquet_path.name,
        "market_state_path": state_path.name,
        "regime": market_state["regime"],
        "risk_score": market_state["risk_score"],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return WorldSnapshotResult(snapshot_id, out_dir, parquet_path, manifest_path, state_path, snapshot, market_state)


def summarize_market_state(frame: pl.DataFrame, snapshot: pl.DataFrame, *, as_of: datetime) -> dict[str, Any]:
    if snapshot.is_empty():
        return {
            "as_of": as_of.isoformat(),
            "regime": "risk_off",
            "risk_score": 1.0,
            "position_scale": 0.0,
            "entry_threshold": 0.03,
            "exit_threshold": -0.01,
            "breadth_up_ratio": 0.0,
            "tradable_ratio": 0.0,
            "research_eligible_ratio": 0.0,
            "limit_hit_ratio": 0.0,
            "median_volatility_20d": 0.0,
            "median_amount": 0.0,
            "visible_symbol_count": 0,
            "eligible_universe_count": 0,
            "suggested_rebalance_mode": "weekly",
        }

    breadth_up_ratio = _breadth_up_ratio(frame)
    median_volatility = _median_volatility_20d(frame)
    limit_hit_ratio = _boolean_ratio(snapshot, ("limit_up", "limit_down"))
    tradable_ratio = _column_ratio(snapshot, "is_tradable", default=True)
    eligible_ratio = _column_ratio(snapshot, "research_eligible", default=True)
    median_amount = float(snapshot["amount"].median()) if "amount" in snapshot.columns and snapshot.height else 0.0
    raw_risk = (
        (1.0 - breadth_up_ratio) * 0.45
        + min(median_volatility / 0.08, 1.0) * 0.25
        + limit_hit_ratio * 0.15
        + (1.0 - tradable_ratio) * 0.15
    )
    risk_score = float(min(max(raw_risk, 0.0), 1.0))
    regime, position_scale, entry_threshold, exit_threshold = _regime_policy(
        risk_score=risk_score,
        breadth_up_ratio=breadth_up_ratio,
        eligible_ratio=eligible_ratio,
    )
    return {
        "as_of": as_of.isoformat(),
        "regime": regime,
        "risk_score": risk_score,
        "position_scale": position_scale,
        "entry_threshold": entry_threshold,
        "exit_threshold": exit_threshold,
        "breadth_up_ratio": breadth_up_ratio,
        "tradable_ratio": tradable_ratio,
        "research_eligible_ratio": eligible_ratio,
        "limit_hit_ratio": limit_hit_ratio,
        "median_volatility_20d": median_volatility,
        "median_amount": median_amount,
        "visible_symbol_count": int(snapshot.height),
        "eligible_universe_count": int(snapshot.filter(pl.col("research_eligible")).height),
        "suggested_rebalance_mode": "weekly",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Snapshot date in YYYY-MM-DD format")
    parser.add_argument("--data-root", required=True, help="A-share lake root")
    parser.add_argument("--out", required=True, help="Snapshot output directory")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    args = parser.parse_args(argv)

    build_world_snapshot(
        data_root=Path(args.data_root),
        as_of_date=date.fromisoformat(args.date),
        out_dir=Path(args.out),
        symbols=tuple(symbol.strip() for symbol in (args.symbols or "").split(",") if symbol.strip()),
    )
    return 0


def _normalize_input(frame: pl.DataFrame, symbols: tuple[str, ...]) -> pl.DataFrame:
    columns = set(frame.columns)
    if {"symbol", "event_time", "available_at", "source_updated_at"}.issubset(columns):
        normalized = frame
    elif {"code", "market", "date", "open", "high", "low", "close", "volume", "amount"}.issubset(columns):
        normalized = _normalize_legacy(frame)
    else:
        raise ValueError("world snapshot requires PIT kline or legacy daily kline columns")
    if symbols:
        normalized = normalized.filter(pl.col("symbol").is_in(list(symbols)))
    return normalized.sort(["symbol", "event_time"])


def _normalize_legacy(frame: pl.DataFrame) -> pl.DataFrame:
    market_expr = (
        pl.when(pl.col("market").cast(pl.Utf8).str.to_lowercase().is_in(["1", "sh"]))
        .then(pl.lit("SH"))
        .otherwise(pl.lit("SZ"))
    )
    event_time = pl.datetime(
        pl.col("date").cast(pl.Date).dt.year(),
        pl.col("date").cast(pl.Date).dt.month(),
        pl.col("date").cast(pl.Date).dt.day(),
        time_zone="UTC",
    )
    return (
        frame.with_columns(
            pl.col("code").cast(pl.Utf8).str.zfill(6).alias("_code"),
            market_expr.alias("market"),
            pl.col("date").cast(pl.Date).alias("date"),
            pl.col("open").cast(pl.Float64).alias("open"),
            pl.col("high").cast(pl.Float64).alias("high"),
            pl.col("low").cast(pl.Float64).alias("low"),
            pl.col("close").cast(pl.Float64).alias("close"),
            pl.col("volume").cast(pl.Int64).alias("volume"),
            pl.col("amount").cast(pl.Float64).alias("amount"),
        )
        .with_columns(
            (pl.col("_code") + pl.lit(".") + pl.col("market")).alias("symbol"),
            event_time.alias("event_time"),
        )
        .with_columns(
            (pl.col("event_time") + pl.duration(hours=23, minutes=59, seconds=59)).alias("available_at"),
        )
        .with_columns(pl.col("available_at").alias("source_updated_at"))
        .select(
            "symbol",
            "market",
            "event_time",
            "available_at",
            "source_updated_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        )
        .sort(["symbol", "event_time"])
    )


def _latest_rows(frame: pl.DataFrame, *, as_of: datetime) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8,
        "market": pl.Utf8,
        "industry": pl.Utf8,
        "as_of": pl.Datetime(time_zone="UTC"),
        "latest_event_time": pl.Datetime(time_zone="UTC"),
        "available_at": pl.Datetime(time_zone="UTC"),
        "source_updated_at": pl.Datetime(time_zone="UTC"),
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "amount": pl.Float64,
        "visible_rows": pl.UInt32,
        "is_st": pl.Boolean,
        "is_suspended": pl.Boolean,
        "limit_up": pl.Boolean,
        "limit_down": pl.Boolean,
        "listed_days": pl.Int64,
        "is_tradable": pl.Boolean,
        "research_eligible": pl.Boolean,
        "tradable_reason": pl.Utf8,
        "research_eligible_reason": pl.Utf8,
        "reason": pl.Utf8,
        "market_state": pl.Utf8,
        "risk_score": pl.Float64,
    }
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    counts = frame.group_by("symbol").len().rename({"len": "visible_rows"})
    latest = frame.sort(["symbol", "event_time"]).group_by("symbol", maintain_order=True).last()
    tradable_reason = (
        pl.when(pl.col("is_tradable").fill_null(True))
        .then(pl.lit("visible PIT kline row"))
        .otherwise(pl.col("reason").fill_null("not tradable"))
        if "is_tradable" in latest.columns
        else pl.lit("tradability status missing; kline visibility only")
    )
    return (
        latest.join(counts, on="symbol", how="left")
        .with_columns(
            pl.lit(as_of).alias("as_of"),
            pl.col("event_time").alias("latest_event_time"),
            pl.col("symbol").alias("industry"),
            pl.col("is_st").fill_null(False) if "is_st" in latest.columns else pl.lit(False).alias("is_st"),
            pl.col("is_suspended").fill_null(False)
            if "is_suspended" in latest.columns
            else pl.lit(False).alias("is_suspended"),
            pl.col("limit_up").fill_null(False) if "limit_up" in latest.columns else pl.lit(False).alias("limit_up"),
            pl.col("limit_down").fill_null(False)
            if "limit_down" in latest.columns
            else pl.lit(False).alias("limit_down"),
            pl.col("listed_days").fill_null(-1).cast(pl.Int64)
            if "listed_days" in latest.columns
            else pl.lit(-1).alias("listed_days"),
            pl.col("is_tradable").fill_null(True)
            if "is_tradable" in latest.columns
            else pl.lit(True).alias("is_tradable"),
            tradable_reason.alias("tradable_reason"),
            tradable_reason.alias("research_eligible_reason"),
            pl.col("is_tradable").fill_null(True).alias("research_eligible")
            if "is_tradable" in latest.columns
            else pl.lit(True).alias("research_eligible"),
            pl.lit("visible PIT kline row").alias("reason"),
            pl.lit("").alias("market_state"),
            pl.lit(0.0).alias("risk_score"),
        )
        .select(list(schema))
        .sort("symbol")
    )


def _latest_status_rows(frame: pl.DataFrame, *, as_of: datetime, symbols: tuple[str, ...]) -> pl.DataFrame:
    if symbols and "symbol" in frame.columns:
        frame = frame.filter(pl.col("symbol").is_in(list(symbols)))
    visible = frame.filter(pl.col("available_at") <= as_of)
    if visible.is_empty():
        return visible
    return visible.sort(["symbol", "event_time"]).group_by("symbol", maintain_order=True).last()


def _merge_tradability_status(snapshot: pl.DataFrame, status: pl.DataFrame) -> pl.DataFrame:
    if snapshot.is_empty():
        return snapshot
    if status.is_empty():
        return snapshot.with_columns(
            pl.lit(False).alias("research_eligible"),
            pl.lit("missing tradability status").alias("tradable_reason"),
            pl.lit("missing tradability status").alias("research_eligible_reason"),
        )
    status_cols = [
        "symbol",
        "is_st",
        "is_suspended",
        "limit_up",
        "limit_down",
        "listed_days",
        "is_tradable",
        "reason",
    ]
    joined = snapshot.drop([col for col in status_cols[1:] if col in snapshot.columns]).join(
        status.select([col for col in status_cols if col in status.columns]),
        on="symbol",
        how="left",
    )
    has_status = pl.col("is_tradable").is_not_null()
    reason_expr = (
        pl.when(has_status & pl.col("is_tradable"))
        .then(pl.lit("tradable"))
        .when(has_status)
        .then(pl.col("reason").fill_null("not tradable"))
        .otherwise(pl.lit("missing tradability status"))
    )
    return joined.with_columns(
        pl.col("is_st").fill_null(False),
        pl.col("is_suspended").fill_null(False),
        pl.col("limit_up").fill_null(False),
        pl.col("limit_down").fill_null(False),
        pl.col("listed_days").fill_null(-1).cast(pl.Int64),
        pl.col("is_tradable").fill_null(False),
        reason_expr.alias("tradable_reason"),
        reason_expr.alias("research_eligible_reason"),
        (has_status & pl.col("is_tradable").fill_null(False)).alias("research_eligible"),
    )


def _breadth_up_ratio(frame: pl.DataFrame) -> float:
    required = {"symbol", "event_time", "close"}
    if frame.is_empty() or not required.issubset(frame.columns):
        return 0.0
    pairs = (
        frame.sort(["symbol", "event_time"])
        .group_by("symbol", maintain_order=True)
        .tail(2)
        .group_by("symbol")
        .agg(
            pl.col("close").last().alias("latest_close"),
            pl.col("close").first().alias("previous_close"),
        )
    )
    if pairs.is_empty():
        return 0.0
    value = pairs.select((pl.col("latest_close") > pl.col("previous_close")).cast(pl.Float64).mean()).item()
    return float(value or 0.0)


def _median_volatility_20d(frame: pl.DataFrame) -> float:
    required = {"symbol", "event_time", "close"}
    if frame.is_empty() or not required.issubset(frame.columns):
        return 0.0
    volatility = (
        frame.sort(["symbol", "event_time"])
        .with_columns(pl.col("close").pct_change().rolling_std(20).over("symbol").alias("volatility_20d"))
        .group_by("symbol", maintain_order=True)
        .last()
    )
    series = volatility["volatility_20d"].drop_nulls()
    return float(series.median()) if series.len() else 0.0


def _boolean_ratio(frame: pl.DataFrame, columns: tuple[str, ...]) -> float:
    if frame.is_empty():
        return 0.0
    available = [column for column in columns if column in frame.columns]
    if not available:
        return 0.0
    expr = None
    for column in available:
        current = pl.col(column).fill_null(False)
        expr = current if expr is None else (expr | current)
    assert expr is not None
    value = frame.select(expr.cast(pl.Float64).mean()).item()
    return float(value or 0.0)


def _column_ratio(frame: pl.DataFrame, column: str, *, default: bool) -> float:
    if frame.is_empty():
        return 0.0
    if column not in frame.columns:
        return 1.0 if default else 0.0
    value = frame.select(pl.col(column).fill_null(default).cast(pl.Float64).mean()).item()
    return float(value or 0.0)


def _regime_policy(*, risk_score: float, breadth_up_ratio: float, eligible_ratio: float) -> tuple[str, float, float, float]:
    if risk_score >= 0.65 or breadth_up_ratio < 0.40 or eligible_ratio < 0.70:
        return "risk_off", 0.35, 0.03, -0.01
    if risk_score <= 0.35 and breadth_up_ratio >= 0.55 and eligible_ratio >= 0.85:
        return "risk_on", 1.0, 0.01, -0.03
    return "neutral", 0.65, 0.015, -0.02


if __name__ == "__main__":
    raise SystemExit(main())
