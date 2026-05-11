"""账户数据读取 - 从交易软件窗口统一读取持仓/委托/成交/余额"""

from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class AccountReader(Protocol):
    """账户数据读取协议"""

    name: str

    def get_balance(self) -> dict:
        """资金余额"""
        ...

    def get_positions(self) -> list[dict]:
        """持仓列表"""
        ...

    def get_today_trades(self) -> list[dict]:
        """今日成交"""
        ...

    def get_today_entrusts(self) -> list[dict]:
        """今日委托"""
        ...

    def is_available(self) -> bool:
        ...


class THSAccountReader:
    """同花顺 - 通过 easytrader 读取"""

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

    def get_balance(self) -> dict:
        self._ensure_bridge()
        return self._bridge.get_balance()

    def get_positions(self) -> list[dict]:
        self._ensure_bridge()
        return self._bridge.get_positions()

    def get_today_trades(self) -> list[dict]:
        self._ensure_bridge()
        return self._bridge.get_today_trades()

    def get_today_entrusts(self) -> list[dict]:
        self._ensure_bridge()
        return self._bridge.get_today_entrusts()


class TDXAccountReader:
    """通达信 - 通过 pywinauto 读取委托窗口

    读取方式: 发送快捷键切换到对应面板，解析列表控件文本
    F4=资金, F3=持仓/委托/成交
    """

    name = "tdx"

    def __init__(self, exe_path: str = r"C:\zd_zsone\TdxW.exe"):
        self._exe_path = exe_path
        self._app = None

    def _ensure_connected(self):
        if self._app is not None:
            return
        try:
            from pywinauto import Application
            self._app = Application(backend="win32").connect(path=self._exe_path)
        except Exception as e:
            logger.error(f"TDX account reader connect failed: {e}")
            self._app = None

    def is_available(self) -> bool:
        try:
            self._ensure_connected()
            return self._app is not None
        except Exception:
            return False

    def get_balance(self) -> dict:
        """通过 F4 查看资金信息"""
        self._ensure_connected()
        if self._app is None:
            return {}
        try:
            import time
            from pywinauto import keyboard
            win = self._app.top_window()
            win.set_focus()
            keyboard.send_keys("{F4}")
            time.sleep(0.5)
            # 尝试读取资金面板的 Static 控件文本
            texts = []
            for ctrl in win.children():
                try:
                    t = ctrl.window_text()
                    if t:
                        texts.append(t)
                except Exception:
                    pass
            # 简单解析: 寻找含 "可用" "余额" 等关键词的文本
            balance = {"raw_texts": texts}
            for t in texts:
                if "可用" in t or "余额" in t:
                    # 尝试提取数字
                    import re
                    nums = re.findall(r"[\d.]+", t)
                    if nums:
                        balance["available"] = float(nums[0])
            return balance
        except Exception as e:
            logger.error(f"TDX get_balance error: {e}")
            return {}

    def get_positions(self) -> list[dict]:
        """读取持仓列表 - 需要通达信显示持仓面板"""
        # TODO: 通达信持仓面板的控件结构因版本而异，需实际调试
        logger.warning("TDX position reading not yet calibrated for this version")
        return []

    def get_today_trades(self) -> list[dict]:
        logger.warning("TDX trades reading not yet calibrated")
        return []

    def get_today_entrusts(self) -> list[dict]:
        logger.warning("TDX entrusts reading not yet calibrated")
        return []


class CTPAccountReader:
    """CTP 账户读取 — 通过 openctp API 直接查询，无需窗口"""

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

    def get_balance(self) -> dict:
        self._ensure_bridge()
        return self._bridge.get_balance()

    def get_positions(self) -> list[dict]:
        self._ensure_bridge()
        return self._bridge.get_positions()

    def get_today_trades(self) -> list[dict]:
        return []  # CTP 需要 QryTrade，后续加

    def get_today_entrusts(self) -> list[dict]:
        return []  # CTP 需要 QryOrder，后续加


class AccountReaderManager:
    """账户数据读取管理器 - 管理多个 AccountReader"""

    def __init__(self):
        self.readers: dict[str, AccountReader] = {}
        self.primary: str | None = None

    def register(self, reader: AccountReader, primary: bool = False):
        self.readers[reader.name] = reader
        if primary or self.primary is None:
            self.primary = reader.name
        logger.info(f"AccountReader registered: {reader.name}" +
                     (" (primary)" if primary else ""))

    def get_primary(self) -> AccountReader | None:
        if self.primary and self.primary in self.readers:
            return self.readers[self.primary]
        return None

    def get_balance(self) -> dict:
        reader = self.get_primary()
        if reader and reader.is_available():
            return reader.get_balance()
        return {}

    def get_positions(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return reader.get_positions()
        return []

    def get_today_trades(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return reader.get_today_trades()
        return []

    def get_today_entrusts(self) -> list[dict]:
        reader = self.get_primary()
        if reader and reader.is_available():
            return reader.get_today_entrusts()
        return []

    def get_status(self) -> list[dict]:
        return [
            {"name": r.name, "available": r.is_available(), "primary": r.name == self.primary}
            for r in self.readers.values()
        ]


# 全局单例
_reader_manager: AccountReaderManager | None = None


def get_account_reader_manager() -> AccountReaderManager:
    global _reader_manager
    if _reader_manager is None:
        _reader_manager = AccountReaderManager()
    return _reader_manager
