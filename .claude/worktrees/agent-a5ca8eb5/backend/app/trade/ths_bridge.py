"""同花顺交易桥接 (easytrader) - 无需QMT的自动交易方案

使用前提:
1. 从招商证券官网下载同花顺版交易客户端
2. 登录后保持同花顺客户端运行
3. 本模块通过 easytrader + pywinauto 操控同花顺窗口下单

安装: pip install easytrader pywinauto
"""

import time
from dataclasses import dataclass

from loguru import logger


@dataclass
class THSConfig:
    """同花顺客户端配置"""
    # 同花顺安装路径 (招商证券版)
    exe_path: str = ""
    # 如果使用通用同花顺，设为空字符串让 easytrader 自动检测
    # 下单确认延迟(秒)
    order_delay: float = 0.5
    # 是否启用 (False = 模拟模式，不真正下单)
    enabled: bool = False


class THSBridge:
    """同花顺自动交易桥接

    方案优势:
    - 不需要 QMT 权限
    - 不需要资金门槛
    - 支持可转债 T+0

    方案限制:
    - 单笔下单约 1-2 秒
    - 需要同花顺客户端保持运行
    - 界面弹窗可能导致失败
    """

    def __init__(self, config: THSConfig | None = None):
        self.config = config or THSConfig()
        self.user = None
        self.connected = False

    def connect(self) -> bool:
        """连接同花顺客户端"""
        if not self.config.enabled:
            logger.info("THS bridge: disabled (simulation mode)")
            return False

        try:
            import easytrader

            self.user = easytrader.use("ths")

            if self.config.exe_path:
                self.user.connect(self.config.exe_path)
            else:
                self.user.connect()

            self.connected = True
            logger.info("THS bridge: connected to 同花顺")

            # 验证连接
            balance = self.user.balance
            logger.info(f"  账户余额: {balance}")
            return True

        except ImportError:
            logger.error("easytrader not installed: pip install easytrader")
            return False
        except Exception as e:
            logger.error(f"THS connect failed: {e}")
            return False

    def buy(self, code: str, price: float, volume: int) -> dict:
        """买入

        Args:
            code: 证券代码 (如 "128025", "123049")
            price: 委托价格
            volume: 委托数量 (可转债单位:张)
        """
        if not self.connected or not self.user:
            logger.warning("THS not connected, simulating buy")
            return {"status": "simulated", "code": code, "price": price, "volume": volume}

        try:
            result = self.user.buy(code, price=price, amount=volume)
            logger.info(f"THS buy: {code} @ {price} x {volume} -> {result}")
            time.sleep(self.config.order_delay)
            return {"status": "submitted", "result": result}
        except Exception as e:
            logger.error(f"THS buy error: {e}")
            return {"status": "error", "error": str(e)}

    def sell(self, code: str, price: float, volume: int) -> dict:
        """卖出"""
        if not self.connected or not self.user:
            logger.warning("THS not connected, simulating sell")
            return {"status": "simulated", "code": code, "price": price, "volume": volume}

        try:
            result = self.user.sell(code, price=price, amount=volume)
            logger.info(f"THS sell: {code} @ {price} x {volume} -> {result}")
            time.sleep(self.config.order_delay)
            return {"status": "submitted", "result": result}
        except Exception as e:
            logger.error(f"THS sell error: {e}")
            return {"status": "error", "error": str(e)}

    def cancel(self, entrust_no: str) -> bool:
        """撤单"""
        if not self.connected or not self.user:
            return False
        try:
            self.user.cancel_entrust(entrust_no)
            logger.info(f"THS cancel: {entrust_no}")
            return True
        except Exception as e:
            logger.error(f"THS cancel error: {e}")
            return False

    def get_balance(self) -> dict:
        """查询资金"""
        if not self.connected or not self.user:
            return {}
        try:
            return self.user.balance
        except Exception as e:
            logger.error(f"THS balance error: {e}")
            return {}

    def get_positions(self) -> list[dict]:
        """查询持仓"""
        if not self.connected or not self.user:
            return []
        try:
            return self.user.position
        except Exception as e:
            logger.error(f"THS position error: {e}")
            return []

    def get_today_entrusts(self) -> list[dict]:
        """查询今日委托"""
        if not self.connected or not self.user:
            return []
        try:
            return self.user.today_entrusts
        except Exception as e:
            logger.error(f"THS entrusts error: {e}")
            return []

    def get_today_trades(self) -> list[dict]:
        """查询今日成交"""
        if not self.connected or not self.user:
            return []
        try:
            return self.user.today_trades
        except Exception as e:
            logger.error(f"THS trades error: {e}")
            return []


class TDXAutoBridge:
    """通达信自动交易桥接 (pywinauto)

    直接操控你现有的招商证券通达信客户端下单
    适用于不想安装同花顺的场景

    原理: 通过 pywinauto 找到通达信委托窗口，模拟键盘输入
    """

    def __init__(self):
        self.app = None
        self.connected = False
        self.tdx_path = r"C:\zd_zsone\TdxW.exe"

    def connect(self) -> bool:
        """连接到已运行的通达信"""
        try:
            from pywinauto import Application

            # 连接到已运行的 TdxW
            self.app = Application(backend="win32").connect(path=self.tdx_path)
            self.connected = True
            logger.info("TDX auto bridge: connected to TdxW.exe")
            return True
        except Exception as e:
            logger.error(f"TDX auto bridge connect failed: {e}")
            logger.info("  确保通达信正在运行并已登录")
            return False

    def _open_order_panel(self):
        """打开委托面板 (F6 或对应快捷键)"""
        if not self.connected:
            return
        try:
            main_win = self.app.top_window()
            main_win.type_keys("{F6}")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Open order panel error: {e}")

    def buy(self, code: str, price: float, volume: int) -> dict:
        """通过通达信委托窗口买入

        注意: 此方法依赖界面，不同通达信版本可能需要调整
        """
        if not self.connected:
            return {"status": "not_connected"}

        try:
            from pywinauto import keyboard

            self._open_order_panel()
            time.sleep(0.3)

            # 模拟键入: 代码 -> Tab -> 价格 -> Tab -> 数量 -> Enter
            keyboard.send_keys(code, pause=0.05)
            time.sleep(0.2)
            keyboard.send_keys("{TAB}")
            time.sleep(0.2)
            keyboard.send_keys(str(price), pause=0.05)
            time.sleep(0.2)
            keyboard.send_keys("{TAB}")
            time.sleep(0.2)
            keyboard.send_keys(str(volume), pause=0.05)
            time.sleep(0.2)
            keyboard.send_keys("{ENTER}")
            time.sleep(0.3)
            # 确认对话框
            keyboard.send_keys("{ENTER}")

            logger.info(f"TDX buy: {code} @ {price} x {volume}")
            return {"status": "submitted", "code": code}

        except Exception as e:
            logger.error(f"TDX buy error: {e}")
            return {"status": "error", "error": str(e)}

    def sell(self, code: str, price: float, volume: int) -> dict:
        """通过通达信委托窗口卖出"""
        if not self.connected:
            return {"status": "not_connected"}

        try:
            from pywinauto import keyboard

            self._open_order_panel()
            time.sleep(0.3)

            # 先切换到卖出标签 (一般是 F2)
            keyboard.send_keys("{F2}")
            time.sleep(0.3)

            keyboard.send_keys(code, pause=0.05)
            time.sleep(0.2)
            keyboard.send_keys("{TAB}")
            keyboard.send_keys(str(price), pause=0.05)
            keyboard.send_keys("{TAB}")
            keyboard.send_keys(str(volume), pause=0.05)
            keyboard.send_keys("{ENTER}")
            time.sleep(0.3)
            keyboard.send_keys("{ENTER}")

            logger.info(f"TDX sell: {code} @ {price} x {volume}")
            return {"status": "submitted", "code": code}

        except Exception as e:
            logger.error(f"TDX sell error: {e}")
            return {"status": "error", "error": str(e)}
