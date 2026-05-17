"""Resolve PIT parquet inputs from an A-share data lake."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from app.research.ashare_data_contract import PRICE_BAR_COLUMNS, TRADABILITY_STATUS_COLUMNS


LEGACY_KLINE_COLUMNS = frozenset(
    {
        "code",
        "market",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }
)


class DataLakeResolutionError(ValueError):
    """Raised when no usable PIT daily parquet can be found."""


def resolve_kline_daily_parquet(data_root: Path, symbols: tuple[str, ...]) -> Path:
    """Find a PIT-safe daily kline parquet under a data lake root."""

    if not data_root.exists():
        raise DataLakeResolutionError(f"A-share data root not found: {data_root}")

    candidates = _manifest_candidates(data_root) or _glob_candidates(data_root)
    for candidate in candidates:
        if _is_usable_daily_parquet(candidate, symbols):
            return candidate
    raise DataLakeResolutionError(
        f"no usable kline daily PIT parquet found under {data_root}"
    )


def resolve_kline_daily_symbols(data_root: Path) -> tuple[str, ...]:
    """Resolve the scan universe from coverage manifest or PIT parquet content."""

    if not data_root.exists():
        raise DataLakeResolutionError(f"A-share data root not found: {data_root}")
    manifest_symbols = _manifest_symbols(data_root)
    if manifest_symbols:
        return manifest_symbols

    parquet = resolve_kline_daily_parquet(data_root, ())
    try:
        frame = pl.read_parquet(parquet, columns=["symbol"])
    except Exception as exc:
        raise DataLakeResolutionError(
            f"cannot resolve symbols from A-share parquet: {parquet}"
        ) from exc
    if "symbol" not in frame.columns:
        raise DataLakeResolutionError(
            f"A-share parquet does not expose symbol universe: {parquet}"
        )
    symbols = tuple(sorted(str(symbol) for symbol in frame["symbol"].unique().to_list()))
    if not symbols:
        raise DataLakeResolutionError(f"A-share data root has an empty symbol universe: {data_root}")
    return symbols


def resolve_tradability_status_parquet(data_root: Path) -> Path | None:
    """Find an optional tradability-status PIT parquet under a data lake root."""

    candidates = _dataset_candidates(data_root, {"ashare.tradability_status_pit", "ashare.instrument_status_pit"})
    candidates.extend(_glob_dataset_candidates(data_root, ("*tradability*status*.parquet", "*instrument*status*.parquet")))
    for candidate in candidates:
        if _has_columns(candidate, TRADABILITY_STATUS_COLUMNS):
            return candidate
    return None


def resolve_adjustment_factor_parquet(data_root: Path) -> Path | None:
    """Find an optional adjustment-factor PIT parquet under a data lake root."""

    candidates = _dataset_candidates(data_root, {"ashare.adjustment_factor_pit"})
    candidates.extend(_glob_dataset_candidates(data_root, ("*adjustment*factor*.parquet", "*adj*factor*.parquet")))
    for candidate in candidates:
        if _has_columns(candidate, {"symbol", "event_time", "available_at", "source_updated_at", "factor"}):
            return candidate
    return None


def resolve_trading_calendar_parquet(data_root: Path) -> Path | None:
    """Find an optional trading-calendar snapshot parquet under a data lake root.

    Matches the layout produced by `app.research.pipeline.calendar.build_calendar_snapshot`
    (`<data_root>/calendar/trading_calendar.parquet`), plus coverage-manifest entries
    keyed under `ashare.trading_calendar`.
    """

    if not data_root.exists():
        return None
    canonical = data_root / "calendar" / "trading_calendar.parquet"
    if _has_columns(canonical, {"date", "market", "is_trading_day"}):
        return canonical
    candidates = _dataset_candidates(data_root, {"ashare.trading_calendar"})
    candidates.extend(_glob_dataset_candidates(data_root, ("*trading*calendar*.parquet",)))
    for candidate in candidates:
        if _has_columns(candidate, {"date", "market", "is_trading_day"}):
            return candidate
    return None


def _manifest_candidates(data_root: Path) -> list[Path]:
    manifest = data_root / "_manifest" / "coverage.json"
    if not manifest.exists():
        return []
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("items", payload.get("entries", [])) if isinstance(payload, dict) else payload
    candidates: list[Path] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset", ""))
        if dataset not in {"kline_daily", "ashare.kline_daily_pit"}:
            continue
        path = _entry_path(item)
        if path is None:
            continue
        candidates.append(path if path.is_absolute() else data_root / path)
    return candidates


def _dataset_candidates(data_root: Path, datasets: set[str]) -> list[Path]:
    manifest = data_root / "_manifest" / "coverage.json"
    if not manifest.exists():
        return []
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("items", payload.get("entries", [])) if isinstance(payload, dict) else payload
    candidates: list[Path] = []
    for item in entries:
        if not isinstance(item, dict) or str(item.get("dataset", "")) not in datasets:
            continue
        path = _entry_path(item)
        if path is not None:
            candidates.append(path if path.is_absolute() else data_root / path)
    return candidates


def _manifest_symbols(data_root: Path) -> tuple[str, ...]:
    manifest = data_root / "_manifest" / "coverage.json"
    if not manifest.exists():
        return ()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("items", payload.get("entries", [])) if isinstance(payload, dict) else payload
    symbols: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset", ""))
        if dataset not in {"kline_daily", "ashare.kline_daily_pit"}:
            continue
        raw_symbols = item.get("symbols", ())
        if isinstance(raw_symbols, list):
            symbols.update(str(symbol) for symbol in raw_symbols if str(symbol).strip())
    return tuple(sorted(symbols))


def _entry_path(item: dict[str, Any]) -> Path | None:
    for key in ("path", "uri", "file", "parquet_path"):
        raw = item.get(key)
        if not raw:
            continue
        value = str(raw)
        if value.startswith("parquet://"):
            value = value.removeprefix("parquet://")
        if "#" in value:
            value = value.split("#", 1)[0]
        return Path(value)
    return None


def _glob_candidates(data_root: Path) -> list[Path]:
    patterns = ("*kline*daily*.parquet", "*daily*.parquet")
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pattern in patterns:
        for path in data_root.rglob(pattern):
            if path in seen or "_manifest" in path.parts:
                continue
            seen.add(path)
            candidates.append(path)
    return candidates


def _glob_dataset_candidates(data_root: Path, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []
    if not data_root.exists():
        return candidates
    for pattern in patterns:
        for path in data_root.rglob(pattern):
            if path in seen or "_manifest" in path.parts:
                continue
            seen.add(path)
            candidates.append(path)
    return candidates


def _is_usable_daily_parquet(path: Path, symbols: tuple[str, ...]) -> bool:
    if not path.exists() or path.suffix.lower() != ".parquet":
        return False
    try:
        frame = pl.read_parquet(path, n_rows=200)
    except Exception:
        return False
    columns = set(frame.columns)
    if PRICE_BAR_COLUMNS.issubset(columns):
        return True
    if not LEGACY_KLINE_COLUMNS.issubset(columns):
        return False
    return True


def _has_columns(path: Path, columns: set[str] | frozenset[str]) -> bool:
    if not path.exists() or path.suffix.lower() != ".parquet":
        return False
    try:
        frame = pl.read_parquet(path, n_rows=1)
    except Exception:
        return False
    return set(columns).issubset(frame.columns)
