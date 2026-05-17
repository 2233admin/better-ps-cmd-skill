"""Dump the TdxW.exe control tree to JSON for offline analysis.

Run **with TdxW open and logged in to a broker account**. The script connects
via pywinauto, optionally sends a hotkey to focus a panel (F4 资金 / F3 持仓 /
F12 委托 / F11 成交), waits, then walks the window children and emits a JSON
tree of (class_name, control_id, automation_id, window_text, rect, depth) for
every descendant control.

The output is the source-of-truth for calibrating
`backend/app/trade/account_reader.py::TDXAccountReader.{get_positions,
get_today_trades, get_today_entrusts, get_balance}`.

Usage:
  python scripts/probe-tdx-controls.py --panel balance --out probe-balance.json
  python scripts/probe-tdx-controls.py --panel positions --out probe-positions.json
  python scripts/probe-tdx-controls.py --panel entrusts --out probe-entrusts.json
  python scripts/probe-tdx-controls.py --panel trades --out probe-trades.json

Panels map to hotkeys per TdxW convention:
  balance   -> F4
  positions -> F3 (持仓)
  entrusts  -> F12 (委托)
  trades    -> F11 (成交)

Add --exe to override path autodetection (default: scan common TdxW dirs).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_EXE_CANDIDATES = (
    r"C:\new_tdx64\TdxW.exe",
    r"C:\zd_zsone\TdxW.exe",
    r"C:\new_tdx\TdxW.exe",
    r"C:\Program Files (x86)\Tdx\TdxW.exe",
)

PANEL_HOTKEYS = {
    "balance": "{F4}",
    "positions": "{F3}",
    "entrusts": "{F12}",
    "trades": "{F11}",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", help="Override TdxW.exe path")
    parser.add_argument(
        "--panel",
        choices=tuple(PANEL_HOTKEYS) + ("none",),
        default="none",
        help="Send a hotkey to focus a panel before probing (default: none)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=1.0,
        help="Seconds to wait after sending the hotkey before snapshotting (default: 1.0)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=12,
        help="Cap recursion depth (default: 12)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=5000,
        help="Cap total nodes captured to avoid runaway dumps (default: 5000)",
    )
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)

    try:
        from pywinauto import Application, keyboard  # type: ignore[import-not-found]
    except ImportError:
        print(
            "pywinauto not installed in this venv. "
            "Run: pip install pywinauto",
            file=sys.stderr,
        )
        return 2

    exe_path = args.exe or _find_tdx_exe()
    if not exe_path:
        print(
            "TdxW.exe not found in any default candidate path. "
            f"Tried: {DEFAULT_EXE_CANDIDATES}. Pass --exe explicitly.",
            file=sys.stderr,
        )
        return 2
    print(f"connecting to {exe_path}")

    try:
        app = Application(backend="win32").connect(path=exe_path)
    except Exception as exc:
        print(
            f"pywinauto connect failed: {exc}\n"
            "Is TdxW.exe running and logged in?",
            file=sys.stderr,
        )
        return 3

    win = app.top_window()
    win.set_focus()
    time.sleep(0.2)

    if args.panel != "none":
        hotkey = PANEL_HOTKEYS[args.panel]
        print(f"sending hotkey {hotkey} for panel '{args.panel}'")
        keyboard.send_keys(hotkey)
        time.sleep(args.wait)

    nodes: list[dict] = []
    _walk(win, depth=0, nodes=nodes, max_depth=args.max_depth, max_nodes=args.max_nodes)

    payload = {
        "exe_path": exe_path,
        "panel": args.panel,
        "wait_seconds": args.wait,
        "max_depth": args.max_depth,
        "max_nodes": args.max_nodes,
        "captured_nodes": len(nodes),
        "truncated": len(nodes) >= args.max_nodes,
        "top_window_title": _safe_text(win),
        "nodes": nodes,
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"wrote {len(nodes)} nodes to {args.out}"
        + (" (truncated)" if payload["truncated"] else "")
    )
    return 0


def _find_tdx_exe() -> str | None:
    for candidate in DEFAULT_EXE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _walk(ctrl, *, depth: int, nodes: list[dict], max_depth: int, max_nodes: int) -> None:
    if depth > max_depth or len(nodes) >= max_nodes:
        return
    try:
        rect = ctrl.rectangle()
        rect_dict = {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
    except Exception:
        rect_dict = None
    try:
        control_id = ctrl.control_id()
    except Exception:
        control_id = None
    try:
        automation_id = ctrl.automation_id()
    except Exception:
        automation_id = None
    nodes.append(
        {
            "depth": depth,
            "class_name": _safe_class(ctrl),
            "control_id": control_id,
            "automation_id": automation_id,
            "window_text": _safe_text(ctrl),
            "rect": rect_dict,
        }
    )
    try:
        children = ctrl.children()
    except Exception:
        children = []
    for child in children:
        if len(nodes) >= max_nodes:
            return
        _walk(child, depth=depth + 1, nodes=nodes, max_depth=max_depth, max_nodes=max_nodes)


def _safe_text(ctrl) -> str:
    try:
        return ctrl.window_text() or ""
    except Exception:
        return ""


def _safe_class(ctrl) -> str:
    try:
        return ctrl.class_name() or ""
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
