"""Build factor sidecar PIT datasets from ChinaData/Tushare.

Produces canonical Delta PIT sidecars:
- pit/market_cap_daily_pit from daily_basic
- pit/industry_daily_pit from bak_daily
- pit/share_float_event_pit from share_float
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd
import polars as pl

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.data.delta_lake import (
    canonical_delta_path,
    dataset_stats,
    merge_polars_delta,
    resolve_delta_or_parquet,
    scan_delta_or_parquet,
    write_polars_delta,
)

TOOL_DATA_DIR = Path(__file__).resolve().parents[1] / "backend" / "tools" / "data"
if str(TOOL_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DATA_DIR))

from tushare_client import fetch_tushare_dataframe, resolve_token  # noqa: E402


DEFAULT_START = date(2016, 1, 1)
SYNC_STRATEGY_VERSION = 2
SHARE_FLOAT_LIMIT = 6000
SHARE_FLOAT_OVERLAP_DAYS = 31


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="DATA/Ashare")
    parser.add_argument("--start", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date YYYY-MM-DD")
    parser.add_argument("--symbols", help="Optional comma-separated symbol subset")
    parser.add_argument("--token")
    parser.add_argument("--pause-seconds", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-market-cap", action="store_true")
    parser.add_argument("--skip-industry", action="store_true")
    parser.add_argument("--skip-share-float", action="store_true")
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = build_chinadata_factor_pit(
        data_root=Path(args.data_root),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        symbols=tuple(part.strip().upper() for part in (args.symbols or "").split(",") if part.strip()),
        token=args.token,
        pause_seconds=args.pause_seconds,
        workers=max(1, args.workers),
        build_market_cap=not args.skip_market_cap,
        build_industry=not args.skip_industry,
        build_share_float=not args.skip_share_float,
        full_refresh=args.full_refresh,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(f"wrote chinadata factor PIT datasets under {args.data_root}")
    return 0


def build_chinadata_factor_pit(
    *,
    data_root: Path,
    start: date | None = None,
    end: date | None = None,
    symbols: tuple[str, ...] = (),
    token: str | None = None,
    pause_seconds: float = 0.05,
    workers: int = 4,
    build_market_cap: bool = True,
    build_industry: bool = True,
    build_share_float: bool = True,
    full_refresh: bool = False,
) -> dict[str, Any]:
    kline_path = data_root / "pit" / "kline_daily_pit.parquet"
    if not kline_path.exists():
        raise SystemExit(f"missing kline PIT parquet: {kline_path}")
    (data_root / "pit").mkdir(parents=True, exist_ok=True)
    (data_root / "_manifest").mkdir(parents=True, exist_ok=True)
    start_date, end_date, symbol_count = _resolve_base_window(kline_path, start=start, end=end)
    trade_dates = _observed_trade_dates(kline_path, start=start_date, end=end_date)
    resolved_token = token or resolve_token()
    manifest_root = data_root / "_manifest"
    gap_report_path = manifest_root / "chinadata_factor_gap_report.json"

    if not build_market_cap and not build_industry and not build_share_float:
        raise SystemExit("all datasets were skipped")

    manifests: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="chinadata_factor_sync_", dir=str(data_root)) as temp_dir:
        temp_root = Path(temp_dir)
        if build_market_cap:
            manifests["market_cap_daily"] = _build_market_cap_dataset(
                data_root=data_root,
                start=start_date,
                end=end_date,
                trade_dates=trade_dates,
                symbols=symbols,
                token=resolved_token,
                pause_seconds=pause_seconds,
                workers=workers,
                temp_root=temp_root / "market_cap",
                full_refresh=full_refresh,
            )
        if build_industry:
            manifests["industry_daily"] = _build_industry_dataset(
                data_root=data_root,
                start=start_date,
                end=end_date,
                trade_dates=trade_dates,
                symbols=symbols,
                token=resolved_token,
                pause_seconds=pause_seconds,
                workers=workers,
                temp_root=temp_root / "industry",
                full_refresh=full_refresh,
            )
        if build_share_float:
            manifests["share_float_event"] = _build_share_float_dataset(
                data_root=data_root,
                start=start_date,
                end=end_date,
                symbols=symbols,
                token=resolved_token,
                pause_seconds=pause_seconds,
                workers=workers,
                temp_root=temp_root / "share_float",
                full_refresh=full_refresh,
            )

    manifest_path = data_root / "_manifest" / "chinadata_factor_sidecars.json"
    gap_report = _build_factor_gap_report(
        data_root=data_root,
        start=start_date,
        end=end_date,
        trade_dates=trade_dates,
        manifests=manifests,
    )
    gap_report_path.write_text(json.dumps(gap_report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    summary = {
        "dataset": "ashare.chinadata_factor_sidecars_v2",
        "sync_strategy_version": SYNC_STRATEGY_VERSION,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "requested_symbol_count": len(symbols) if symbols else symbol_count,
        "manifests": manifests,
        "gap_report_path": str(gap_report_path),
    }
    manifest_path.write_text(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    summary["manifest_path"] = str(manifest_path)
    return summary


def _build_market_cap_dataset(
    *,
    data_root: Path,
    start: date,
    end: date,
    trade_dates: tuple[date, ...],
    symbols: tuple[str, ...],
    token: str,
    pause_seconds: float,
    workers: int,
    temp_root: Path,
    full_refresh: bool,
) -> dict[str, Any]:
    fields = ",".join(
        [
            "ts_code",
            "trade_date",
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
        ]
    )
    parquet_path = data_root / "pit" / "market_cap_daily_pit.parquet"
    out_path = canonical_delta_path(parquet_path)
    manifest_path = data_root / "_manifest" / "market_cap_daily_pit.json"
    prior_manifest = _read_json_object(manifest_path)
    sync_mode = _resolve_sync_mode(prior_manifest, full_refresh=full_refresh)
    planned_trade_dates = trade_dates if sync_mode != "incremental" else _missing_trade_dates(out_path, trade_dates)
    fetch_count = 0
    wrote_batch = False
    for batch_index, trade_date_batch in enumerate(_trade_date_batches(planned_trade_dates)):
        batch_root = temp_root / f"batch-{batch_index:04d}"
        batch_count = _fetch_trade_date_chunks(
            api_name="daily_basic",
            trade_dates=trade_date_batch,
            fields=fields,
            token=token,
            pause_seconds=pause_seconds,
            workers=workers,
            temp_root=batch_root,
            normalizer=_normalize_market_cap_chunk,
            symbols=symbols,
        )
        fetch_count += batch_count
        if not batch_count:
            continue
        materialized_path = temp_root / f"market_cap_daily_pit_{batch_index:04d}.parquet"
        _materialize_chunk_dir(batch_root, materialized_path, partition_columns=("symbol", "event_time"))
        _write_or_merge_delta(
            out_path=out_path,
            frame=pl.read_parquet(materialized_path),
            full_refresh=sync_mode != "incremental" and not wrote_batch,
            partition_by=["trade_year", "trade_month"],
            keys=["symbol", "event_time"],
        )
        wrote_batch = True
        _write_dataset_progress_manifest(
            path=out_path,
            manifest_path=manifest_path,
            data_root=data_root,
            dataset="ashare.market_cap_daily_pit",
            tier="pit",
            extra={
                "fetch_chunk_count": fetch_count,
                "source": "chinadata:daily_basic",
                "sync_strategy_version": SYNC_STRATEGY_VERSION,
                "sync_mode": sync_mode,
                "planned_trade_date_count": len(planned_trade_dates),
                "progress_trade_date_count": len(_existing_trade_dates(out_path, start=start, end=end)),
                "honest_gaps": [
                    "available_at is set to the next session open proxy because daily_basic is a daily close-derived dataset",
                ],
            },
        )
    if parquet_path.exists():
        parquet_path.unlink()
    manifest = _dataset_manifest(
        out_path,
        data_root=data_root,
        dataset="ashare.market_cap_daily_pit",
        tier="pit",
        extra={
            "fetch_chunk_count": fetch_count,
            "source": "chinadata:daily_basic",
            "sync_strategy_version": SYNC_STRATEGY_VERSION,
            "sync_mode": sync_mode,
            "planned_trade_date_count": len(planned_trade_dates),
            "missing_trade_date_count": max(0, len(trade_dates) - len(_existing_trade_dates(out_path, start=start, end=end))),
            "honest_gaps": [
                "available_at is set to the next session open proxy because daily_basic is a daily close-derived dataset",
            ],
        },
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return manifest | {"manifest_path": str(manifest_path)}


def _build_industry_dataset(
    *,
    data_root: Path,
    start: date,
    end: date,
    trade_dates: tuple[date, ...],
    symbols: tuple[str, ...],
    token: str,
    pause_seconds: float,
    workers: int,
    temp_root: Path,
    full_refresh: bool,
) -> dict[str, Any]:
    fields = ",".join(["ts_code", "trade_date", "name", "industry", "area"])
    parquet_path = data_root / "pit" / "industry_daily_pit.parquet"
    out_path = canonical_delta_path(parquet_path)
    manifest_path = data_root / "_manifest" / "industry_daily_pit.json"
    prior_manifest = _read_json_object(manifest_path)
    sync_mode = _resolve_sync_mode(prior_manifest, full_refresh=full_refresh)
    planned_trade_dates = trade_dates if sync_mode != "incremental" else _missing_trade_dates(out_path, trade_dates)
    fetch_count = 0
    wrote_batch = False
    for batch_index, trade_date_batch in enumerate(_trade_date_batches(planned_trade_dates)):
        batch_root = temp_root / f"batch-{batch_index:04d}"
        batch_count = _fetch_trade_date_chunks(
            api_name="bak_daily",
            trade_dates=trade_date_batch,
            fields=fields,
            token=token,
            pause_seconds=pause_seconds,
            workers=workers,
            temp_root=batch_root,
            normalizer=_normalize_industry_chunk,
            symbols=symbols,
        )
        fetch_count += batch_count
        if not batch_count:
            continue
        materialized_path = temp_root / f"industry_daily_pit_{batch_index:04d}.parquet"
        _materialize_chunk_dir(batch_root, materialized_path, partition_columns=("symbol", "event_time"))
        _write_or_merge_delta(
            out_path=out_path,
            frame=pl.read_parquet(materialized_path),
            full_refresh=sync_mode != "incremental" and not wrote_batch,
            partition_by=["trade_year", "trade_month"],
            keys=["symbol", "event_time"],
        )
        wrote_batch = True
        _write_dataset_progress_manifest(
            path=out_path,
            manifest_path=manifest_path,
            data_root=data_root,
            dataset="ashare.industry_daily_pit",
            tier="pit",
            extra={
                "fetch_chunk_count": fetch_count,
                "source": "chinadata:bak_daily",
                "sync_strategy_version": SYNC_STRATEGY_VERSION,
                "sync_mode": sync_mode,
                "planned_trade_date_count": len(planned_trade_dates),
                "progress_trade_date_count": len(_existing_trade_dates(out_path, start=start, end=end)),
                "honest_gaps": [
                    f"industry coverage is bounded by upstream availability; this sync starts at {start.isoformat()}",
                    "block/theme membership is still separate from the daily industry classification layer",
                ],
            },
        )
    if parquet_path.exists():
        parquet_path.unlink()
    manifest = _dataset_manifest(
        out_path,
        data_root=data_root,
        dataset="ashare.industry_daily_pit",
        tier="pit",
        extra={
            "fetch_chunk_count": fetch_count,
            "source": "chinadata:bak_daily",
            "sync_strategy_version": SYNC_STRATEGY_VERSION,
            "sync_mode": sync_mode,
            "planned_trade_date_count": len(planned_trade_dates),
            "missing_trade_date_count": max(0, len(trade_dates) - len(_existing_trade_dates(out_path, start=start, end=end))),
            "honest_gaps": [
                f"industry coverage is bounded by upstream availability; this sync starts at {start.isoformat()}",
                "block/theme membership is still separate from the daily industry classification layer",
            ],
        },
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return manifest | {"manifest_path": str(manifest_path)}


def _build_share_float_dataset(
    *,
    data_root: Path,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    token: str,
    pause_seconds: float,
    workers: int,
    temp_root: Path,
    full_refresh: bool,
) -> dict[str, Any]:
    fields = ",".join(["ts_code", "ann_date", "float_date", "float_share", "float_ratio", "holder_name", "share_type"])
    parquet_path = data_root / "pit" / "share_float_event_pit.parquet"
    out_path = canonical_delta_path(parquet_path)
    manifest_path = data_root / "_manifest" / "share_float_event_pit.json"
    prior_manifest = _read_json_object(manifest_path)
    sync_mode = _resolve_sync_mode(prior_manifest, full_refresh=full_refresh)
    effective_start = start
    if sync_mode == "incremental":
        last_ann_date = _existing_max_date(out_path, "ann_date")
        if last_ann_date is not None:
            effective_start = max(start, last_ann_date - timedelta(days=SHARE_FLOAT_OVERLAP_DAYS))
    fetch_count = 0
    wrote_batch = False
    for batch_index, (batch_start, batch_end) in enumerate(_month_ranges(effective_start, end)):
        batch_root = temp_root / f"batch-{batch_index:04d}"
        batch_count = _fetch_share_float_ranges(
            start=batch_start,
            end=batch_end,
            fields=fields,
            token=token,
            pause_seconds=pause_seconds,
            workers=workers,
            temp_root=batch_root,
            normalizer=_normalize_share_float_chunk,
            symbols=symbols,
        )
        fetch_count += batch_count
        if not batch_count:
            continue
        materialized_path = temp_root / f"share_float_event_pit_{batch_index:04d}.parquet"
        _materialize_chunk_dir(
            batch_root,
            materialized_path,
            partition_columns=("symbol", "event_time", "ann_date", "holder_name", "share_type"),
        )
        _write_or_merge_delta(
            out_path=out_path,
            frame=pl.read_parquet(materialized_path),
            full_refresh=sync_mode != "incremental" and not wrote_batch,
            partition_by=["ann_year", "ann_month"],
            keys=["symbol", "event_time", "ann_date", "holder_name", "share_type"],
        )
        wrote_batch = True
        _write_dataset_progress_manifest(
            path=out_path,
            manifest_path=manifest_path,
            data_root=data_root,
            dataset="ashare.share_float_event_pit",
            tier="pit",
            extra={
                "fetch_chunk_count": fetch_count,
                "source": "chinadata:share_float",
                "sync_strategy_version": SYNC_STRATEGY_VERSION,
                "sync_mode": sync_mode,
                "requested_start": start.isoformat(),
                "effective_start": effective_start.isoformat(),
                "progress_ann_date_end": batch_end.isoformat(),
                "honest_gaps": [
                    "available_at uses ann_date plus next-session-open proxy because the feed exposes announcement date but not timestamp",
                ],
            },
        )
    if parquet_path.exists():
        parquet_path.unlink()
    manifest = _dataset_manifest(
        out_path,
        data_root=data_root,
        dataset="ashare.share_float_event_pit",
        tier="pit",
        extra={
            "fetch_chunk_count": fetch_count,
            "source": "chinadata:share_float",
            "sync_strategy_version": SYNC_STRATEGY_VERSION,
            "sync_mode": sync_mode,
            "requested_start": start.isoformat(),
            "effective_start": effective_start.isoformat(),
            "honest_gaps": [
                "available_at uses ann_date plus next-session-open proxy because the feed exposes announcement date but not timestamp",
            ],
        },
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return manifest | {"manifest_path": str(manifest_path)}


def _resolve_sync_mode(prior_manifest: dict[str, Any] | None, *, full_refresh: bool) -> str:
    if full_refresh:
        return "full_refresh"
    if not prior_manifest:
        return "full_refresh"
    if int(prior_manifest.get("sync_strategy_version") or 0) < SYNC_STRATEGY_VERSION:
        return "repair_full_refresh"
    return "incremental"


def _write_or_merge_delta(
    *,
    out_path: Path,
    frame: pl.DataFrame,
    full_refresh: bool,
    partition_by: list[str],
    keys: list[str],
) -> None:
    if full_refresh or not out_path.exists():
        write_polars_delta(
            frame,
            out_path,
            mode="overwrite",
            partition_by=partition_by,
            schema_mode="overwrite",
        )
        return
    merge_polars_delta(frame, out_path, keys=keys)


def _write_dataset_progress_manifest(
    *,
    path: Path,
    manifest_path: Path,
    data_root: Path,
    dataset: str,
    tier: str,
    extra: dict[str, Any],
) -> None:
    manifest = _dataset_manifest(path, data_root=data_root, dataset=dataset, tier=tier, extra=extra)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")


def _existing_trade_dates(path: Path, *, start: date, end: date) -> set[date]:
    parquet_path = path.with_suffix(".parquet") if path.suffix == "" else path
    target = path if path.exists() else resolve_delta_or_parquet(parquet_path)
    if target is None or not target.exists():
        return set()
    frame = (
        scan_delta_or_parquet(target)
        .select(pl.col("event_time").dt.date().alias("trade_date"))
        .filter((pl.col("trade_date") >= pl.lit(start)) & (pl.col("trade_date") <= pl.lit(end)))
        .unique()
        .collect()
    )
    return set(frame.get_column("trade_date").to_list())


def _missing_trade_dates(path: Path, trade_dates: tuple[date, ...]) -> tuple[date, ...]:
    if not trade_dates:
        return ()
    existing = _existing_trade_dates(path, start=trade_dates[0], end=trade_dates[-1])
    return tuple(item for item in trade_dates if item not in existing)


def _existing_max_date(path: Path, column: str) -> date | None:
    parquet_path = path.with_suffix(".parquet") if path.suffix == "" else path
    target = path if path.exists() else resolve_delta_or_parquet(parquet_path)
    if target is None or not target.exists():
        return None
    frame = scan_delta_or_parquet(target).select(pl.col(column).max().alias("value")).collect()
    value = frame.row(0, named=True)["value"]
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _fetch_share_float_ranges(
    *,
    start: date,
    end: date,
    fields: str,
    token: str,
    pause_seconds: float,
    workers: int,
    temp_root: Path,
    normalizer: Callable[[pd.DataFrame], pd.DataFrame],
    symbols: tuple[str, ...],
) -> int:
    temp_root.mkdir(parents=True, exist_ok=True)
    written = 0

    def visit(chunk_start: date, chunk_end: date) -> None:
        nonlocal written
        frame = fetch_tushare_dataframe(
            "share_float",
            params={"start_date": chunk_start.strftime("%Y%m%d"), "end_date": chunk_end.strftime("%Y%m%d")},
            fields=fields,
            retries=2,
            token=token,
        )
        if frame is None or frame.empty:
            time.sleep(pause_seconds)
            return
        if len(frame.index) >= SHARE_FLOAT_LIMIT and chunk_start < chunk_end:
            midpoint = chunk_start + timedelta(days=(chunk_end - chunk_start).days // 2)
            visit(chunk_start, midpoint)
            visit(midpoint + timedelta(days=1), chunk_end)
            return
        if len(frame.index) >= SHARE_FLOAT_LIMIT and chunk_start == chunk_end:
            raise SystemExit(
                f"share_float still hit the {SHARE_FLOAT_LIMIT} row cap on {chunk_start.isoformat()}; "
                "the sync must add another partition key before the dataset can be trusted"
            )
        if symbols:
            frame = frame[frame["ts_code"].astype(str).str.upper().isin(symbols)]
            if frame.empty:
                time.sleep(pause_seconds)
                return
        normalized = normalizer(frame)
        if normalized.empty:
            time.sleep(pause_seconds)
            return
        normalized = normalized.drop_duplicates()
        pl.DataFrame(normalized.to_dict(orient="records"), infer_schema_length=None).write_parquet(
            temp_root / f"chunk-{written:05d}.parquet"
        )
        written += 1
        time.sleep(pause_seconds)

    visit(start, end)
    return written


def _trade_date_batches(trade_dates: tuple[date, ...], batch_size: int = 20) -> list[tuple[date, ...]]:
    return [trade_dates[index : index + batch_size] for index in range(0, len(trade_dates), batch_size)]


def _fetch_chunks(
    *,
    api_name: str,
    ranges: list[tuple[date, date]],
    fields: str,
    token: str,
    pause_seconds: float,
    temp_root: Path,
    normalizer: Callable[[pd.DataFrame], pd.DataFrame],
    symbols: tuple[str, ...],
    range_param_names: tuple[str, str] = ("start_date", "end_date"),
) -> int:
    temp_root.mkdir(parents=True, exist_ok=True)
    written = 0
    for idx, (chunk_start, chunk_end) in enumerate(ranges):
        frame = fetch_tushare_dataframe(
            api_name,
            params={
                range_param_names[0]: chunk_start.strftime("%Y%m%d"),
                range_param_names[1]: chunk_end.strftime("%Y%m%d"),
            },
            fields=fields,
            retries=2,
            token=token,
        )
        if frame is None or frame.empty:
            time.sleep(pause_seconds)
            continue
        if symbols:
            frame = frame[frame["ts_code"].astype(str).str.upper().isin(symbols)]
            if frame.empty:
                time.sleep(pause_seconds)
                continue
        normalized = normalizer(frame)
        if normalized.empty:
            time.sleep(pause_seconds)
            continue
        normalized = normalized.drop_duplicates()
        pl.DataFrame(normalized.to_dict(orient="records"), infer_schema_length=None).write_parquet(
            temp_root / f"chunk-{idx:05d}.parquet"
        )
        written += 1
        time.sleep(pause_seconds)
    return written


def _fetch_trade_date_chunks(
    *,
    api_name: str,
    trade_dates: tuple[date, ...],
    fields: str,
    token: str,
    pause_seconds: float,
    workers: int,
    temp_root: Path,
    normalizer: Callable[[pd.DataFrame], pd.DataFrame],
    symbols: tuple[str, ...],
) -> int:
    temp_root.mkdir(parents=True, exist_ok=True)
    written = 0

    def fetch_one(trade_date: date) -> tuple[date, pd.DataFrame]:
        frame = fetch_tushare_dataframe(
            api_name,
            params={"trade_date": trade_date.strftime("%Y%m%d")},
            fields=fields,
            retries=2,
            token=token,
        )
        time.sleep(pause_seconds)
        return trade_date, frame if frame is not None else pd.DataFrame()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_one, trade_date): trade_date for trade_date in trade_dates}
        for future in as_completed(futures):
            trade_date, frame = future.result()
            if frame.empty:
                continue
            if symbols:
                frame = frame[frame["ts_code"].astype(str).str.upper().isin(symbols)]
                if frame.empty:
                    continue
            normalized = normalizer(frame)
            if normalized.empty:
                continue
            normalized = normalized.drop_duplicates()
            pl.DataFrame(normalized.to_dict(orient="records"), infer_schema_length=None).write_parquet(
                temp_root / f"chunk-{trade_date.strftime('%Y%m%d')}.parquet"
            )
            written += 1
    return written


def _normalize_market_cap_chunk(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["market"] = data["symbol"].str.split(".").str[-1]
    trade_date = pd.to_datetime(data["trade_date"], format="%Y%m%d", errors="coerce", utc=True)
    data["event_time"] = trade_date
    data["available_at"] = trade_date + pd.to_timedelta(1, unit="D") + pd.to_timedelta(9 * 60 + 30, unit="m")
    source_updated_at = datetime.now(tz=UTC)
    data["source_updated_at"] = source_updated_at
    data["trade_year"] = trade_date.dt.year.astype("Int64")
    data["trade_month"] = trade_date.dt.month.astype("Int64")
    for column in (
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
    ):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["symbol", "event_time"])
    return data[
        [
            "symbol",
            "market",
            "event_time",
            "available_at",
            "source_updated_at",
            "trade_year",
            "trade_month",
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
        ]
    ]


def _normalize_industry_chunk(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["market"] = data["symbol"].str.split(".").str[-1]
    trade_date = pd.to_datetime(data["trade_date"], format="%Y%m%d", errors="coerce", utc=True)
    data["event_time"] = trade_date
    data["available_at"] = trade_date + pd.to_timedelta(1, unit="D") + pd.to_timedelta(9 * 60 + 30, unit="m")
    data["source_updated_at"] = datetime.now(tz=UTC)
    data["trade_year"] = trade_date.dt.year.astype("Int64")
    data["trade_month"] = trade_date.dt.month.astype("Int64")
    data["name"] = data.get("name", "").fillna("").astype(str)
    data["industry"] = data.get("industry", "").fillna("").astype(str)
    data["area"] = data.get("area", "").fillna("").astype(str)
    data = data.dropna(subset=["symbol", "event_time"])
    return data[
        [
            "symbol",
            "market",
            "event_time",
            "available_at",
            "source_updated_at",
            "trade_year",
            "trade_month",
            "name",
            "industry",
            "area",
        ]
    ]


def _normalize_share_float_chunk(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["symbol"] = data["ts_code"].astype(str).str.upper()
    data["market"] = data["symbol"].str.split(".").str[-1]
    ann_date = pd.to_datetime(data["ann_date"], format="%Y%m%d", errors="coerce", utc=True)
    float_date = pd.to_datetime(data["float_date"], format="%Y%m%d", errors="coerce", utc=True)
    data["event_time"] = float_date
    data["available_at"] = ann_date + pd.to_timedelta(1, unit="D") + pd.to_timedelta(9 * 60 + 30, unit="m")
    data["source_updated_at"] = datetime.now(tz=UTC)
    data["ann_date"] = ann_date
    data["ann_year"] = ann_date.dt.year.astype("Int64")
    data["ann_month"] = ann_date.dt.month.astype("Int64")
    data["holder_name"] = data.get("holder_name", "").fillna("").astype(str)
    data["share_type"] = data.get("share_type", "").fillna("").astype(str)
    for column in ("float_share", "float_ratio"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["symbol", "event_time", "available_at"])
    return data[
        [
            "symbol",
            "market",
            "event_time",
            "available_at",
            "source_updated_at",
            "ann_date",
            "ann_year",
            "ann_month",
            "float_share",
            "float_ratio",
            "holder_name",
            "share_type",
        ]
    ]


def _materialize_chunk_dir(temp_root: Path, out_path: Path, *, partition_columns: tuple[str, ...]) -> None:
    files = sorted(temp_root.glob("chunk-*.parquet"))
    if not files:
        raise SystemExit(f"no data chunks were produced under {temp_root}")
    glob_path = str(temp_root / "chunk-*.parquet").replace("\\", "/").replace("'", "''")
    out_sql = str(out_path).replace("\\", "/").replace("'", "''")
    partition_sql = ", ".join(partition_columns)
    conn = duckdb.connect()
    try:
        conn.execute(
            f"""
            COPY (
                SELECT *
                FROM read_parquet('{glob_path}')
                QUALIFY row_number() OVER (
                    PARTITION BY {partition_sql}
                    ORDER BY source_updated_at DESC
                ) = 1
                ORDER BY symbol, event_time
            ) TO '{out_sql}' (FORMAT PARQUET)
            """
        )
    finally:
        conn.close()


def _dataset_manifest(path: Path, *, data_root: Path, dataset: str, tier: str, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "tier": tier,
        "path": _relative(path, data_root),
        **dataset_stats(path),
        **extra,
    }


def _resolve_base_window(kline_path: Path, *, start: date | None, end: date | None) -> tuple[date, date, int]:
    conn = duckdb.connect()
    try:
        row = conn.execute(
            """
            SELECT
                min(CAST(event_time AS DATE)),
                max(CAST(event_time AS DATE)),
                count(DISTINCT symbol)
            FROM read_parquet(?)
            """,
            [str(kline_path)],
        ).fetchone()
    finally:
        conn.close()
    if row[0] is None or row[1] is None:
        raise SystemExit(f"empty kline PIT parquet: {kline_path}")
    base_start = max(row[0], DEFAULT_START)
    base_end = row[1]
    return start or base_start, end or base_end, int(row[2])


def _observed_trade_dates(kline_path: Path, *, start: date, end: date) -> tuple[date, ...]:
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT CAST(event_time AS DATE) AS trade_date
            FROM read_parquet(?)
            WHERE CAST(event_time AS DATE) BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            [str(kline_path), start, end],
        ).fetchall()
    finally:
        conn.close()
    return tuple(row[0] for row in rows)


def _month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    current = date(start.year, start.month, 1)
    while current <= end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        ranges.append((max(current, start), min(month_end, end)))
        current = next_month
    return ranges


def _build_factor_gap_report(
    *,
    data_root: Path,
    start: date,
    end: date,
    trade_dates: tuple[date, ...],
    manifests: dict[str, Any],
) -> dict[str, Any]:
    market_cap_path = canonical_delta_path(data_root / "pit" / "market_cap_daily_pit.parquet")
    industry_path = canonical_delta_path(data_root / "pit" / "industry_daily_pit.parquet")
    share_float_path = canonical_delta_path(data_root / "pit" / "share_float_event_pit.parquet")
    market_cap_dates = _existing_trade_dates(market_cap_path, start=start, end=end)
    industry_dates = _existing_trade_dates(industry_path, start=start, end=end)
    return {
        "dataset": "ashare.chinadata_factor_gap_report_v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "market_cap_daily": {
            "expected_trade_date_count": len(trade_dates),
            "present_trade_date_count": len(market_cap_dates),
            "missing_trade_date_count": len(trade_dates) - len(market_cap_dates),
            "missing_trade_dates_sample": [item.isoformat() for item in sorted(set(trade_dates) - market_cap_dates)[:20]],
            "manifest": manifests.get("market_cap_daily"),
        },
        "industry_daily": {
            "expected_trade_date_count": len(trade_dates),
            "present_trade_date_count": len(industry_dates),
            "missing_trade_date_count": len(trade_dates) - len(industry_dates),
            "missing_trade_dates_sample": [item.isoformat() for item in sorted(set(trade_dates) - industry_dates)[:20]],
            "manifest": manifests.get("industry_daily"),
        },
        "share_float_event": {
            "manifest": manifests.get("share_float_event"),
            "notes": [
                "share_float is event data, so there is no daily trade-date completeness target",
                "completeness is enforced by dynamic date-range splitting below the 6000-row upstream cap",
            ],
            "stats": dataset_stats(share_float_path) if share_float_path.exists() else None,
        },
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _parse_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
