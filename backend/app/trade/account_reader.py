"""账户数据读取 - 从交易软件窗口/API 统一读取持仓/委托/成交/余额.

Three sources are supported, each implementing `AccountReader`:
- `TDXAccountReader` (pywinauto on TdxW.exe)
- `THSAccountReader` (easytrader on 同花顺)
- `CTPAccountReader` (openctp API)

The EasyXT/QMT execution boundary is HTTP-only (see
`backend/app/trading/adapters/qmt/bridge.py`); a forthcoming
`QMTHTTPAccountReader` will hit the same bridge's readback endpoints.

`MultiSourceAccountReader` composes multiple readers with primary/secondary
priority and per-field fallback, so reconciliation can prefer TDX while
cross-checking against EasyXT/QMT.

Field contracts (`PositionRecord`, `EntrustRecord`, `TradeRecord`,
`BalanceRecord`) are the unified shape every reader must emit. Each record
carries `source: str` so downstream reconciliation knows which reader
produced it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from loguru import logger


@dataclass(frozen=True)
class PositionRecord:
    """Single-symbol position snapshot."""

    symbol: str
    quantity: int
    available_quantity: int
    avg_cost: float
    market_value: float
    source: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EntrustRecord:
    """Today entrust (order request) record."""

    request_id: str
    symbol: str
    side: str
    qty: int
    price: float
    status: str
    timestamp: datetime | None
    source: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TradeRecord:
    """Today trade (fill) record."""

    request_id: str
    symbol: str
    side: str
    fill_qty: int
    fill_price: float
    timestamp: datetime | None
    source: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BalanceRecord:
    """Account-level cash balance snapshot."""

    available_cash: float
    frozen_cash: float
    total_assets: float
    source: str
    raw: dict = field(default_factory=dict)


@runtime_checkable
class AccountReader(Protocol):
    """账户数据读取协议. Implementations must return typed records (not raw dicts)."""

    name: str

    def is_available(self) -> bool: ...

    def get_balance(self) -> BalanceRecord | None: ...

    def get_positions(self) -> list[PositionRecord]: ...

    def get_today_trades(self) -> list[TradeRecord]: ...

    def get_today_entrusts(self) -> list[EntrustRecord]: ...


class THSAccountReader:
    """同花顺 - 通过 easytrader 读取."""

    name = "ths"

    def __init__(self):
        self._bridge = None

    def _ensure_bridge(self):
        if self._bridge is None:
            from .ths_bridge import THSBridge, THSConfig

            self._bridge = THSBridge(THSConfig(enabled=True))
            self._bridge.connect()

    def is_available(self) -> bool:
        try:
            self._ensure_bridge()
            return self._bridge.connected
        except Exception:
            return False

    def get_balance(self) -> BalanceRecord | None:
        self._ensure_bridge()
        raw = self._bridge.get_balance() or {}
        if not raw:
            return None
        return BalanceRecord(
            available_cash=float(raw.get("available", 0.0) or 0.0),
            frozen_cash=float(raw.get("frozen", 0.0) or 0.0),
            total_assets=float(raw.get("total_assets", raw.get("balance", 0.0)) or 0.0),
            source=self.name,
            raw=raw,
        )

    def get_positions(self) -> list[PositionRecord]:
        self._ensure_bridge()
        rows = self._bridge.get_positions() or []
        return [
            PositionRecord(
                symbol=str(row.get("symbol", row.get("code", ""))),
                quantity=int(row.get("quantity", row.get("volume", 0)) or 0),
                available_quantity=int(row.get("available", row.get("enable_amount", 0)) or 0),
                avg_cost=float(row.get("avg_cost", row.get("cost_price", 0.0)) or 0.0),
                market_value=float(row.get("market_value", 0.0) or 0.0),
                source=self.name,
                raw=row,
            )
            for row in rows
        ]

    def get_today_trades(self) -> list[TradeRecord]:
        self._ensure_bridge()
        rows = self._bridge.get_today_trades() or []
        return [_dict_to_trade(row, source=self.name) for row in rows]

    def get_today_entrusts(self) -> list[EntrustRecord]:
        self._ensure_bridge()
        rows = self._bridge.get_today_entrusts() or []
        return [_dict_to_entrust(row, source=self.name) for row in rows]


class TDXAccountReader:
    """通达信 - 通过 pywinauto 读取持仓/委托/成交/资金面板.

    Control-tree calibration is gated on a real `scripts/probe-tdx-controls.py`
    dump against a running TdxW.exe with a logged-in broker account. Until
    that calibration lands, the position/trade/entrust readers report
    `_uncalibrated()` so MultiSourceAccountReader can fall back to other
    sources rather than silently returning empty lists.
    """

    name = "tdx"

    def __init__(self, exe_path: str | None = None):
        self._exe_path = exe_path or _autodetect_tdx_exe()
        self._app = None
        self._calibrated = False

    def _ensure_connected(self):
        if self._app is not None:
            return
        if not self._exe_path:
            logger.warning("TDX exe path not configured and autodetect failed")
            return
        try:
            from pywinauto import Application

            self._app = Application(backend="win32").connect(path=self._exe_path)
        except Exception as exc:
            logger.error(f"TDX account reader connect failed: {exc}")
            self._app = None

    def is_available(self) -> bool:
        try:
            self._ensure_connected()
            return self._app is not None
        except Exception:
            return False

    def get_balance(self) -> BalanceRecord | None:
        self._ensure_connected()
        if self._app is None:
            return None
        try:
            import re
            import time

            from pywinauto import keyboard

            win = self._app.top_window()
            win.set_focus()
            keyboard.send_keys("{F4}")
            time.sleep(0.5)
            texts = []
            for ctrl in win.children():
                try:
                    text = ctrl.window_text()
                    if text:
                        texts.append(text)
                except Exception:
                    continue
            available = 0.0
            for text in texts:
                if "可用" in text or "余额" in text:
                    nums = re.findall(r"[\d.]+", text)
                    if nums:
                        available = float(nums[0])
                        break
            return BalanceRecord(
                available_cash=available,
                frozen_cash=0.0,
                total_assets=0.0,
                source=self.name,
                raw={"texts": texts},
            )
        except Exception as exc:
            logger.error(f"TDX get_balance error: {exc}")
            return None

    def get_positions(self) -> list[PositionRecord]:
        self._uncalibrated("positions")
        return []

    def get_today_trades(self) -> list[TradeRecord]:
        self._uncalibrated("trades")
        return []

    def get_today_entrusts(self) -> list[EntrustRecord]:
        self._uncalibrated("entrusts")
        return []

    def _uncalibrated(self, panel: str) -> None:
        if self._calibrated:
            return
        logger.warning(
            "TDX %s reader not calibrated for this TdxW build; run "
            "scripts/probe-tdx-controls.py --panel %s to capture the control tree "
            "(tracked in XAR-414)",
            panel,
            panel,
        )


class CTPAccountReader:
    """CTP 账户读取 - 通过 openctp API 直接查询，无需窗口."""

    name = "ctp"

    def __init__(self):
        self._bridge = None

    def _ensure_bridge(self):
        if self._bridge is None:
            from .ctp_bridge import CTPBridge

            self._bridge = CTPBridge()
            self._bridge.connect()

    def is_available(self) -> bool:
        try:
            self._ensure_bridge()
            return self._bridge.connected
        except Exception:
            return False

    def get_balance(self) -> BalanceRecord | None:
        self._ensure_bridge()
        raw = self._bridge.get_balance() or {}
        if not raw:
            return None
        return BalanceRecord(
            available_cash=float(raw.get("available", 0.0) or 0.0),
            frozen_cash=float(raw.get("frozen", 0.0) or 0.0),
            total_assets=float(raw.get("balance", 0.0) or 0.0),
            source=self.name,
            raw=raw,
        )

    def get_positions(self) -> list[PositionRecord]:
        self._ensure_bridge()
        rows = self._bridge.get_positions() or []
        return [
            PositionRecord(
                symbol=str(row.get("symbol", "")),
                quantity=int(row.get("position", 0) or 0),
                available_quantity=int(row.get("available", 0) or 0),
                avg_cost=float(row.get("avg_price", 0.0) or 0.0),
                market_value=float(row.get("market_value", 0.0) or 0.0),
                source=self.name,
                raw=row,
            )
            for row in rows
        ]

    def get_today_trades(self) -> list[TradeRecord]:
        return []

    def get_today_entrusts(self) -> list[EntrustRecord]:
        return []


class MultiSourceAccountReader:
    """Compose multiple readers with primary/secondary fallback.

    Per-method behavior: try the primary source first; fall back to the next
    available reader if primary returns None / [] or raises. Records carry
    `source=<reader-name>` so reconciliation knows where each row came from.

    This composer does NOT cross-validate. Cross-validation
    (compare expected vs actual positions for reconciliation pass/fail) lives
    in `app.trading.adapters.qmt.reconciliation` and friends; this reader's
    job is just to surface the best-available snapshot.
    """

    name = "multi"

    def __init__(self, readers: list[AccountReader]):
        if not readers:
            raise ValueError("MultiSourceAccountReader requires at least one reader")
        self._readers = list(readers)

    def is_available(self) -> bool:
        return any(self._safe_call(reader.is_available, default=False) for reader in self._readers)

    def get_balance(self) -> BalanceRecord | None:
        for reader in self._readers:
            if not self._safe_call(reader.is_available, default=False):
                continue
            result = self._safe_call(reader.get_balance, default=None)
            if result is not None:
                return result
        return None

    def get_positions(self) -> list[PositionRecord]:
        return self._first_non_empty(lambda r: r.get_positions())

    def get_today_trades(self) -> list[TradeRecord]:
        return self._first_non_empty(lambda r: r.get_today_trades())

    def get_today_entrusts(self) -> list[EntrustRecord]:
        return self._first_non_empty(lambda r: r.get_today_entrusts())

    def _first_non_empty(self, getter) -> list:
        for reader in self._readers:
            if not self._safe_call(reader.is_available, default=False):
                continue
            result = self._safe_call(lambda r=reader: getter(r), default=[])
            if result:
                return result
        return []

    @staticmethod
    def _safe_call(fn, *, default):
        try:
            return fn()
        except Exception as exc:
            logger.warning(f"MultiSourceAccountReader call failed: {exc}")
            return default


class AccountReaderManager:
    """账户数据读取管理器 - 管理多个 AccountReader."""

    def __init__(self):
        self.readers: dict[str, AccountReader] = {}
        self.primary: str | None = None

    def register(self, reader: AccountReader, primary: bool = False):
        self.readers[reader.name] = reader
        if primary or self.primary is None:
            self.primary = reader.name
        logger.info(
            f"AccountReader registered: {reader.name}"
            + (" (primary)" if primary else "")
        )

    def get_primary(self) -> AccountReader | None:
        if self.primary and self.primary in self.readers:
            return self.readers[self.primary]
        return None

    def get_balance(self) -> dict:
        reader = self.get_primary()
        if reader and reader.is_available():
            record = reader.get_balance()
            return asdict(record) if record else {}
        return {}

    def get_positions(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return [asdict(record) for record in reader.get_positions()]
        return []

    def get_today_trades(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return [asdict(record) for record in reader.get_today_trades()]
        return []

    def get_today_entrusts(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return [asdict(record) for record in reader.get_today_entrusts()]
        return []

    def get_status(self) -> list[dict]:
        return [
            {"name": r.name, "available": r.is_available(), "primary": r.name == self.primary}
            for r in self.readers.values()
        ]


_TDX_EXE_CANDIDATES = (
    r"C:\new_tdx64\TdxW.exe",
    r"C:\zd_zsone\TdxW.exe",
    r"C:\new_tdx\TdxW.exe",
    r"C:\Program Files (x86)\Tdx\TdxW.exe",
)


def _autodetect_tdx_exe() -> str | None:
    from pathlib import Path

    for candidate in _TDX_EXE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _dict_to_trade(row: dict, *, source: str) -> TradeRecord:
    ts = row.get("timestamp") or row.get("time")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = None
    return TradeRecord(
        request_id=str(row.get("request_id", row.get("entrust_no", ""))),
        symbol=str(row.get("symbol", row.get("code", ""))),
        side=str(row.get("side", row.get("operation", ""))),
        fill_qty=int(row.get("fill_qty", row.get("volume", 0)) or 0),
        fill_price=float(row.get("fill_price", row.get("price", 0.0)) or 0.0),
        timestamp=ts if isinstance(ts, datetime) else None,
        source=source,
        raw=row,
    )


def _dict_to_entrust(row: dict, *, source: str) -> EntrustRecord:
    ts = row.get("timestamp") or row.get("time")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = None
    return EntrustRecord(
        request_id=str(row.get("request_id", row.get("entrust_no", ""))),
        symbol=str(row.get("symbol", row.get("code", ""))),
        side=str(row.get("side", row.get("operation", ""))),
        qty=int(row.get("qty", row.get("volume", 0)) or 0),
        price=float(row.get("price", 0.0) or 0.0),
        status=str(row.get("status", "")),
        timestamp=ts if isinstance(ts, datetime) else None,
        source=source,
        raw=row,
    )


_reader_manager: AccountReaderManager | None = None


def get_account_reader_manager() -> AccountReaderManager:
    global _reader_manager
    if _reader_manager is None:
        _reader_manager = AccountReaderManager()
    return _reader_manager
