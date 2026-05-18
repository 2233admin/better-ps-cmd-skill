"""Operate the local A-share data lake build steps.

This is an orchestrator, not a transformer. It keeps long-running/network
downloads explicit while giving local rebuilds one repeatable entry point.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_PROJECT = REPO_ROOT / "backend"
COMMON_DIRS = ("raw", "normalized", "pit", "features", "experiments", "_manifest")


@dataclass
class StepResult:
    name: str
    status: str
    command: list[str] = field(default_factory=list)
    reason: str | None = None
    returncode: int | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        summary = init_layout(args)
    elif args.command == "build-local":
        summary = build_local(args)
    elif args.command == "download-tdx-raw":
        summary = download_tdx_raw(args)
    elif args.command == "sync-chinadata-status":
        summary = sync_chinadata_status(args)
    elif args.command == "sync-chinadata-factors":
        summary = sync_chinadata_factors(args)
    elif args.command == "report-source-gaps":
        summary = report_source_gaps(args)
    elif args.command == "migrate-sidecars-to-delta":
        summary = migrate_sidecars_to_delta(args)
    elif args.command == "status":
        summary = status(args)
    elif args.command == "watch-progress":
        return watch_progress_cli(args)
    else:  # pragma: no cover - argparse enforces this
        raise SystemExit(f"unknown command: {args.command}")

    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(_format_summary(summary))
    return 1 if summary.get("failed") else 0


def main_argparse_for_test(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create the shared lake directory layout")
    init_parser.add_argument("--data-root", default="DATA", help="Top-level DATA root")
    init_parser.add_argument("--assets", default="ashare,crypto")
    init_parser.add_argument("--dry-run", action="store_true")
    init_parser.add_argument("--json", action="store_true")

    build_parser = subparsers.add_parser("build-local", help="Run non-network A-share lake build steps")
    _add_ashare_common_args(build_parser)
    build_parser.add_argument("--date", default=_today_iso(), help="Artifact date/run date YYYY-MM-DD")
    build_parser.add_argument("--run-id", help="Run id for snapshot outputs, defaults to --date as YYYYMMDD")
    build_parser.add_argument("--db-path", help="DuckDB cache path for index export")
    build_parser.add_argument("--start", help="Optional start date for index/backtest view")
    build_parser.add_argument("--end", help="Optional end date for index/backtest view")
    build_parser.add_argument("--symbols", help="Optional comma-separated symbol subset for backtest view")
    build_parser.add_argument("--as-of", help="Optional PIT visibility cutoff for backtest view")
    build_parser.add_argument("--min-listed-days", type=int, default=21)
    build_parser.add_argument("--skip-index", action="store_true")
    build_parser.add_argument("--skip-normalize-tdx", action="store_true")
    build_parser.add_argument("--skip-backtest-view", action="store_true")
    build_parser.add_argument("--skip-coverage", action="store_true")
    build_parser.add_argument("--skip-readiness", action="store_true")
    build_parser.add_argument("--require-tdx-raw", action="store_true")
    build_parser.add_argument("--require-index", action="store_true")
    build_parser.add_argument("--dry-run", action="store_true")
    build_parser.add_argument("--json", action="store_true")

    download_parser = subparsers.add_parser("download-tdx-raw", help="Explicitly download raw TDX snapshots")
    _add_ashare_common_args(download_parser)
    download_parser.add_argument("--run-id", default=_today_run_id())
    download_parser.add_argument("--tdx-cli", default=str(Path(r"D:\projects\tdx\tdxcli-rs\target\debug\tdx-cli.exe")))
    download_parser.add_argument("--symbol-scope", choices=("all", "tradable"), default="all")
    download_parser.add_argument("--limit", type=int)
    download_parser.add_argument("--workers", type=int, default=4)
    download_parser.add_argument("--timeout", type=int, default=45)
    download_parser.add_argument("--skip-finance", action="store_true")
    download_parser.add_argument("--skip-blocks", action="store_true")
    download_parser.add_argument("--force", action="store_true")
    download_parser.add_argument("--dry-run", action="store_true")
    download_parser.add_argument("--json", action="store_true")

    chinadata_parser = subparsers.add_parser(
        "sync-chinadata-status",
        help="Build official A-share tradability PIT from ChinaData/Tushare",
    )
    _add_ashare_common_args(chinadata_parser)
    chinadata_parser.add_argument("--start")
    chinadata_parser.add_argument("--end")
    chinadata_parser.add_argument("--symbols")
    chinadata_parser.add_argument("--token")
    chinadata_parser.add_argument("--pause-seconds", type=float, default=0.05)
    chinadata_parser.add_argument("--dry-run", action="store_true")
    chinadata_parser.add_argument("--json", action="store_true")

    factor_parser = subparsers.add_parser(
        "sync-chinadata-factors",
        help="Build market-cap, industry, and share-float PIT datasets from ChinaData/Tushare",
    )
    _add_ashare_common_args(factor_parser)
    factor_parser.add_argument("--start")
    factor_parser.add_argument("--end")
    factor_parser.add_argument("--symbols")
    factor_parser.add_argument("--token")
    factor_parser.add_argument("--pause-seconds", type=float, default=0.05)
    factor_parser.add_argument("--workers", type=int, default=4)
    factor_parser.add_argument("--skip-market-cap", action="store_true")
    factor_parser.add_argument("--skip-industry", action="store_true")
    factor_parser.add_argument("--skip-share-float", action="store_true")
    factor_parser.add_argument("--full-refresh", action="store_true")
    factor_parser.add_argument("--dry-run", action="store_true")
    factor_parser.add_argument("--json", action="store_true")

    report_parser = subparsers.add_parser(
        "report-source-gaps",
        help="Summarize what TDXCLI covers versus what still needs ChinaData/Tushare PIT datasets",
    )
    _add_ashare_common_args(report_parser)
    report_parser.add_argument("--json", action="store_true")
    report_parser.add_argument("--dry-run", action="store_true")

    migrate_parser = subparsers.add_parser(
        "migrate-sidecars-to-delta",
        help="Promote existing A-share PIT sidecar parquet files into Delta tables",
    )
    _add_ashare_common_args(migrate_parser)
    migrate_parser.add_argument("--keep-parquet-copy", action="store_true")
    migrate_parser.add_argument("--dry-run", action="store_true")
    migrate_parser.add_argument("--json", action="store_true")

    status_parser = subparsers.add_parser("status", help="Summarize current A-share lake artifacts")
    _add_ashare_common_args(status_parser)
    status_parser.add_argument("--run-id", default=_today_run_id())
    status_parser.add_argument("--json", action="store_true")

    watch_parser = subparsers.add_parser("watch-progress", help="Continuously refresh A-share sidecar sync progress")
    _add_ashare_common_args(watch_parser)
    watch_parser.add_argument("--run-id", default=_today_run_id())
    watch_parser.add_argument("--interval", type=float, default=5.0)
    watch_parser.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted")
    watch_parser.add_argument("--json", action="store_true")
    return parser


def _add_ashare_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", default="DATA/Ashare", help="A-share lake root")


def init_layout(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("init-data-lake-layout.py"),
        "--data-root",
        str(Path(args.data_root)),
        "--assets",
        args.assets,
    ]
    result = _run_step("init-layout", command, dry_run=args.dry_run)
    return {
        "command": "init",
        "data_root": str(Path(args.data_root)),
        "steps": [asdict(result)],
        "failed": result.status == "failed",
    }


def build_local(args: argparse.Namespace) -> dict[str, Any]:
    data_root = Path(args.data_root)
    run_id = args.run_id or _run_id_from_date(args.date)
    steps: list[StepResult] = [_ensure_ashare_dirs(data_root, dry_run=args.dry_run)]

    if args.skip_index:
        steps.append(StepResult("export-index", "skipped", reason="--skip-index"))
    else:
        steps.append(_export_index_step(args, data_root, dry_run=args.dry_run))

    if args.skip_normalize_tdx:
        steps.append(StepResult("normalize-tdx", "skipped", reason="--skip-normalize-tdx"))
    else:
        steps.append(_normalize_tdx_step(args, data_root, run_id, dry_run=args.dry_run))

    if args.skip_backtest_view:
        steps.append(StepResult("build-backtest-view", "skipped", reason="--skip-backtest-view"))
    else:
        steps.append(_backtest_view_step(args, data_root, run_id, dry_run=args.dry_run))

    if args.skip_coverage:
        steps.append(StepResult("coverage", "skipped", reason="--skip-coverage"))
    else:
        steps.append(_coverage_step(data_root, dry_run=args.dry_run))

    if args.skip_readiness:
        steps.append(StepResult("readiness", "skipped", reason="--skip-readiness"))
    else:
        steps.append(_readiness_step(data_root, dry_run=args.dry_run))

    failed = any(step.status == "failed" for step in steps)
    return {
        "command": "build-local",
        "data_root": str(data_root),
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "steps": [asdict(step) for step in steps],
        "artifacts": _artifact_summary(data_root, run_id),
        "failed": failed,
    }


def download_tdx_raw(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("download-ashare-tdx-raw.py"),
        "--tdx-cli",
        args.tdx_cli,
        "--data-root",
        str(Path(args.data_root)),
        "--run-id",
        args.run_id,
        "--symbol-scope",
        args.symbol_scope,
        "--workers",
        str(args.workers),
        "--timeout",
        str(args.timeout),
    ]
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.skip_finance:
        command.append("--skip-finance")
    if args.skip_blocks:
        command.append("--skip-blocks")
    if args.force:
        command.append("--force")
    result = _run_step("download-tdx-raw", command, dry_run=args.dry_run)
    return {
        "command": "download-tdx-raw",
        "data_root": str(Path(args.data_root)),
        "run_id": args.run_id,
        "steps": [asdict(result)],
        "artifacts": _artifact_summary(Path(args.data_root), args.run_id),
        "failed": result.status == "failed",
    }


def sync_chinadata_status(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("ingest-ashare-chinadata-status.py"),
        "--data-root",
        str(Path(args.data_root)),
        "--pause-seconds",
        str(args.pause_seconds),
    ]
    if args.start:
        command.extend(["--start", args.start])
    if args.end:
        command.extend(["--end", args.end])
    if args.symbols:
        command.extend(["--symbols", args.symbols])
    if args.token:
        command.extend(["--token", args.token])
    result = _run_step("sync-chinadata-status", command, dry_run=args.dry_run)
    return {
        "command": "sync-chinadata-status",
        "data_root": str(Path(args.data_root)),
        "steps": [asdict(result)],
        "artifacts": _artifact_summary(Path(args.data_root), _today_run_id()),
        "failed": result.status == "failed",
    }


def sync_chinadata_factors(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("ingest-ashare-chinadata-factors.py"),
        "--data-root",
        str(Path(args.data_root)),
        "--pause-seconds",
        str(args.pause_seconds),
        "--workers",
        str(args.workers),
    ]
    if args.start:
        command.extend(["--start", args.start])
    if args.end:
        command.extend(["--end", args.end])
    if args.symbols:
        command.extend(["--symbols", args.symbols])
    if args.token:
        command.extend(["--token", args.token])
    if args.skip_market_cap:
        command.append("--skip-market-cap")
    if args.skip_industry:
        command.append("--skip-industry")
    if args.skip_share_float:
        command.append("--skip-share-float")
    if args.full_refresh:
        command.append("--full-refresh")
    result = _run_step("sync-chinadata-factors", command, dry_run=args.dry_run)
    return {
        "command": "sync-chinadata-factors",
        "data_root": str(Path(args.data_root)),
        "steps": [asdict(result)],
        "artifacts": _artifact_summary(Path(args.data_root), _today_run_id()),
        "failed": result.status == "failed",
    }


def report_source_gaps(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("report-ashare-source-gaps.py"),
        "--data-root",
        str(Path(args.data_root)),
    ]
    result = _run_step("report-source-gaps", command, dry_run=args.dry_run)
    return {
        "command": "report-source-gaps",
        "data_root": str(Path(args.data_root)),
        "steps": [asdict(result)],
        "artifacts": _artifact_summary(Path(args.data_root), _today_run_id()),
        "failed": result.status == "failed",
    }


def status(args: argparse.Namespace) -> dict[str, Any]:
    data_root = Path(args.data_root)
    return {
        "command": "status",
        "data_root": str(data_root),
        "run_id": args.run_id,
        "artifacts": _artifact_summary(data_root, args.run_id),
        "failed": False,
    }


def watch_progress_cli(args: argparse.Namespace) -> int:
    iterations = max(0, args.iterations)
    seen = 0
    while True:
        snapshot = watch_progress_snapshot(args)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True, indent=2))
        else:
            print(_format_progress_snapshot(snapshot))
        seen += 1
        if iterations and seen >= iterations:
            return 0
        time.sleep(max(0.2, args.interval))


def watch_progress_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    artifacts = _artifact_summary(Path(args.data_root), args.run_id)
    datasets = {}
    for key in ("market_cap_daily_pit", "industry_daily_pit", "share_float_event_pit"):
        item = artifacts.get(key, {})
        datasets[key] = _progress_item(item)
    return {
        "command": "watch-progress",
        "observed_at": datetime.now(tz=UTC).isoformat(),
        "data_root": str(Path(args.data_root)),
        "datasets": datasets,
        "failed": False,
    }


def migrate_sidecars_to_delta(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *_script_command("migrate-ashare-sidecars-to-delta.py"),
        "--data-root",
        str(Path(args.data_root)),
    ]
    if args.keep_parquet_copy:
        command.append("--keep-parquet-copy")
    result = _run_step("migrate-sidecars-to-delta", command, dry_run=args.dry_run)
    return {
        "command": "migrate-sidecars-to-delta",
        "data_root": str(Path(args.data_root)),
        "steps": [asdict(result)],
        "artifacts": _artifact_summary(Path(args.data_root), _today_run_id()),
        "failed": result.status == "failed",
    }


def _ensure_ashare_dirs(data_root: Path, *, dry_run: bool) -> StepResult:
    if dry_run:
        return StepResult(
            "ensure-layout",
            "planned",
            command=["mkdir", *[str(data_root / dirname) for dirname in COMMON_DIRS]],
        )
    for dirname in COMMON_DIRS:
        (data_root / dirname).mkdir(parents=True, exist_ok=True)
    return StepResult("ensure-layout", "completed", command=["mkdir", *COMMON_DIRS])


def _export_index_step(args: argparse.Namespace, data_root: Path, *, dry_run: bool) -> StepResult:
    db_path = Path(args.db_path) if args.db_path else data_root / "Aquant.duckdb"
    if not db_path.exists() and not dry_run:
        status = "failed" if args.require_index else "skipped"
        return StepResult("export-index", status, reason=f"DuckDB cache not found: {db_path}")
    command = [
        *_script_command("export-ashare-index-duckdb-to-lake.py"),
        "--db-path",
        str(db_path),
        "--out-root",
        str(data_root),
    ]
    _append_optional_dates(command, args.start, args.end)
    return _run_step("export-index", command, dry_run=dry_run)


def _normalize_tdx_step(
    args: argparse.Namespace,
    data_root: Path,
    run_id: str,
    *,
    dry_run: bool,
) -> StepResult:
    finance_dir = data_root / "raw" / f"tdx_finance_{run_id}"
    blocks_dir = data_root / "raw" / f"tdx_blocks_{run_id}"
    has_finance = finance_dir.exists()
    has_blocks = blocks_dir.exists()
    if not dry_run and not (has_finance or has_blocks):
        status = "failed" if args.require_tdx_raw else "skipped"
        return StepResult(
            "normalize-tdx",
            status,
            reason=f"no raw TDX dirs for run_id={run_id}",
        )

    command = [
        *_script_command("normalize-ashare-tdx-snapshots.py"),
        "--data-root",
        str(data_root),
        "--run-id",
        run_id,
    ]
    if not dry_run and not has_finance:
        command.append("--skip-finance")
    if not dry_run and not has_blocks:
        command.append("--skip-blocks")
    return _run_step("normalize-tdx", command, dry_run=dry_run)


def _backtest_view_step(
    args: argparse.Namespace,
    data_root: Path,
    run_id: str,
    *,
    dry_run: bool,
) -> StepResult:
    kline_path = data_root / "pit" / "kline_daily_pit.parquet"
    if not kline_path.exists() and not dry_run:
        return StepResult("build-backtest-view", "failed", reason=f"missing {kline_path}")
    command = [
        *_script_command("build-ashare-backtest-view.py"),
        "--data-root",
        str(data_root),
        "--out-parquet",
        str(data_root / "features" / f"backtest_daily_pit_{run_id}.parquet"),
        "--min-listed-days",
        str(args.min_listed_days),
    ]
    _append_optional_dates(command, args.start, args.end)
    if args.symbols:
        command.extend(["--symbols", args.symbols])
    if args.as_of:
        command.extend(["--as-of", args.as_of])
    return _run_step("build-backtest-view", command, dry_run=dry_run)


def _coverage_step(data_root: Path, *, dry_run: bool) -> StepResult:
    kline_path = data_root / "pit" / "kline_daily_pit.parquet"
    if not kline_path.exists() and not dry_run:
        return StepResult("coverage", "failed", reason=f"missing {kline_path}")
    command = [*_script_command("build-ashare-coverage.py"), "--root", str(data_root)]
    return _run_step("coverage", command, dry_run=dry_run)


def _readiness_step(data_root: Path, *, dry_run: bool) -> StepResult:
    command = [
        *_script_command("check-ashare-factor-data-readiness.py"),
        "--data-root",
        str(data_root),
        "--out",
        str(data_root / "_manifest" / "factor_data_readiness.json"),
    ]
    return _run_step("readiness", command, dry_run=dry_run)


def _run_step(name: str, command: Sequence[str], *, dry_run: bool) -> StepResult:
    command_list = [str(part) for part in command]
    if dry_run:
        return StepResult(name, "planned", command=command_list)
    completed = subprocess.run(
        command_list,
        check=False,
        capture_output=True,
        text=True,
    )
    return StepResult(
        name=name,
        status="completed" if completed.returncode == 0 else "failed",
        command=command_list,
        returncode=completed.returncode,
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
    )


def _script_command(script_name: str) -> list[str]:
    uv = shutil.which("uv")
    if uv and (BACKEND_PROJECT / "pyproject.toml").exists():
        return [uv, "run", "--project", str(BACKEND_PROJECT), "python", str(SCRIPT_DIR / script_name)]
    if uv:
        return [uv, "run", "python", str(SCRIPT_DIR / script_name)]
    return [sys.executable, str(SCRIPT_DIR / script_name)]


def _append_optional_dates(command: list[str], start: str | None, end: str | None) -> None:
    if start:
        command.extend(["--start", start])
    if end:
        command.extend(["--end", end])


def _artifact_summary(data_root: Path, run_id: str) -> dict[str, Any]:
    return {
        "kline_daily_pit": _path_item(data_root / "pit" / "kline_daily_pit.parquet"),
        "index_daily_pit": _manifest_item(data_root / "_manifest" / "index_daily_pit.json"),
        "coverage": _manifest_item(data_root / "_manifest" / "coverage.json", compact_coverage=True),
        "tradability_status_pit": _coverage_dataset(data_root, "ashare.tradability_status_pit"),
        "market_cap_daily_pit": _dataset_status(data_root, "ashare.market_cap_daily_pit", "market_cap_daily_pit"),
        "industry_daily_pit": _dataset_status(data_root, "ashare.industry_daily_pit", "industry_daily_pit"),
        "share_float_event_pit": _dataset_status(data_root, "ashare.share_float_event_pit", "share_float_event_pit"),
        "adjustment_factor_pit": _coverage_dataset(data_root, "ashare.adjustment_factor_pit"),
        "corporate_action_pit": _coverage_dataset(data_root, "ashare.corporate_action_pit"),
        "tdx_finance_snapshot": _manifest_item(data_root / "_manifest" / f"tdx_finance_snapshot_{run_id}.json"),
        "tdx_block_membership": _manifest_item(data_root / "_manifest" / f"tdx_block_membership_{run_id}.json"),
        "backtest_daily_features": _manifest_item(data_root / "_manifest" / f"backtest_daily_pit_{run_id}.json"),
        "factor_readiness": _manifest_item(data_root / "_manifest" / "factor_data_readiness.json"),
        "source_gap_report": _manifest_item(data_root / "_manifest" / "source_gap_report.json"),
    }


def _dataset_status(data_root: Path, dataset: str, stem: str) -> dict[str, Any]:
    manifest_path = data_root / "_manifest" / f"{stem}.json"
    manifest_item = _manifest_item(manifest_path)
    coverage_item = _coverage_dataset(data_root, dataset)
    if manifest_item.get("exists"):
        if coverage_item.get("exists"):
            merged = dict(coverage_item)
            merged.update({key: value for key, value in manifest_item.items() if key not in {"exists", "path"}})
            merged["path"] = manifest_item.get("path", coverage_item.get("path"))
            return merged
        return manifest_item
    return coverage_item


def _coverage_dataset(data_root: Path, dataset: str) -> dict[str, Any]:
    coverage = data_root / "_manifest" / "coverage.json"
    if not coverage.exists():
        return {"exists": False, "reason": "coverage manifest missing"}
    try:
        payload = json.loads(coverage.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"exists": False, "reason": f"invalid coverage manifest: {exc}"}
    for item in payload.get("items", []):
        if item.get("dataset") == dataset:
            return _compact_manifest(item) | {"exists": True}
    return {"exists": False, "reason": f"{dataset} not present in coverage"}


def _manifest_item(path: Path, *, compact_coverage: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"exists": False, "path": str(path), "reason": f"invalid JSON: {exc}"}
    if compact_coverage:
        return {
            "exists": True,
            "path": str(path),
            "dataset_count": len(payload.get("items", [])),
            "items": [_compact_manifest(item) for item in payload.get("items", [])],
        }
    return _compact_manifest(payload) | {"exists": True, "path": str(path)}


def _path_item(path: Path) -> dict[str, Any]:
    return {"exists": path.exists(), "path": str(path), "size_bytes": path.stat().st_size if path.exists() else 0}


def _compact_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "dataset",
        "tier",
        "status",
        "overall",
        "path",
        "row_count",
        "symbol_count",
        "block_name_count",
        "start",
        "end",
        "content_hash",
        "can_run_price_factors",
        "can_run_fundamental_factors",
        "can_run_industry_neutral_factors",
        "can_run_market_cap_neutral_factors",
        "blocking_granularities",
        "partial_granularities",
        "storage_format",
        "delta_version",
        "source",
        "sync_strategy_version",
        "sync_mode",
        "fetch_chunk_count",
        "planned_trade_date_count",
        "progress_trade_date_count",
        "missing_trade_date_count",
        "requested_start",
        "effective_start",
        "progress_ann_date_end",
    )
    return {key: payload[key] for key in keys if key in payload}


def _progress_item(item: dict[str, Any]) -> dict[str, Any]:
    progress = item.get("progress_trade_date_count")
    planned = item.get("planned_trade_date_count")
    percent = None
    if isinstance(progress, int) and isinstance(planned, int) and planned > 0:
        percent = round(progress * 100.0 / planned, 2)
    return {
        "dataset": item.get("dataset"),
        "exists": bool(item.get("exists")),
        "sync_mode": item.get("sync_mode"),
        "sync_strategy_version": item.get("sync_strategy_version"),
        "progress_trade_date_count": progress,
        "planned_trade_date_count": planned,
        "progress_percent": percent,
        "progress_ann_date_end": item.get("progress_ann_date_end"),
        "start": item.get("start"),
        "end": item.get("end"),
        "row_count": item.get("row_count"),
        "symbol_count": item.get("symbol_count"),
        "storage_format": item.get("storage_format"),
        "delta_version": item.get("delta_version"),
        "source": item.get("source"),
        "path": item.get("path"),
    }


def _format_progress_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [
        f"[{snapshot['observed_at']}] A-share sidecar progress",
        f"data_root={snapshot['data_root']}",
    ]
    for key, item in snapshot["datasets"].items():
        name = item.get("dataset") or key
        if not item.get("exists"):
            lines.append(f"- {name}: missing")
            continue
        if item.get("progress_percent") is not None:
            lines.append(
                f"- {name}: {item['progress_trade_date_count']}/{item['planned_trade_date_count']} "
                f"({item['progress_percent']}%), end={item.get('end')}, rows={item.get('row_count')}"
            )
            continue
        ann_end = item.get("progress_ann_date_end")
        if ann_end:
            lines.append(f"- {name}: ann_end={ann_end}, end={item.get('end')}, rows={item.get('row_count')}")
            continue
        lines.append(f"- {name}: end={item.get('end')}, rows={item.get('row_count')}")
    return "\n".join(lines)


def _tail(value: str, max_chars: int = 4000) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _run_id_from_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%Y%m%d")


def _today_iso() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _today_run_id() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d")


def _format_summary(summary: dict[str, Any]) -> str:
    lines = [f"{summary['command']}: {'failed' if summary.get('failed') else 'ok'}"]
    if "data_root" in summary:
        lines.append(f"data_root: {summary['data_root']}")
    if "run_id" in summary:
        lines.append(f"run_id: {summary['run_id']}")
    for step in summary.get("steps", []):
        reason = f" ({step['reason']})" if step.get("reason") else ""
        lines.append(f"- {step['name']}: {step['status']}{reason}")
    artifacts = summary.get("artifacts") or {}
    if artifacts:
        ready = [
            name
            for name, item in artifacts.items()
            if isinstance(item, dict) and item.get("exists")
        ]
        if ready:
            lines.append("artifacts: " + ", ".join(ready))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
