# Experiment Manifest Specification

This is the reproducibility contract for `k-atana` experiments.

Every serious research result must have an immutable manifest. A notebook,
frontend screenshot, LLM summary, or chat message is not a manifest.

## Required Fields

An experiment manifest must include:

- `experiment_id`
- `name`
- `code_commit`
- `data_versions`
- `factor_versions`
- `config_hash`
- `random_seed`
- `created_at`
- optional output `artifact`

`data_versions` must point to immutable dataset versions, not mutable table
names like "latest daily bars".

## Dataset Snapshot

A dataset snapshot records a concrete data artifact:

- dataset name
- market
- frequency
- tier: `raw`, `normalized`, or `pit`
- artifact URI
- artifact content hash
- row count
- source
- parent snapshots

Snapshots are append-only. Fixing bad data creates a new snapshot.

## Dataset Version

A dataset version points research code to a specific snapshot and schema:

- `snapshot_id`
- `schema_hash`
- `data_hash`
- `as_of`
- semantic or date version string

Research code may not read anonymous "current" data.

## LLMwiki Role

LLMwiki is useful for:

- experiment summaries
- lineage explanations
- agent decisions
- caveats and postmortems
- search over prior research

LLMwiki is not allowed to be the source of truth for:

- dataset hashes
- code commit
- factor definitions
- random seed
- backtest metrics
- execution decisions

The source of truth is:

```text
Git commit + dataset version + manifest hash + artifact hash
```

LLMwiki stores a human-readable memory keyed by `manifest_hash`.

## Hard Rule

If a result cannot be reproduced from its manifest, it is commentary, not
research evidence.
