# CLAUDE.md — k-atana

Briefing for any Claude / LLM agent entering this repo. Read top-to-bottom before writing code.

## What this repo is

A-share + crypto quantitative research + paper / live_test trading terminal. Research-first, not auto-trading. PIT-correct (point-in-time) data contract is load-bearing — violating it is a critical bug.

Main chain:

```
data ingest → factor / feature → strategy / signal → backtest → paper / live_test → morning package
```

## Before you write code

### 1. Check if a wheel already does it

`docs/EXTERNAL_WHEEL_AUDIT.md` is the catalogue of external libraries that overlap with this repo (qlib, empyrical, rqalpha, vnpy, alphalens, …). Before building anything that resembles a library — factor lib, backtest engine, attribution math, state machine, rate-limiter, data adapter — spend 30 minutes evaluating wheels and write a 3–5 candidate shortlist. Only proceed with self-write after declaring **"wheels evaluated, none fit because [evidence]"**.

LLM default is to skip this step. Don't. Two outstanding tickets (XAR-417 single-component / XAR-418 strategic) exist because previous Claude work shipped hand-rolled factor + attribution code without doing this audit first.

### 2. Check what's already in the repo

`docs/OLD_WHEEL_REUSE_MAP.md` catalogues internal code reuse — which `app/research/*` modules are canonical, which `legacy/*` is parked, which `backend/src/quant_terminal/*` is compatibility-only. Read this before adding a new module.

### 3. Honor the architectural boundaries

- `backend/app/research/` — PIT-safe research, broker-neutral
- `backend/app/trading/` — execution boundary; research code must not import broker SDKs
- `backend/app/trade/` — compatibility shims only
- `backend/app/ai_lab/` — WIP 2026 roadmap; excluded from default test gate
- `legacy/` — parked, do not import without first relocating + adding tests
- LLM output is `auxiliary_notes` commentary, never evidence — see `docs/PRODUCT_BOUNDARY.md`

### 4. Honor the PIT contract

`backend/app/research/ashare_data_contract.py` defines required columns (`event_time` + `available_at` + `source_updated_at`). Anything that fabricates `available_at` from `event_time` is broken — see `docs/ASHARE_DATA_PIT_SPEC.md` for the full spec.

## Operational quick reference

| Task | Command |
|---|---|
| End-to-end demo (fixture mode, no TDX required) | `python scripts/demo-run-pipeline.py` |
| End-to-end with real PIT lake | `python scripts/demo-run-pipeline.py --data-root <lake-root>` |
| Throughput benchmark + control gate | `scripts/run-ashare-control-plane.ps1` |
| PIT lake build chain | `ingest-ashare-bridge.py` → `build-ashare-coverage.py` → `validate-ashare-lake.py` |
| Full test suite | `cd backend && pytest tests/unit tests/contract` |

Fixture venv: `C:\Users\Administrator\AppData\Local\Temp\katana-fixture-venv` (Windows host only). Project venv is uv-managed; never `pip install -e .` over CUDA torch.

## Push workflow

Direct push to `gitea origin/main`. No PR review. Authentication via env:

```powershell
git -c "http.https://git.xart.top:8418/.extraheader=Authorization: token $env:GITEA_CLAUDEQWQ_TOKEN" push origin main
```

If push rejected (remote ahead): `git pull --rebase origin main`, then retry.

## Linear

Team key: `XAR`. CLI: `lin issue ...` or `lin api query <gql>` for comments (the `lin issue comment new` subcommand has a known argument validation bug in v0.16; use raw GraphQL `commentCreate` mutation). Issue UUID required for the GraphQL path; human keys (`XAR-417`) work for the typed `lin issue ...` paths.

## Anti-patterns (real, observed)

- Writing a factor library / attribution math from scratch without checking qlib / empyrical first (see `docs/EXTERNAL_WHEEL_AUDIT.md`)
- Fabricating `available_at` from `event_time` (PIT contract violation)
- Importing broker SDKs from `app/research/*` (architecture boundary violation)
- Using `cat` heredocs in bash on this host (`cat` is aliased to `bat`) — use the `Write` tool or PowerShell here-string
- `bash` commands with chained `cd && cmd` fail with "z: command not found" on this host — use PowerShell or absolute paths

## Pointers

- **Architecture**: `docs/RESEARCH_ARCHITECTURE.md`, `docs/ARCHITECTURE_DECISION_QMT_PHASE1.md`
- **Product boundary**: `docs/PRODUCT_BOUNDARY.md` (trading modes, execution limits, what the system *will not* do)
- **PIT spec**: `docs/ASHARE_DATA_PIT_SPEC.md`, `docs/CRYPTO_DATA_PIT_SPEC.md`
- **Project structure**: `docs/PROJECT_STRUCTURE.md`
- **External wheels**: `docs/EXTERNAL_WHEEL_AUDIT.md` ← always check first
- **Internal reuse map**: `docs/OLD_WHEEL_REUSE_MAP.md`
- **Account reader matrix**: `docs/ACCOUNT_READER_MATRIX.md` (TDX / EasyXT / QMT field coverage)
