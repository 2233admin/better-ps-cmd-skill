"""Shared local path resolution for legacy data stores and caches."""

from __future__ import annotations

import os
from pathlib import Path


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return backend_root().parent


def default_data_dir() -> Path:
    return project_root() / "DATA"


def resolve_data_dir() -> Path:
    raw = os.environ.get("KATANA_DATA_DIR", "").strip()
    path = Path(raw).expanduser() if raw else default_data_dir()
    return path.resolve()


def default_ashare_data_dir() -> Path:
    return default_data_dir() / "ashare"


def resolve_ashare_data_dir() -> Path:
    raw = os.environ.get("KATANA_ASHARE_DATA_DIR", "").strip()
    path = Path(raw).expanduser() if raw else default_ashare_data_dir()
    return path.resolve()


def default_ashare_lake_root() -> Path:
    return resolve_ashare_data_dir() / "lake"


def resolve_ashare_lake_root() -> Path:
    raw = os.environ.get("KATANA_ASHARE_LAKE_ROOT", "").strip()
    path = Path(raw).expanduser() if raw else default_ashare_lake_root()
    return path.resolve()


def resolve_ashare_duckdb_path() -> Path:
    raw = os.environ.get("KATANA_ASHARE_DUCKDB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    candidates = (
        resolve_ashare_data_dir() / "Aquant.duckdb",
        resolve_ashare_data_dir() / "ashare.duckdb",
        resolve_data_dir() / "Aquant.duckdb",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def resolve_duckdb_path(filename: str = "quant.duckdb") -> Path:
    raw = os.environ.get("KATANA_DUCKDB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (resolve_data_dir() / filename).resolve()


def resolve_chroma_dir(dirname: str = "chromadb") -> Path:
    raw = os.environ.get("KATANA_CHROMA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (resolve_data_dir() / dirname).resolve()
