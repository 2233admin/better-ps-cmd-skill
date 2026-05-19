"""Daily factor-evolution orchestrator for k-atana 挂机 mode (Lane J reconfig).

Runs symbolic-regression engines (gplearn always; shinka + pysr on crypto only
IF their source modules exist — see honest-gap section) against both the crypto
PIT lake and the A-share PIT lake, joins the resulting alpha pools cross-universe,
applies the Lane D DSR + BH FDR + CSCV PBO ship-gate filter, and writes a daily
report with a binary verdict (PASS / FAIL @ >= 3 surviving alphas).

Port notes (archive/crypto-w1-complete -> factor-framework-merge, 2026-05-20):
  - run_mining(universe, ...) unified entry point (PR-3a); no split run_mining_ashare.
  - dsr_pbo import path: app.research.methodology.dsr_pbo (was same path -- drop-in).
  - _import_ship_gate_helpers() ELIMINATED: run-ship-gate-eval.py absent from
    current branch. load_gplearn_factors / load_pysr_factors / load_shinka_factors /
    build_pbo_matrix / run_pool are inlined here directly importing from dsr_pbo.
  - shinka + pysr engines: guarded by module existence check. Both dirs have only
    .pyc artifacts on this branch; source .py absent. They are declared as gaps.
  - All CLI flags, env vars, artifact layout identical to archive.

Layout (Lane J):

    artifacts/daily-evo/YYYY-MM-DD/
        crypto/
            gplearn/top_factors.json
            shinka/final_report.md  (+ best_factor.py)  [gap: no source module]
            pysr/top_factors.json                        [gap: no source module]
            <engine>.log
        ashare/
            gplearn/top_factors.json
            gplearn.log
        REPORT.md
        SURVIVORS.csv
        dsr_pbo_breakdown.json

Usage:
    # default: --universe both --date <today UTC>
    python scripts/run-daily-evo.py

    # crypto-only (legacy compat)
    python scripts/run-daily-evo.py --universe crypto

    # ashare-only, smoke
    GPLEARN_POP=80 GPLEARN_GEN=3 python scripts/run-daily-evo.py \\
        --universe ashare --ashare-sample-symbols 50

    # idempotent re-run with explicit date (reuses cached engine outputs)
    python scripts/run-daily-evo.py --date 2026-05-19

    # dry-run: resolve plan + print commands without running engines
    python scripts/run-daily-evo.py --dry-run

    # kill switch via env (Task Scheduler-friendly)
    set KATANA_DAILY_EVO=skip && python scripts/run-daily-evo.py   # no-op exit
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

K_ATANA_ROOT = Path(r"D:\projects\k-atana")
BACKEND = K_ATANA_ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"

# Data lakes
CRYPTO_PARQUET = K_ATANA_ROOT / ".data" / "lake" / "crypto" / "kline_pit_combined.parquet"
ASHARE_LAKE = K_ATANA_ROOT / ".data" / "lake" / "pit"
ASHARE_KLINE = ASHARE_LAKE / "kline_daily_pit.parquet"
ASHARE_VALUATION = ASHARE_LAKE / "valuation_daily_pit.parquet"
ASHARE_INDUSTRY = ASHARE_LAKE / "industry_classification_pit.parquet"
ASHARE_MACRO = ASHARE_LAKE / "macro_cn_monthly_pit.parquet"

# Artifact roots
NEW_ARTIFACTS_ROOT = K_ATANA_ROOT / "artifacts" / "daily-evo"      # Lane J layout
LINEAGE_PATH = NEW_ARTIFACTS_ROOT / "LINEAGE.jsonl"

KILL_SWITCH_ENV = "KATANA_DAILY_EVO"

# -------------------------------------------------------------------------
# Engine source module existence guards
#
# shinka and pysr dirs exist on this branch but contain only .pyc artifacts —
# no runnable Python source. The ENGINES matrix below omits them and declares
# an honest gap. When their source modules are restored, add them back.
# -------------------------------------------------------------------------

_SHINKA_MODULE_PATH = BACKEND / "app" / "research" / "factors" / "mining" / "shinka" / "run_evo.py"
_PYSR_MODULE_PATH   = BACKEND / "app" / "research" / "factors" / "mining" / "pysr" / "mine.py"
_SHINKA_AVAILABLE   = _SHINKA_MODULE_PATH.exists()
_PYSR_AVAILABLE     = _PYSR_MODULE_PATH.exists()

# -------------------------------------------------------------------------
# Engine matrix per universe.
#
# Each entry resolves a subprocess plan: module + cli arg builder. The arg
# builder receives (artifacts_dir, ashare_args_dict) and returns a list of
# CLI args to append after `python -m <module>`.
# -------------------------------------------------------------------------

def _crypto_shinka_args(artifacts_dir: Path, _: dict[str, Any]) -> list[str]:
    return ["--artifacts-dir", str(artifacts_dir)]


def _crypto_pysr_args(artifacts_dir: Path, _: dict[str, Any]) -> list[str]:
    return [
        "--combined-parquet", str(CRYPTO_PARQUET),
        "--out-dir", str(artifacts_dir),
        "--niterations", os.environ.get("PYSR_NITER", "30"),
        "--populations", os.environ.get("PYSR_POPS", "16"),
        "--population-size", os.environ.get("PYSR_POP_SIZE", "33"),
        "--maxsize", os.environ.get("PYSR_MAXSIZE", "20"),
        "--top-k", "10",
    ]


def _crypto_gplearn_args(artifacts_dir: Path, _: dict[str, Any]) -> list[str]:
    return [
        "--universe", "crypto",
        "--combined-parquet", str(CRYPTO_PARQUET),
        "--out-dir", str(artifacts_dir),
        "--population-size", os.environ.get("GPLEARN_POP", "500"),
        "--generations", os.environ.get("GPLEARN_GEN", "20"),
        "--top-k", "10",
    ]


def _ashare_gplearn_args(artifacts_dir: Path, ashare_args: dict[str, Any]) -> list[str]:
    args = [
        "--universe", "ashare",
        "--ashare-kline", str(ASHARE_KLINE),
        "--ashare-valuation", str(ASHARE_VALUATION),
        "--out-dir", str(artifacts_dir),
        "--population-size", os.environ.get("GPLEARN_POP_ASHARE", os.environ.get("GPLEARN_POP", "300")),
        "--generations", os.environ.get("GPLEARN_GEN_ASHARE", os.environ.get("GPLEARN_GEN", "10")),
        "--top-k", "10",
    ]
    if ASHARE_INDUSTRY.exists():
        args += ["--ashare-industry", str(ASHARE_INDUSTRY)]
    if ASHARE_MACRO.exists():
        args += ["--ashare-macro", str(ASHARE_MACRO)]
    sample = ashare_args.get("sample_symbols")
    if sample:
        args += ["--ashare-sample-symbols", str(sample)]
    return args


# Always include gplearn for both universes (source confirmed present).
ENGINES: dict[str, dict[str, Any]] = {
    "crypto:gplearn": {
        "universe": "crypto",
        "engine": "gplearn",
        "label": "gplearn crypto (symbolic regression)",
        "module": "app.research.factors.mining.gplearn.mine",
        "default_timeout_s": 3600,
        "extra_env": {},
        "args_builder": _crypto_gplearn_args,
        "output_marker": "top_factors.json",
        "n_trials_fn": lambda env: int(env.get("GPLEARN_POP", "500")) * int(env.get("GPLEARN_GEN", "20")),
    },
    "ashare:gplearn": {
        "universe": "ashare",
        "engine": "gplearn",
        "label": "gplearn A-share (symbolic regression)",
        "module": "app.research.factors.mining.gplearn.mine",
        "default_timeout_s": 5400,
        "extra_env": {},
        "args_builder": _ashare_gplearn_args,
        "output_marker": "top_factors.json",
        "n_trials_fn": lambda env: (
            int(env.get("GPLEARN_POP_ASHARE", env.get("GPLEARN_POP", "300"))) *
            int(env.get("GPLEARN_GEN_ASHARE", env.get("GPLEARN_GEN", "10")))
        ),
    },
}

# Conditionally add shinka / pysr when their source modules exist.
if _SHINKA_AVAILABLE:
    ENGINES["crypto:shinka"] = {
        "universe": "crypto",
        "engine": "shinka",
        "label": "ShinkaEvolve (LLM-driven, MiniMax-M2.7-highspeed)",
        "module": "app.research.factors.mining.shinka.run_evo",
        "default_timeout_s": 7200,
        "extra_env": {
            "FACTOR_EVO_GENERATIONS": os.environ.get("FACTOR_EVO_GENERATIONS", "10"),
            "FACTOR_EVO_POPULATION": os.environ.get("FACTOR_EVO_POPULATION", "4"),
        },
        "args_builder": _crypto_shinka_args,
        "output_marker": "final_report.md",
        "n_trials_fn": lambda env: int(env.get("FACTOR_EVO_POPULATION", "4")) * int(env.get("FACTOR_EVO_GENERATIONS", "10")),
    }

if _PYSR_AVAILABLE:
    ENGINES["crypto:pysr"] = {
        "universe": "crypto",
        "engine": "pysr",
        "label": "PySR (SymbolicRegression.jl)",
        "module": "app.research.factors.mining.pysr.mine",
        "default_timeout_s": 10800,
        "extra_env": {},
        "args_builder": _crypto_pysr_args,
        "output_marker": "top_factors.json",
        "n_trials_fn": lambda env: int(env.get("PYSR_POPS", "16")) * int(env.get("PYSR_NITER", "30")),
    }

# Ship-gate threshold
SHIP_GATE_THRESHOLD = 3
DSR_ALPHA = 0.05
BH_ALPHA = 0.05
PBO_N_SPLITS = 6


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _validate_date(s: str) -> str:
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bad --date {s!r}: expected YYYY-MM-DD") from exc
    return s


def _check_data_freshness(parquet: Path, inst_col: str) -> dict[str, Any]:
    """Inspect a PIT parquet via the project venv (no pl import at module level)."""
    if not parquet.exists():
        return {"present": False, "error": f"missing: {parquet}"}
    code = (
        "import polars as pl, json;"
        f"df=pl.read_parquet(r'{parquet}');"
        "out={"
        "'rows': len(df),"
        f"'instruments': df['{inst_col}'].n_unique() if '{inst_col}' in df.columns else None,"
        "'t0': str(df['event_time'].min()) if 'event_time' in df.columns else None,"
        "'t1': str(df['event_time'].max()) if 'event_time' in df.columns else None,"
        "};"
        "print(json.dumps(out))"
    )
    r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return {"present": True, "error": f"inspect failed: {r.stderr[:200]}"}
    return {"present": True, **json.loads(r.stdout.strip())}


def _run_meta_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "_run_meta.json"


def _write_run_meta(artifacts_dir: Path, engine_key: str, env_snapshot: dict[str, str]) -> None:
    """Sidecar capturing the env snapshot used for this engine run."""
    spec = ENGINES[engine_key]
    n_trials = spec["n_trials_fn"](env_snapshot)
    _run_meta_path(artifacts_dir).write_text(
        json.dumps(
            {
                "engine_key": engine_key,
                "ran_at": _iso_utc(),
                "n_trials": int(n_trials),
                "env_snapshot": env_snapshot,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_run_meta_n_trials(artifacts_dir: Path) -> int | None:
    p = _run_meta_path(artifacts_dir)
    if not p.exists():
        return None
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("n_trials"))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _run_engine(
    engine_key: str,
    artifacts_dir: Path,
    timeout_s: int,
    log_path: Path,
    ashare_args: dict[str, Any],
    force: bool,
) -> dict[str, Any]:
    """Run one engine via subprocess unless its output marker already exists."""
    spec = ENGINES[engine_key]
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    marker = artifacts_dir / spec["output_marker"]
    if marker.exists() and not force:
        return {
            "engine_key": engine_key,
            "universe": spec["universe"],
            "engine": spec["engine"],
            "label": spec["label"],
            "status": "cached",
            "duration_s": 0.0,
            "exit_code": 0,
            "log_path": str(log_path.relative_to(K_ATANA_ROOT)) if log_path.exists() else "",
            "marker": str(marker.relative_to(K_ATANA_ROOT)),
        }

    cmd: list[str] = [str(VENV_PY), "-m", spec["module"]]
    cmd += spec["args_builder"](artifacts_dir, ashare_args)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    for k, v in spec["extra_env"].items():
        env[k] = str(v)

    t0 = time.time()
    result: dict[str, Any] = {
        "engine_key": engine_key,
        "universe": spec["universe"],
        "engine": spec["engine"],
        "label": spec["label"],
        "started_at": _iso_utc(),
        "command": " ".join(cmd),
    }

    try:
        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(f"# {_iso_utc()} starting {engine_key}\n")
            logf.write(f"# cmd: {' '.join(cmd)}\n")
            logf.flush()
            proc = subprocess.run(
                cmd,
                cwd=str(K_ATANA_ROOT),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        result["exit_code"] = int(proc.returncode)
        result["status"] = "ok" if proc.returncode == 0 else "fail"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["exit_code"] = None
        result["error"] = f"exceeded {timeout_s}s"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "crash"
        result["exit_code"] = None
        result["error"] = repr(exc)[:200]

    result["duration_s"] = round(time.time() - t0, 1)
    result["log_path"] = str(log_path.relative_to(K_ATANA_ROOT))
    result["marker"] = str(marker.relative_to(K_ATANA_ROOT))
    result["marker_present"] = marker.exists()

    if result["status"] == "ok" and marker.exists():
        env_snapshot = {
            k: env.get(k, "")
            for k in (
                "GPLEARN_POP", "GPLEARN_GEN",
                "GPLEARN_POP_ASHARE", "GPLEARN_GEN_ASHARE",
                "PYSR_POPS", "PYSR_NITER",
                "FACTOR_EVO_POPULATION", "FACTOR_EVO_GENERATIONS",
            )
        }
        _write_run_meta(artifacts_dir, engine_key, env_snapshot)

    return result


# -------------------------------------------------------------------------
# Inlined loader helpers (formerly in run-ship-gate-eval.py which is absent
# from factor-framework-merge). Import dsr_filter + cscv_pbo directly.
# -------------------------------------------------------------------------

def _ensure_backend_importable() -> None:
    backend_path = str(BACKEND)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def _import_dsr_pbo():
    """Return (dsr_filter, cscv_pbo) from app.research.methodology.dsr_pbo."""
    _ensure_backend_importable()
    from app.research.methodology.dsr_pbo import (  # noqa: PLC0415
        cscv_pbo,
        dsr_filter,
    )
    return dsr_filter, cscv_pbo


def load_gplearn_factors(json_path: Path) -> list[dict]:
    """Load gplearn top_factors.json.  Returns [] if file absent."""
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out = []
    for entry in data:
        # Normalise key names: archive uses wf_mean_ic_t; mine.py writes
        # wf_information_ratio as the IR field. Both are valid SR proxies.
        # The combined_dsr_pbo call uses sr_key="wf_mean_ic_t"; if absent,
        # fall back to wf_information_ratio.
        if "wf_mean_ic_t" not in entry:
            entry = dict(entry)  # shallow copy before mutation
            entry["wf_mean_ic_t"] = entry.get("wf_information_ratio", 0.0)
        out.append(entry)
    return out


def load_pysr_factors(json_path: Path) -> list[dict]:
    """Load pysr top_factors.json.  Returns [] if file absent."""
    if not json_path.exists():
        return []
    return json.loads(json_path.read_text(encoding="utf-8"))


def load_shinka_factors(json_path: Path) -> list[dict]:
    """Load shinka candidate_fitness.json.  Returns [] if file absent."""
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # shinka schema: list of dicts with 'name', 'fitness', 'per_fold'
    out = []
    for entry in data:
        cand = dict(entry)
        if "wf_mean_ic_t" not in cand:
            cand["wf_mean_ic_t"] = cand.get("fitness", 0.0)
        if "wf_mean_ic" not in cand:
            cand["wf_mean_ic"] = cand.get("mean_ic", 0.0)
        out.append(cand)
    return out


def build_pbo_matrix(candidates: list[dict]) -> pd.DataFrame:
    """Build T x N matrix of per-fold IC values for CSCV PBO.

    Each column is a candidate; rows are fold IC_t values.
    Candidates without wf_per_fold get a column of NaN (excluded by cscv_pbo).
    """
    cols: dict[str, list[float]] = {}
    n_folds = 0
    for cand in candidates:
        folds = cand.get("wf_per_fold", [])
        name = cand.get("name", cand.get("expression", f"cand_{id(cand)}"))
        if folds:
            ic_vals = [f.get("ic_t_stat", f.get("fold_score", 0.0)) for f in folds]
            n_folds = max(n_folds, len(ic_vals))
            cols[name] = ic_vals
    if not cols or n_folds == 0:
        return pd.DataFrame()
    # Pad columns shorter than n_folds with NaN
    padded = {k: v + [float("nan")] * (n_folds - len(v)) for k, v in cols.items()}
    return pd.DataFrame(padded)


def _load_candidates_for_engine(engine_key: str, artifacts_dir: Path) -> list[dict]:
    """Load candidate factors for a given engine from its artifacts dir."""
    spec = ENGINES[engine_key]
    engine = spec["engine"]
    universe = spec["universe"]

    if engine == "gplearn":
        cands = load_gplearn_factors(artifacts_dir / "top_factors.json")
    elif engine == "pysr":
        cands = load_pysr_factors(artifacts_dir / "top_factors.json")
    elif engine == "shinka":
        cands = load_shinka_factors(artifacts_dir / "candidate_fitness.json")
    else:
        cands = []

    for c in cands:
        c["pool"] = engine_key
        c["universe"] = universe
        c["engine"] = engine
    return cands


def _combined_dsr_pbo(
    all_cands: list[dict],
    n_trials_total: int,
) -> dict[str, Any]:
    """Single cross-universe DSR + BH + CSCV PBO call."""
    dsr_filter, cscv_pbo = _import_dsr_pbo()

    if not all_cands:
        return {
            "n_input": 0,
            "n_surviving": 0,
            "n_trials_total": n_trials_total,
            "surviving": [],
            "annotated": [],
            "dsr_p_values": [],
            "pbo_score": float("nan"),
            "pbo_n_combinations": 0,
            "pbo_note": "no candidates",
        }

    dsr_res = dsr_filter(
        all_cands,
        n_trials=n_trials_total,
        alpha_dsr=DSR_ALPHA,
        alpha_bh=BH_ALPHA,
        sr_key="wf_mean_ic_t",
        folds_key="wf_per_fold",
    )

    pbo_mat = build_pbo_matrix(all_cands)
    if not pbo_mat.empty and pbo_mat.shape[0] >= 4:
        pbo = cscv_pbo(pbo_mat, n_splits=PBO_N_SPLITS)
    else:
        pbo = {"pbo_score": float("nan"), "n_combinations": 0, "note": "insufficient folds for PBO"}

    pbo_score = pbo.get("pbo_score", float("nan"))
    pbo_pass = (not math.isnan(pbo_score)) and (pbo_score < 0.5)
    annotated: list[dict] = []
    surviving: list[dict] = []
    for i, cand in enumerate(all_cands):
        dsr_p = dsr_res["dsr_p_values"][i]
        bh_ok = dsr_res["bh_survive"][i]
        dsr_pass = bh_ok and dsr_p >= (1.0 - DSR_ALPHA)
        survives = dsr_pass and pbo_pass
        ann = {
            **{k: v for k, v in cand.items() if k != "wf_per_fold"},
            "dsr_p": round(dsr_p, 6),
            "bh_survive": bool(bh_ok),
            "dsr_pass": bool(dsr_pass),
            "pbo_pass": bool(pbo_pass),
            "survives": bool(survives),
        }
        annotated.append(ann)
        if survives:
            surviving.append(ann)

    return {
        "n_input": len(all_cands),
        "n_surviving": len(surviving),
        "n_trials_total": n_trials_total,
        "surviving": surviving,
        "annotated": annotated,
        "dsr_p_values": dsr_res["dsr_p_values"],
        "sr_std_across_candidates": dsr_res.get("sr_std_across_candidates", 0.0),
        "pbo_score": pbo_score,
        "pbo_n_combinations": pbo.get("n_combinations", 0),
        "pbo_note": pbo.get("note", ""),
    }


def _per_pool_diagnostics(
    cands_by_pool: dict[str, list[dict]],
    actual_n_trials: dict[str, int],
) -> dict[str, Any]:
    """Per-pool DSR diagnostics (breakdown JSON only, not the verdict)."""
    dsr_filter, cscv_pbo = _import_dsr_pbo()
    out: dict[str, Any] = {}
    for pool, cands in cands_by_pool.items():
        if not cands:
            out[pool] = {"n_input": 0, "n_surviving": 0, "n_trials": actual_n_trials.get(pool, 0)}
            continue
        try:
            n_trials = actual_n_trials.get(pool, len(cands))
            res = dsr_filter(
                cands,
                n_trials=n_trials,
                alpha_dsr=DSR_ALPHA,
                alpha_bh=BH_ALPHA,
                sr_key="wf_mean_ic_t",
                folds_key="wf_per_fold",
            )
            pbo_mat = build_pbo_matrix(cands)
            if not pbo_mat.empty and pbo_mat.shape[0] >= 4:
                pbo = cscv_pbo(pbo_mat, n_splits=PBO_N_SPLITS)
            else:
                pbo = {"pbo_score": float("nan"), "n_combinations": 0, "note": ""}
            pbo_score = pbo.get("pbo_score", float("nan"))
            out[pool] = {
                "n_input": res["n_input"],
                "n_surviving": res["n_surviving"],
                "n_trials": n_trials,
                "pbo_score": pbo_score if not math.isnan(pbo_score) else None,
                "sr_std_across_candidates": res.get("sr_std_across_candidates", 0),
                "candidates": [
                    {
                        "name": c.get("name", c.get("expression", "")[:60]),
                        "pool": pool,
                        "wf_mean_ic_t": c.get("wf_mean_ic_t", 0.0),
                        "wf_mean_ic": c.get("wf_mean_ic", 0.0),
                        "dsr_p": round(res["dsr_p_values"][i], 6),
                        "bh_survive": res["bh_survive"][i],
                        "survives": i in res["surviving_indices"],
                    }
                    for i, c in enumerate(cands)
                ],
            }
        except Exception as exc:  # noqa: BLE001
            out[pool] = {"n_input": len(cands), "error": repr(exc)[:200]}
    return out


def _write_survivors_csv(out_dir: Path, surviving: list[dict]) -> Path:
    path = out_dir / "SURVIVORS.csv"
    fieldnames = [
        "pool", "universe", "engine", "name", "wf_mean_ic_t",
        "wf_mean_ic", "dsr_p", "bh_survive", "dsr_pass", "pbo_pass", "survives",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for s in surviving:
            writer.writerow({k: s.get(k, "") for k in fieldnames})
    return path


def _update_lineage(
    date_str: str,
    n_input: int,
    n_surviving: int,
    n_trials_total: int,
    pbo_score: float,
    verdict: str,
    engines_ran: list[str],
) -> None:
    """Append-or-replace a JSONL entry keyed by date (idempotent)."""
    NEW_ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": date_str,
        "generated_at": _iso_utc(),
        "n_input": n_input,
        "n_surviving": n_surviving,
        "n_trials_total": n_trials_total,
        "pbo_score": None if math.isnan(pbo_score) else round(pbo_score, 4),
        "ship_gate_verdict": verdict,
        "engines_ran": engines_ran,
        "schema": "lane-j-v1",
    }

    lines: list[str] = []
    if LINEAGE_PATH.exists():
        for raw in LINEAGE_PATH.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("date") != date_str:
                lines.append(raw)
    lines.append(json.dumps(entry, ensure_ascii=False))
    LINEAGE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_pbo(score: float) -> str:
    return "n/a" if math.isnan(score) else f"{score:.4f}"


def _write_report(
    *,
    date_str: str,
    out_dir: Path,
    universes_requested: list[str],
    engines_to_run: list[str],
    data_status: dict[str, dict[str, Any]],
    engine_results: list[dict[str, Any]],
    candidates_per_pool: dict[str, list[dict]],
    actual_n_trials_per_pool: dict[str, int],
    combined: dict[str, Any],
    per_pool_diag: dict[str, Any],
    elapsed_total_s: float,
    smoke_only: bool,
    cli_args: list[str],
) -> Path:
    n_input = combined["n_input"]
    n_surviving = combined["n_surviving"]
    verdict = "PASS" if n_surviving >= SHIP_GATE_THRESHOLD else "FAIL"
    pbo = combined["pbo_score"]

    shinka_gap = "" if _SHINKA_AVAILABLE else " [GAP: shinka source absent on this branch]"
    pysr_gap = "" if _PYSR_AVAILABLE else " [GAP: pysr source absent on this branch]"

    lines: list[str] = [
        f"# k-atana daily evo report -- {date_str}",
        "",
        f"Generated: {_iso_utc()}",
        f"Wall-clock total: {elapsed_total_s:.1f}s",
        f"Universes requested: {', '.join(universes_requested)}",
        f"Engines planned: {', '.join(engines_to_run)}",
        f"Mode: {'smoke-only (mining skipped)' if smoke_only else 'full'}",
        f"CLI: `{' '.join(cli_args)}`",
    ]
    if not _SHINKA_AVAILABLE:
        lines.append(f"NOTE:{shinka_gap}")
    if not _PYSR_AVAILABLE:
        lines.append(f"NOTE:{pysr_gap}")
    lines += [
        "",
        "## Ship gate verdict",
        "",
        f"- **Verdict: {verdict}**  (surviving alphas: {n_surviving} / threshold >= {SHIP_GATE_THRESHOLD})",
        f"- Candidates submitted (post-WF, pre-DSR): {n_input}",
        f"- Combined n_trials (cross-universe, multi-test surface): {combined['n_trials_total']}",
        f"- Combined PBO (CSCV, n_splits={PBO_N_SPLITS}): {_format_pbo(pbo)}"
        f"  -> {'PASS (<0.5)' if not math.isnan(pbo) and pbo < 0.5 else 'FAIL/skip'}",
        f"- DSR alpha: {DSR_ALPHA}  |  BH FDR: {BH_ALPHA}",
        "",
        "## Data snapshot",
        "",
    ]
    for u, st in data_status.items():
        if st.get("present"):
            lines.append(
                f"- {u}: rows={st.get('rows')} instruments={st.get('instruments')} "
                f"range={st.get('t0')} -> {st.get('t1')}"
            )
        else:
            lines.append(f"- {u}: MISSING ({st.get('error', '?')})")
    lines.append("")

    lines += [
        "## Engine run status",
        "",
        "| Pool (universe:engine) | Status | Duration (s) | Exit | Log |",
        "|------------------------|--------|--------------|------|-----|",
    ]
    for r in engine_results:
        lines.append(
            f"| {r['engine_key']} | {r['status']} | {r['duration_s']} | "
            f"{r.get('exit_code', '')} | `{r.get('log_path', '')}` |"
        )
    lines.append("")

    lines += [
        "## Per-pool candidate counts + n_trials (audit trail)",
        "",
        "| Pool | candidates loaded | n_trials (actual) |",
        "|------|------------------:|-------------------:|",
    ]
    for pool, cands in candidates_per_pool.items():
        lines.append(f"| {pool} | {len(cands)} | {actual_n_trials_per_pool.get(pool, 0)} |")
    lines.append("")

    lines += [
        "## Surviving alphas (cross-universe pool, DSR + BH + PBO)",
        "",
    ]
    if not combined["surviving"]:
        lines.append("_No alpha survived DSR/BH/PBO filter at this evaluation._")
        lines.append("")
    else:
        lines += [
            "| Pool | Universe | Name | wf_mean_ic_t | wf_mean_ic | DSR_p | BH | PBO_pass |",
            "|------|----------|------|-------------:|-----------:|------:|----|----------|",
        ]
        for s in combined["surviving"]:
            lines.append(
                f"| {s.get('pool')} | {s.get('universe')} | `{str(s.get('name', s.get('expression', '')))[:60]}` "
                f"| {s.get('wf_mean_ic_t', 0):.4f} | {s.get('wf_mean_ic', 0):.4f} "
                f"| {s.get('dsr_p', 0):.4f} | {s.get('bh_survive')} | {s.get('pbo_pass')} |"
            )
        lines.append("")

    lines += [
        "## Per-pool diagnostics (DSR with per-pool n_trials)",
        "",
        "Note: the verdict above uses a single cross-universe DSR call",
        f"(combined n_trials={combined['n_trials_total']}). The per-pool rows below",
        "are diagnostic only.",
        "",
        "| Pool | n_input | n_surviving | n_trials | PBO |",
        "|------|--------:|------------:|---------:|----:|",
    ]
    for pool, d in per_pool_diag.items():
        pbo_p = d.get("pbo_score")
        pbo_s = "n/a" if pbo_p is None else f"{pbo_p:.4f}"
        lines.append(
            f"| {pool} | {d.get('n_input', 0)} | {d.get('n_surviving', 0)} "
            f"| {d.get('n_trials', 0)} | {pbo_s} |"
        )
    lines.append("")

    lines += [
        "## Artifacts",
        "",
        f"- SURVIVORS.csv  -> `{(out_dir / 'SURVIVORS.csv').relative_to(K_ATANA_ROOT)}`",
        f"- dsr_pbo_breakdown.json  -> `{(out_dir / 'dsr_pbo_breakdown.json').relative_to(K_ATANA_ROOT)}`",
        f"- LINEAGE.jsonl  -> `{LINEAGE_PATH.relative_to(K_ATANA_ROOT)}`  (appended/replaced for {date_str})",
        "",
        "## Gap register (honest)",
        "",
        "- shinka engine: source module absent on factor-framework-merge branch (only .pyc).",
        "- pysr engine: source module absent on factor-framework-merge branch (only .pyc).",
        "- run-ship-gate-eval.py absent: load helpers inlined directly here.",
        "- shinka/pysr are auto-enabled when their .py source reappears (existence guard at import time).",
    ]

    report_path = out_dir / "REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main() -> int:
    if os.environ.get(KILL_SWITCH_ENV, "").lower() == "skip":
        print(f"[daily-evo] {KILL_SWITCH_ENV}=skip -> no-op exit.", file=sys.stderr)
        return 0

    parser = argparse.ArgumentParser(
        description="k-atana daily evo runner (Lane J, factor-framework-merge).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Engines active: gplearn (crypto + ashare).
Engines gapped: shinka, pysr (source .py absent on this branch; auto-enabled when restored).

Environment knobs:
  GPLEARN_POP / GPLEARN_GEN          gplearn population / generations (crypto)
  GPLEARN_POP_ASHARE / GPLEARN_GEN_ASHARE  override for A-share (falls back to above)
  PYSR_POPS / PYSR_NITER / PYSR_POP_SIZE / PYSR_MAXSIZE  PySR run config
  FACTOR_EVO_GENERATIONS / FACTOR_EVO_POPULATION          Shinka config
  KATANA_DAILY_EVO=skip              kill switch (Task Scheduler-friendly)
""",
    )
    parser.add_argument("--universe", choices=["crypto", "ashare", "both"], default="both",
                        help="Which universes to mine (default: both).")
    parser.add_argument("--date", type=_validate_date, default=_today_utc_date(),
                        help="Date stamp YYYY-MM-DD for artifacts (default: today UTC).")
    parser.add_argument("--artifacts-root", default=str(NEW_ARTIFACTS_ROOT),
                        help="Override artifacts root (default: artifacts/daily-evo).")
    parser.add_argument("--skip", action="append", default=[],
                        help="Engine key (e.g. crypto:gplearn) to skip. Repeatable.")
    parser.add_argument("--only", action="append", default=[],
                        help="Engine key to run exclusively. Repeatable. Overrides --skip.")
    parser.add_argument("--smoke-only", action="store_true",
                        help="Skip mining; only run DSR/PBO filter on cached engine outputs.")
    parser.add_argument("--force", action="store_true",
                        help="Re-run engines even if their output marker exists (no cache reuse).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve plan + print commands; do not run engines.")
    parser.add_argument("--ashare-sample-symbols", type=int, default=None,
                        help="Pass-through: sample N A-share symbols (smoke knob).")
    parser.add_argument("--gplearn-timeout", type=int,
                        default=ENGINES["crypto:gplearn"]["default_timeout_s"],
                        help="Timeout in seconds for gplearn crypto engine.")
    parser.add_argument("--gplearn-ashare-timeout", type=int,
                        default=ENGINES["ashare:gplearn"]["default_timeout_s"],
                        help="Timeout in seconds for gplearn ashare engine.")
    # Conditional timeout args for optional engines
    if _SHINKA_AVAILABLE:
        parser.add_argument("--shinka-timeout", type=int,
                            default=ENGINES["crypto:shinka"]["default_timeout_s"])
    if _PYSR_AVAILABLE:
        parser.add_argument("--pysr-timeout", type=int,
                            default=ENGINES["crypto:pysr"]["default_timeout_s"])
    parser.add_argument("--commit", action="store_true",
                        help="git add+commit REPORT.md + SURVIVORS.csv after run.")
    args = parser.parse_args()

    if not VENV_PY.exists():
        print(f"FAIL: venv python missing: {VENV_PY}", file=sys.stderr)
        return 2

    # Resolve universes -> default engine plan
    if args.universe == "crypto":
        universes = ["crypto"]
    elif args.universe == "ashare":
        universes = ["ashare"]
    else:
        universes = ["crypto", "ashare"]

    planned = [k for k, spec in ENGINES.items() if spec["universe"] in universes]
    if args.only:
        run_engines = [k for k in planned if k in args.only]
    else:
        run_engines = [k for k in planned if k not in args.skip]

    timeouts: dict[str, int] = {
        "crypto:gplearn": args.gplearn_timeout,
        "ashare:gplearn": args.gplearn_ashare_timeout,
    }
    if _SHINKA_AVAILABLE:
        timeouts["crypto:shinka"] = args.shinka_timeout
    if _PYSR_AVAILABLE:
        timeouts["crypto:pysr"] = args.pysr_timeout

    date_str = args.date
    out_root = Path(args.artifacts_root) / date_str
    out_root.mkdir(parents=True, exist_ok=True)

    # Pre-flight data checks (don't abort, just record)
    data_status: dict[str, dict[str, Any]] = {}
    if "crypto" in universes:
        data_status["crypto"] = _check_data_freshness(CRYPTO_PARQUET, "inst_id")
    if "ashare" in universes:
        data_status["ashare"] = _check_data_freshness(ASHARE_KLINE, "symbol")

    print(
        f"[daily-evo] date={date_str} universes={universes} engines={run_engines} out={out_root}",
        file=sys.stderr,
    )
    if not _SHINKA_AVAILABLE:
        print("[daily-evo] GAP: shinka source absent — engine skipped", file=sys.stderr)
    if not _PYSR_AVAILABLE:
        print("[daily-evo] GAP: pysr source absent — engine skipped", file=sys.stderr)
    for u, st in data_status.items():
        print(f"[daily-evo] data status [{u}]: {st}", file=sys.stderr)

    if args.dry_run:
        print("[daily-evo] --dry-run: planned commands:", file=sys.stderr)
        for k in run_engines:
            spec = ENGINES[k]
            sub_dir = out_root / spec["universe"] / spec["engine"]
            cmd = [str(VENV_PY), "-m", spec["module"]] + spec["args_builder"](
                sub_dir, {"sample_symbols": args.ashare_sample_symbols}
            )
            print(f"  {k}: {' '.join(cmd)}  (timeout {timeouts.get(k, '?')}s)", file=sys.stderr)
        return 0

    t_total = time.time()
    engine_results: list[dict[str, Any]] = []

    if not args.smoke_only:
        for k in run_engines:
            spec = ENGINES[k]
            sub_dir = out_root / spec["universe"] / spec["engine"]
            log_path = out_root / spec["universe"] / f"{spec['engine']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[daily-evo] running {k} -> {sub_dir} (timeout {timeouts.get(k)}s)", file=sys.stderr)
            res = _run_engine(
                engine_key=k,
                artifacts_dir=sub_dir,
                timeout_s=timeouts[k],
                log_path=log_path,
                ashare_args={"sample_symbols": args.ashare_sample_symbols},
                force=args.force,
            )
            print(f"[daily-evo] {k}: status={res['status']} dur={res['duration_s']}s", file=sys.stderr)
            engine_results.append(res)

    # -------- DSR/PBO filter stage (always runs, also on --smoke-only) --------
    print("[daily-evo] running DSR/PBO filter ...", file=sys.stderr)

    candidates_per_pool: dict[str, list[dict]] = {}
    actual_n_trials_per_pool: dict[str, int] = {}

    pools_to_load: list[str] = []
    if args.smoke_only:
        pools_to_load = list(ENGINES.keys())
    else:
        for r in engine_results:
            if r["status"] in {"ok", "cached"}:
                pools_to_load.append(r["engine_key"])

    n_trials_total = 0
    for k in pools_to_load:
        spec = ENGINES[k]
        sub_dir = out_root / spec["universe"] / spec["engine"]
        try:
            cands = _load_candidates_for_engine(k, sub_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[daily-evo] WARN: load failed for {k}: {exc!r}", file=sys.stderr)
            cands = []
        candidates_per_pool[k] = cands

        if cands:
            n_trials = _read_run_meta_n_trials(sub_dir)
            if n_trials is None:
                n_trials = spec["n_trials_fn"](os.environ)
            actual_n_trials_per_pool[k] = n_trials
            n_trials_total += n_trials
        else:
            actual_n_trials_per_pool[k] = 0

    all_cands: list[dict] = []
    for k, lst in candidates_per_pool.items():
        all_cands.extend(lst)

    combined = _combined_dsr_pbo(all_cands, n_trials_total)
    per_pool_diag = _per_pool_diagnostics(candidates_per_pool, actual_n_trials_per_pool)

    # -------- Write artifacts --------
    survivors_csv = _write_survivors_csv(out_root, combined["surviving"])
    breakdown_path = out_root / "dsr_pbo_breakdown.json"
    breakdown_path.write_text(
        json.dumps(
            {
                "date": date_str,
                "schema": "lane-j-v1",
                "dsr_alpha": DSR_ALPHA,
                "bh_alpha": BH_ALPHA,
                "ship_gate_threshold": SHIP_GATE_THRESHOLD,
                "n_trials_total": combined["n_trials_total"],
                "n_input": combined["n_input"],
                "n_surviving": combined["n_surviving"],
                "ship_gate_verdict": "PASS" if combined["n_surviving"] >= SHIP_GATE_THRESHOLD else "FAIL",
                "pbo_score": None if math.isnan(combined["pbo_score"]) else combined["pbo_score"],
                "pbo_n_combinations": combined["pbo_n_combinations"],
                "pbo_note": combined["pbo_note"],
                "sr_std_across_candidates": combined.get("sr_std_across_candidates", 0.0),
                "annotated_candidates": combined["annotated"],
                "per_pool_diagnostics": per_pool_diag,
                "engine_results": engine_results,
                "actual_n_trials_per_pool": actual_n_trials_per_pool,
                "gaps": {
                    "shinka_source_absent": not _SHINKA_AVAILABLE,
                    "pysr_source_absent": not _PYSR_AVAILABLE,
                    "ship_gate_eval_inlined": True,
                },
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    elapsed = time.time() - t_total
    report_path = _write_report(
        date_str=date_str,
        out_dir=out_root,
        universes_requested=universes,
        engines_to_run=run_engines,
        data_status=data_status,
        engine_results=engine_results,
        candidates_per_pool=candidates_per_pool,
        actual_n_trials_per_pool=actual_n_trials_per_pool,
        combined=combined,
        per_pool_diag=per_pool_diag,
        elapsed_total_s=elapsed,
        smoke_only=args.smoke_only,
        cli_args=sys.argv,
    )

    verdict = "PASS" if combined["n_surviving"] >= SHIP_GATE_THRESHOLD else "FAIL"
    print(f"[daily-evo] wrote {report_path}", file=sys.stderr)
    print(f"[daily-evo] SURVIVORS.csv -> {survivors_csv}", file=sys.stderr)
    print(
        f"[daily-evo] ship gate verdict: {verdict} "
        f"({combined['n_surviving']}/{SHIP_GATE_THRESHOLD} surviving, n_trials_total={n_trials_total})",
        file=sys.stderr,
    )

    _update_lineage(
        date_str=date_str,
        n_input=combined["n_input"],
        n_surviving=combined["n_surviving"],
        n_trials_total=combined["n_trials_total"],
        pbo_score=combined["pbo_score"],
        verdict=verdict,
        engines_ran=[r["engine_key"] for r in engine_results if r["status"] in {"ok", "cached"}],
    )

    if args.commit:
        try:
            def _rel(p: Path) -> str:
                return str(p.relative_to(K_ATANA_ROOT)).replace("\\", "/")
            subprocess.run(
                ["git", "-C", str(K_ATANA_ROOT), "add",
                 _rel(report_path), _rel(survivors_csv),
                 _rel(breakdown_path), _rel(LINEAGE_PATH)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(K_ATANA_ROOT), "commit",
                 "-m", f"daily-evo: {date_str} verdict={verdict} surviving={combined['n_surviving']}"],
                check=True, capture_output=True, text=True,
            )
            print(f"[daily-evo] committed {report_path.name}", file=sys.stderr)
        except subprocess.CalledProcessError as exc:
            print(f"[daily-evo] git commit failed: {exc.stderr[:200] if exc.stderr else exc}", file=sys.stderr)

    failures = [r for r in engine_results if r["status"] not in {"ok", "cached"}]
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
