"""Contract tests for immutable experiment manifests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def test_experiment_manifest_spec_defines_llmwiki_as_memory_not_truth():
    doc = Path(__file__).resolve().parents[3] / "docs" / "EXPERIMENT_MANIFEST_SPEC.md"
    text = doc.read_text(encoding="utf-8")

    assert "code_commit" in text
    assert "data_versions" in text
    assert "manifest_hash" in text
    assert "LLMwiki is not allowed to be the source of truth" in text
    assert "Git commit + dataset version + manifest hash + artifact hash" in text


def test_dataset_snapshot_requires_valid_hash_and_tier():
    from app.research.manifest import ArtifactKind, DataArtifact, DatasetSnapshot
    from app.research.models import Frequency, Market

    artifact = DataArtifact(
        kind=ArtifactKind.PIT_TABLE,
        uri="duckdb://ashare.kline_daily_pit/2024-01-31",
        content_hash=HASH_A,
    )
    snapshot = DatasetSnapshot(
        name="ashare.kline_daily_pit",
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        tier="pit",
        artifact=artifact,
        row_count=100,
        source="tdx",
    )

    assert snapshot.tier == "pit"
    assert snapshot.artifact.content_hash == HASH_A

    with pytest.raises(ValueError, match="sha256"):
        DataArtifact(kind=ArtifactKind.PIT_TABLE, uri="x", content_hash="not-a-hash")

    with pytest.raises(ValueError, match="tier"):
        DatasetSnapshot(
            name="bad",
            market=Market.ASHARE,
            frequency=Frequency.DAILY,
            tier="latest",
            artifact=artifact,
            row_count=1,
            source="tdx",
        )


def test_experiment_manifest_requires_dataset_versions_and_hashes():
    from app.research.manifest import DatasetVersion, ExperimentManifest, sha256_json
    from app.research.models import Frequency, Market

    dataset_version = DatasetVersion(
        name="ashare.kline_daily_pit",
        market=Market.ASHARE,
        frequency=Frequency.DAILY,
        snapshot_id="ds-001",
        schema_hash=HASH_A,
        data_hash=HASH_B,
        as_of=datetime(2024, 2, 1, tzinfo=UTC),
        version="2024-02-01",
    )
    config_hash = sha256_json({"rebalance": "daily", "lookback": 20})
    manifest = ExperimentManifest(
        experiment_id="exp-001",
        name="ashare_momentum_20d",
        code_commit="c8b3779",
        data_versions=(dataset_version,),
        factor_versions=("momentum_20d:v1",),
        config_hash=config_hash,
        random_seed=42,
    )

    assert len(manifest.manifest_hash()) == 64

    with pytest.raises(ValueError, match="data_versions"):
        ExperimentManifest(
            experiment_id="exp-002",
            name="bad",
            code_commit="c8b3779",
            data_versions=(),
            factor_versions=("momentum_20d:v1",),
            config_hash=config_hash,
            random_seed=42,
        )

    with pytest.raises(ValueError, match="config_hash"):
        ExperimentManifest(
            experiment_id="exp-003",
            name="bad",
            code_commit="c8b3779",
            data_versions=(dataset_version,),
            factor_versions=("momentum_20d:v1",),
            config_hash="latest",
            random_seed=42,
        )


def test_llmwiki_memory_is_keyed_by_manifest_hash():
    from app.research.manifest import LLMWikiResearchMemory

    memory = LLMWikiResearchMemory(
        manifest_hash=HASH_D,
        title="A-share momentum 20d",
        summary="PIT-safe plan generated from immutable manifest.",
        decisions=("use only ashare.kline_daily_pit",),
        caveats=("execution constraints not yet modeled",),
    )

    assert memory.manifest_hash == HASH_D

    with pytest.raises(ValueError, match="manifest_hash"):
        LLMWikiResearchMemory(
            manifest_hash="exp-001",
            title="bad",
            summary="not keyed by immutable manifest",
        )
