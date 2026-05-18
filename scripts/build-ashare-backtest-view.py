"""Build a PIT-safe daily A-share backtest feature view.

The view is designed to be passed directly to the research pipeline via
`--pit-parquet`. It uses PIT daily bars plus optional PIT tradability and
adjustment-factor layers. Current finance/block snapshots are intentionally not
joined because they do not have historical visibility windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import read_delta_or_parquet, resolve_delta_or_parquet, scan_delta_or_parquet


TRADABILITY_COLUMNS = (
    "is_st",
    "is_suspended",
    "limit_up",
    "limit_down",
    "listed_days",
    "is_tradable",
    "reason",
)
MARKET_CAP_COLUMNS = (
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
INDUSTRY_COLUMNS = (
    "name",
    "industry",
    "area",
)

LIMIT_REFORM_DATE = date(2020, 8, 24)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare", help="A-share data root")
    parser.add_argument("--out-parquet", help="Output feature parquet")
    parser.add_argument("--start", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date YYYY-MM-DD")
    parser.add_argument("--as-of", help="Optional visibility cutoff ISO date/datetime")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    parser.add_argument("--min-listed-days", type=int, default=21)
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    run_id = _run_id(args.as_of) or datetime.now(tz=UTC).strftime("%Y%m%d")
    out_parquet = (
        Path(args.out_parquet)
        if args.out_parquet
        else data_root / "features" / f"backtest_daily_pit_{run_id}.parquet"
    )
    result = build_backtest_daily_view(
        data_root=data_root,
        out_parquet=out_parquet,
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        as_of=_parse_as_of(args.as_of),
        symbols=tuple(symbol.strip().upper() for symbol in (args.symbols or "").split(",") if symbol.strip()),
        min_listed_days=args.min_listed_days,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote A-share backtest feature view: {out_parquet}")
    return 0


def build_backtest_daily_view(
    *,
    data_root: Path,
    out_parquet: Path,
    start: date | None = None,
    end: date | None = None,
    as_of: datetime | None = None,
    symbols: tuple[str, ...] = (),
    min_listed_days: int = 21,
) -> dict[str, Any]:
    kline_path = data_root / "pit" / "kline_daily_pit.parquet"
    resolved_kline = resolve_delta_or_parquet(kline_path)
    if resolved_kline is None:
        raise SystemExit(f"missing kline PIT parquet: {kline_path}")
    frame = scan_delta_or_parquet(resolved_kline)
    if symbols:
        frame = frame.filter(pl.col("symbol").is_in(list(symbols)))
    if start is not None:
        frame = frame.filter(pl.col("event_time").dt.date() >= start)
    if end is not None:
        frame = frame.filter(pl.col("event_time").dt.date() <= end)
    if as_of is not None:
        frame = frame.filter(pl.col("available_at") <= as_of)
    bars = frame.collect().sort(["symbol", "event_time"])
    if bars.is_empty():
        raise SystemExit("backtest view has no rows after filters")

    sources: dict[str, str] = {"kline_daily_pit": _relative(resolved_kline, data_root)}
    status_path = resolve_delta_or_parquet(data_root / "pit" / "tradability_status_pit.parquet")
    if status_path is not None:
        bars = _merge_tradability(bars, read_delta_or_parquet(status_path))
        sources["tradability_status_pit"] = _relative(status_path, data_root)
    else:
        bars = _with_missing_tradability_columns(bars)

    adjustment_path = resolve_delta_or_parquet(data_root / "pit" / "adjustment_factor_pit.parquet")
    if adjustment_path is not None:
        bars = _merge_adjustment_factor(bars, read_delta_or_parquet(adjustment_path))
        sources["adjustment_factor_pit"] = _relative(adjustment_path, data_root)
    else:
        bars = bars.with_columns(pl.lit(1.0).alias("adjustment_factor"))

    market_cap_path = resolve_delta_or_parquet(data_root / "pit" / "market_cap_daily_pit.parquet")
    if market_cap_path is not None:
        bars = _merge_visible_daily_sidecar(
            bars,
            read_delta_or_parquet(market_cap_path),
            sidecar_name="market_cap",
            payload_columns=MARKET_CAP_COLUMNS,
        )
        sources["market_cap_daily_pit"] = _relative(market_cap_path, data_root)
    else:
        bars = _with_missing_columns(bars, MARKET_CAP_COLUMNS)

    industry_path = resolve_delta_or_parquet(data_root / "pit" / "industry_daily_pit.parquet")
    if industry_path is not None:
        bars = _merge_visible_daily_sidecar(
            bars,
            read_delta_or_parquet(industry_path),
            sidecar_name="industry",
            payload_columns=INDUSTRY_COLUMNS,
        )
        sources["industry_daily_pit"] = _relative(industry_path, data_root)
    else:
        bars = _with_missing_columns(bars, INDUSTRY_COLUMNS)

    view = _finalize_backtest_view(bars, min_listed_days=min_listed_days)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    view.write_parquet(out_parquet)

    manifest = {
        "dataset": "ashare.backtest_daily_features_v1",
        "tier": "features",
        "backtest_ready": True,
        "research_eligible": True,
        "path": _relative(out_parquet, data_root),
        "row_count": view.height,
        "symbol_count": view.select(pl.col("symbol").n_unique()).item(),
        "start": str(view["event_time"].min()),
        "end": str(view["event_time"].max()),
        "as_of": as_of.isoformat() if as_of is not None else None,
        "min_listed_days": min_listed_days,
        "content_hash": _sha256_file(out_parquet),
        "sources": sources,
        "excluded_sidecars": [
            "normalized/tdx/finance_snapshot_*.parquet",
            "normalized/tdx/block_membership_*.parquet",
            "pit/share_float_event_pit.parquet",
        ],
        "honest_gaps": [
            "tradability status is applied only when a PIT status row is visible for that bar; missing historical status defaults are explicit",
            "limit_up/limit_down use visible PIT status when available, otherwise a price-derived limit proxy",
            "adjusted_close follows the current pipeline adjustment-factor convention and does not replace raw execution close",
            "market cap and industry fields are carried only when a visible daily PIT sidecar row exists for that bar",
            "share_float events remain a separate PIT event layer and are not flattened into the daily backtest view",
            "TDX finance/block snapshots are excluded because they are not historical PIT datasets",
        ],
    }
    manifest_path = data_root / "_manifest" / f"{out_parquet.stem}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest | {"manifest_path": str(manifest_path)}


def _merge_tradability(frame: pl.DataFrame, status: pl.DataFrame) -> pl.DataFrame:
    if status.is_empty():
        return _with_missing_tradability_columns(frame)
    wanted = ["symbol", "event_time", "available_at", *TRADABILITY_COLUMNS]
    layer = (
        status.select([column for column in wanted if column in status.columns])
        .sort(["symbol", "event_time"])
        .rename({"available_at": "status_available_at", "event_time": "status_event_time"})
    )
    joined = frame.join_asof(
        layer,
        left_on="event_time",
        right_on="status_event_time",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    if "status_available_at" not in joined.columns:
        return _with_missing_tradability_columns(joined)
    has_status = pl.col("status_available_at").is_not_null() & (
        pl.col("status_available_at") <= pl.col("available_at")
    )
    expressions = []
    for column in TRADABILITY_COLUMNS:
        if column in joined.columns:
            expressions.append(
                pl.when(has_status)
                .then(pl.col(column))
                .otherwise(None)
                .alias(column)
            )
    expressions.append(has_status.alias("has_status_pit"))
    return joined.with_columns(expressions).drop("status_available_at")


def _with_missing_tradability_columns(frame: pl.DataFrame) -> pl.DataFrame:
    expressions = []
    defaults: dict[str, Any] = {
        "is_st": None,
        "is_suspended": None,
        "limit_up": None,
        "limit_down": None,
        "listed_days": None,
        "is_tradable": None,
        "reason": None,
        "has_status_pit": False,
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            expressions.append(pl.lit(value).alias(column))
    return frame.with_columns(expressions) if expressions else frame


def _merge_adjustment_factor(frame: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    if factors.is_empty():
        return frame.with_columns(pl.lit(1.0).alias("adjustment_factor"))
    layer = (
        factors.select([column for column in ("symbol", "event_time", "available_at", "factor") if column in factors.columns])
        .sort(["symbol", "event_time"])
        .rename(
            {
                "event_time": "factor_event_time",
                "available_at": "factor_available_at",
                "factor": "adjustment_factor",
            }
        )
    )
    joined = frame.join_asof(
        layer,
        left_on="event_time",
        right_on="factor_event_time",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    if "adjustment_factor" not in joined.columns:
        return frame.with_columns(pl.lit(1.0).alias("adjustment_factor"))
    return joined.with_columns(
        pl.when(pl.col("factor_available_at") <= pl.col("available_at"))
        .then(pl.col("adjustment_factor"))
        .otherwise(None)
        .fill_null(1.0)
        .alias("adjustment_factor")
    ).drop("factor_available_at")


def _merge_visible_daily_sidecar(
    frame: pl.DataFrame,
    sidecar: pl.DataFrame,
    *,
    sidecar_name: str,
    payload_columns: tuple[str, ...],
) -> pl.DataFrame:
    if sidecar.is_empty():
        return _with_missing_columns(frame, payload_columns)
    wanted = ["symbol", "event_time", "available_at", *payload_columns]
    available = [column for column in wanted if column in sidecar.columns]
    layer = (
        sidecar.select(available)
        .sort(["symbol", "available_at", "event_time"])
        .rename(
            {
                "event_time": f"{sidecar_name}_event_time",
                "available_at": f"{sidecar_name}_available_at",
            }
        )
    )
    joined = frame.join_asof(
        layer,
        left_on="available_at",
        right_on=f"{sidecar_name}_available_at",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    available_at_column = f"{sidecar_name}_available_at"
    if available_at_column not in joined.columns:
        return _with_missing_columns(joined, payload_columns)
    event_time_column = f"{sidecar_name}_event_time"
    has_visible_row = (
        pl.col(available_at_column).is_not_null()
        & (pl.col(available_at_column) <= pl.col("available_at"))
        & pl.col(event_time_column).is_not_null()
        & (pl.col(event_time_column) <= pl.col("event_time"))
    )
    joined = joined.with_columns(
        [
            pl.when(has_visible_row).then(pl.col(column)).otherwise(None).alias(column)
            for column in payload_columns
            if column in joined.columns
        ]
    )
    return joined.drop([column for column in (available_at_column, event_time_column) if column in joined.columns])


def _with_missing_columns(frame: pl.DataFrame, columns: tuple[str, ...]) -> pl.DataFrame:
    missing = [pl.lit(None).alias(column) for column in columns if column not in frame.columns]
    return frame.with_columns(missing) if missing else frame


def _finalize_backtest_view(frame: pl.DataFrame, *, min_listed_days: int) -> pl.DataFrame:
    frame = frame.sort(["symbol", "event_time"]).with_columns(
        pl.col("event_time").dt.date().alias("date"),
        pl.cum_count("event_time").over("symbol").cast(pl.Int64).alias("_observed_listed_days"),
        pl.col("close").shift(1).over("symbol").alias("previous_close"),
    )
    limit_band = _limit_band_expr()
    limit_proxy = (
        frame.with_columns(limit_band.alias("limit_band_pct"))
        .with_columns(
            ((pl.col("close") / pl.col("previous_close")) - 1.0).alias("close_return"),
            (
                pl.col("previous_close").is_not_null()
                & (pl.col("previous_close") > 0)
                & (((pl.col("close") / pl.col("previous_close")) - 1.0) >= (pl.col("limit_band_pct") - 0.002))
            ).alias("limit_up_proxy"),
            (
                pl.col("previous_close").is_not_null()
                & (pl.col("previous_close") > 0)
                & (((pl.col("close") / pl.col("previous_close")) - 1.0) <= (-pl.col("limit_band_pct") + 0.002))
            ).alias("limit_down_proxy"),
        )
    )
    frame = limit_proxy
    frame = frame.with_columns(
        pl.col("is_st").fill_null(False).alias("is_st"),
        pl.col("is_suspended").fill_null(False).alias("is_suspended"),
        (
            pl.col("limit_up").fill_null(False)
            | ((~pl.col("has_status_pit").fill_null(False)) & pl.col("limit_up_proxy"))
        ).alias("limit_up"),
        (
            pl.col("limit_down").fill_null(False)
            | ((~pl.col("has_status_pit").fill_null(False)) & pl.col("limit_down_proxy"))
        ).alias("limit_down"),
        pl.when(pl.col("has_status_pit").fill_null(False))
        .then(pl.lit("status_pit"))
        .otherwise(pl.lit("price_proxy"))
        .alias("limit_status_source"),
        pl.col("is_tradable").fill_null(True).alias("is_tradable"),
        pl.coalesce([pl.col("listed_days"), pl.col("_observed_listed_days")]).cast(pl.Int64).alias("listed_days"),
        pl.col("reason").fill_null("no visible status PIT row; kline-only default").alias("reason"),
        pl.col("close").alias("raw_close"),
        (pl.col("close") * pl.col("adjustment_factor").fill_null(1.0)).alias("adjusted_close"),
    )
    eligible = (
        (~pl.col("is_st"))
        & (~pl.col("is_suspended"))
        & pl.col("is_tradable")
        & (pl.col("listed_days") >= min_listed_days)
        & (pl.col("volume").fill_null(0) > 0)
    )
    frame = frame.with_columns(
        eligible.alias("research_eligible"),
        pl.when(eligible)
        .then(pl.lit("eligible"))
        .when(pl.col("is_st"))
        .then(pl.lit("st_stock"))
        .when(pl.col("is_suspended"))
        .then(pl.lit("suspended"))
        .when(~pl.col("is_tradable"))
        .then(pl.col("reason"))
        .when(pl.col("listed_days") < min_listed_days)
        .then(pl.lit("listed_days_below_minimum"))
        .when(pl.col("volume").fill_null(0) <= 0)
        .then(pl.lit("no_volume"))
        .otherwise(pl.lit("not eligible"))
        .alias("eligibility_reason"),
    )
    columns = [
        "symbol",
        "market",
        "date",
        "event_time",
        "available_at",
        "source_updated_at",
        "open",
        "high",
        "low",
        "close",
        "raw_close",
        "adjusted_close",
        "adjustment_factor",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
        "name",
        "industry",
        "area",
        "previous_close",
        "close_return",
        "limit_band_pct",
        "volume",
        "amount",
        "is_st",
        "is_suspended",
        "limit_up",
        "limit_down",
        "limit_up_proxy",
        "limit_down_proxy",
        "limit_status_source",
        "listed_days",
        "is_tradable",
        "reason",
        "research_eligible",
        "eligibility_reason",
    ]
    return frame.select([column for column in columns if column in frame.columns])


def _limit_band_expr() -> pl.Expr:
    symbol = pl.col("symbol").cast(pl.Utf8)
    is_chinext = symbol.str.starts_with("300") | symbol.str.starts_with("301")
    is_star = symbol.str.starts_with("688")
    is_bj = (
        symbol.str.starts_with("430")
        | symbol.str.starts_with("83")
        | symbol.str.starts_with("87")
        | symbol.str.starts_with("920")
    )
    return (
        pl.when(is_bj)
        .then(pl.lit(0.30))
        .when(is_star | (is_chinext & (pl.col("event_time").dt.date() >= LIMIT_REFORM_DATE)))
        .then(pl.lit(0.20))
        .otherwise(pl.lit(0.10))
    )


def _parse_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _parse_as_of(raw: str | None) -> datetime | None:
    if not raw:
        return None
    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw + "T23:59:59+00:00")
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _run_id(as_of: str | None) -> str | None:
    if not as_of:
        return None
    return as_of[:10].replace("-", "")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
