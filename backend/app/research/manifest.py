"""Immutable dataset and experiment manifest contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from .models import Frequency, Market


class ArtifactKind(str, Enum):
    RAW_DUMP = "raw_dump"
    NORMALIZED_TABLE = "normalized_table"
    PIT_TABLE = "pit_table"
    FEATURE_VIEW = "feature_view"
    REPORT = "report"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def canonical_json(payload: Any) -> str:
    return json.dumps(
        _canonical_payload(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{field_name} must be a sha256 hex digest")


def _canonical_payload(payload: Any) -> Any:
    if is_dataclass(payload):
        return _canonical_payload(asdict(payload))
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, datetime):
        return payload.isoformat()
    if isinstance(payload, date):
        return payload.isoformat()
    if isinstance(payload, dict):
        return {str(key): _canonical_payload(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_canonical_payload(item) for item in payload]
    return payload


@dataclass(frozen=True)
class DataArtifact:
    kind: ArtifactKind
    uri: str
    content_hash: str
    created_at: datetime = field(default_factory=_now_utc)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.uri.strip():
            raise ValueError("artifact uri is required")
        _require_sha256(self.content_hash, "artifact content_hash")


@dataclass(frozen=True)
class DatasetSnapshot:
    name: str
    market: Market
    frequency: Frequency
    tier: str
    artifact: DataArtifact
    row_count: int
    source: str
    snapshot_id: str = field(default_factory=lambda: f"ds-{uuid4().hex}")
    parents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dataset snapshot name is required")
        if not self.source.strip():
            raise ValueError("dataset snapshot source is required")
        if self.row_count < 0:
            raise ValueError("dataset snapshot row_count cannot be negative")
        if self.tier not in {"raw", "normalized", "pit"}:
            raise ValueError("dataset snapshot tier must be raw, normalized, or pit")


@dataclass(frozen=True)
class DatasetVersion:
    name: str
    market: Market
    frequency: Frequency
    snapshot_id: str
    schema_hash: str
    data_hash: str
    as_of: datetime
    version: str
    parents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dataset version name is required")
        if not self.snapshot_id.strip():
            raise ValueError("dataset version snapshot_id is required")
        if not self.version.strip():
            raise ValueError("dataset version is required")
        if self.version.lower() == "latest":
            raise ValueError("dataset version cannot be latest")
        _require_sha256(self.schema_hash, "dataset schema_hash")
        _require_sha256(self.data_hash, "dataset data_hash")


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    name: str
    code_commit: str
    data_versions: tuple[DatasetVersion, ...]
    factor_versions: tuple[str, ...]
    config_hash: str
    random_seed: int
    created_at: datetime = field(default_factory=_now_utc)
    artifact: DataArtifact | None = None

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment manifest experiment_id is required")
        if not self.name.strip():
            raise ValueError("experiment manifest name is required")
        if not self.code_commit.strip():
            raise ValueError("experiment manifest code_commit is required")
        if not self.data_versions:
            raise ValueError("experiment manifest data_versions are required")
        if not self.factor_versions:
            raise ValueError("experiment manifest factor_versions are required")
        _require_sha256(self.config_hash, "experiment config_hash")

    def to_payload(self) -> dict[str, Any]:
        return _canonical_payload(asdict(self))

    def manifest_hash(self) -> str:
        return sha256_json(self.to_payload())


@dataclass(frozen=True)
class LLMWikiResearchMemory:
    """LLMwiki-facing summary, not the source of truth."""

    manifest_hash: str
    title: str
    summary: str
    decisions: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.manifest_hash, "llmwiki manifest_hash")
        if not self.title.strip():
            raise ValueError("llmwiki memory title is required")
        if not self.summary.strip():
            raise ValueError("llmwiki memory summary is required")
