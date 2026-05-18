# app.ai_lab

WIP AI research module. Holds Kronos K-line model stubs, TFT model scaffold,
predict/train loops, and the `/ai/*` FastAPI router.

## Status

- Scheduled for 2026 roadmap (XAR-416). Do **not** delete.
- Not part of the default ship path quality gate.
- Excluded from the default pytest run via `pyproject.toml`
  (`addopts = "--ignore=tests/ai_lab"`); lab-specific tests live under
  `tests/ai_lab/` when added.

## Layout

- `predict.py`, `train.py` -- predictor / trainer stubs
- `kronos_predictor.py`, `kronos_model/` -- Kronos service + model code
- `models/tft.py` -- TFT scaffold
- `api.py` -- FastAPI router mounted at `/ai/*` (still registered by `app.main`)

## Back-compat

`app/ai/__init__.py` is a thin shim that re-exports `app.ai_lab` and emits a
`DeprecationWarning` once. Migrate stragglers to `app.ai_lab` and drop the
shim once callers are clean.
