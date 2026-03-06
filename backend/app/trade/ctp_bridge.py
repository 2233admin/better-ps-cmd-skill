"""OpenCTP 交易桥接 — 统一 CTP 接口对接多券商

支持模式:
- sim: openctp 7x24 仿真环境 (开发调试)
- tora: 华鑫证券 (A股实盘)
- xtp: 中泰证券 (A股实盘)
- emt: 东方财富证券 (A股实盘)

架构: 应用层只对接 CTPAPI，底层通过 DLL 适配器转发到各券商系统。
切换券商不改代码，只换前置地址。
"""

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger


# openctp 仿真环境前置地址 (7x24)
SIM_MD_FRONT = "tcp://121.36.146.182:20004"
SIM_TD_FRONT = "tcp://121.36.146.182:20002"

# openctp 仿真账号 (免费注册 https://openctp.cn)
SIM_BROKER_ID = ""
SIM_USER_ID = ""
SIM_PASSWORD = ""


class CTPStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LOGGED_IN = "logged_in"
    ERROR = "error"


@dataclass
class CTPConfig:
    """CTP 连接配置"""
    md_front: str = SIM_MD_FRONT
    td_front: str = SIM_TD_FRONT
    broker_id: str = SIM_BROKER_ID
    user_id: str = SIM_USER_ID
    password: str = SIM_PASSWORD
    app_id: str = "openctp_quant"
    auth_code: str = ""
    flow_path: str = "./data/ctp_flow/"


class CTPTraderSpi:
    """交易回调处理"""

    def __init__(self, bridge: "CTPBridge"):
        self._bridge = bridge
        self._event = threading.Event()

    # --- 连接/登录回调 ---

    def OnFrontConnected(self):
        logger.info("CTP Trader: 前置连接成功")
        self._bridge._status = CTPStatus.CONNECTED
        # 自动登录
        self._bridge._do_login()

    def OnFrontDisconnected(self, nReason: int):
        logger.warning(f"CTP Trader: 连接断开 reason={nReason}")
        self._bridge._status = CTPStatus.DISCONNECTED

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"CTP 登录失败: {pRspInfo.ErrorMsg}")
            self._bridge._status = CTPStatus.ERROR
        else:
            logger.info(f"CTP 登录成功: TradingDay={pRspUserLogin.TradingDay}")
            self._bridge._status = CTPStatus.LOGGED_IN
            self._bridge._trading_day = pRspUserLogin.TradingDay
            self._bridge._front_id = pRspUserLogin.FrontID
            self._bridge._session_id = pRspUserLogin.SessionID
        self._event.set()

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"CTP 认证失败: {pRspInfo.ErrorMsg}")
        else:
            logger.info("CTP 认证成功，开始登录")
            self._bridge._do_login()

    # --- 下单回调 ---

    def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID != 0:
            logger.error(f"CTP 报单失败: {pRspInfo.ErrorMsg}")
            if self._bridge._order_callback:
                self._bridge._order_callback({
                    "status": "rejected",
                    "error": pRspInfo.ErrorMsg,
                    "request_id": nRequestID,
                })

    def OnRtnOrder(self, pOrder):
        """报单回报 — 委托状态变化"""
        if pOrder is None:
            return
        order_info = {
            "order_ref": pOrder.OrderRef,
            "instrument": pOrder.InstrumentID,
            "direction": "buy" if pOrder.Direction == "0" else "sell",
            "price": pOrder.LimitPrice,
            "volume": pOrder.VolumeTotalOriginal,
            "traded": pOrder.VolumeTraded,
            "status": pOrder.OrderStatus,
            "status_msg": pOrder.StatusMsg,
        }
        logger.info(f"CTP 报单回报: {order_info}")
        if self._bridge._order_callback:
            self._bridge._order_callback(order_info)

    def OnRtnTrade(self, pTrade):
        """成交回报"""
        if pTrade is None:
            return
        trade_info = {
            "instrument": pTrade.InstrumentID,
            "direction": "buy" if pTrade.Direction == "0" else "sell",
            "price": pTrade.Price,
            "volume": pTrade.Volume,
            "trade_id": pTrade.TradeID,
            "time": pTrade.TradeTime,
        }
        logger.info(f"CTP 成交回报: {trade_info}")
        if self._bridge._trade_callback:
            self._bridge._trade_callback(trade_info)

    # --- 查询回调 ---

    def OnRspQryTradingAccount(self, pTradingAccount, pRspInfo, nRequestID, bIsLast):
        if pTradingAccount:
            self._bridge._account_data = {
                "balance": pTradingAccount.Balance,
                "available": pTradingAccount.Available,
                "frozen_margin": pTradingAccount.FrozenMargin,
                "frozen_commission": pTradingAccount.FrozenCommission,
                "position_profit": pTradingAccount.PositionProfit,
                "close_profit": pTradingAccount.CloseProfit,
            }
        if bIsLast:
            self._event.set()

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast):
        if pInvestorPosition and pInvestorPosition.InstrumentID:
            self._bridge._positions_data.append({
                "code": pInvestorPosition.InstrumentID,
                "direction": "long" if pInvestorPosition.PosiDirection == "2" else "short",
                "volume": pInvestorPosition.Position,
                "available": pInvestorPosition.Position - pInvestorPosition.ShortFrozen - pInvestorPosition.LongFrozen,
                "avg_price": pInvestorPosition.OpenCost / pInvestorPosition.Position if pInvestorPosition.Position > 0 else 0,
                "position_profit": pInvestorPosition.PositionProfit,
            })
        if bIsLast:
            self._event.set()


class CTPBridge:
    """OpenCTP 交易桥接

    用法:
        bridge = CTPBridge(CTPConfig(md_front=..., td_front=...))
        bridge.connect()
        bridge.buy("600000", 9.80, 100)
        positions = bridge.get_positions()
    """

    def __init__(self, config: CTPConfig | None = None):
        self.config = config or CTPConfig()
        self._api = None
        self._spi = None
        self._status = CTPStatus.DISCONNECTED
        self._request_id = 0
        self._order_ref = 0
        self._trading_day = ""
        self._front_id = 0
        self._session_id = 0
        self._order_callback = None
        self._trade_callback = None
        self._account_data = {}
        self._positions_data = []

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _next_order_ref(self) -> str:
        self._order_ref += 1
        return str(self._order_ref)

    def connect(self) -> bool:
        """连接 CTP 前置"""
        try:
            from openctp_ctp import tdapi

            os.makedirs(self.config.flow_path, exist_ok=True)

            self._api = tdapi.CThostFtdcTraderApi.CreateFtdcTraderApi(
                self.config.flow_path
            )
            self._spi = CTPTraderSpi(self)
            self._api.RegisterSpi(self._spi)
            self._api.SubscribePublicTopic(tdapi.THOST_TERT_QUICK)
            self._api.SubscribePrivateTopic(tdapi.THOST_TERT_QUICK)
            self._api.RegisterFront(self.config.td_front)

            self._status = CTPStatus.CONNECTING
            self._api.Init()

            # 等待登录完成 (最多10秒)
            self._spi._event.clear()
            self._spi._event.wait(timeout=10)

            if self._status == CTPStatus.LOGGED_IN:
                logger.info("CTP Bridge: 连接并登录成功")
                return True
            else:
                logger.error(f"CTP Bridge: 登录超时/失败, status={self._status}")
                return False

        except ImportError:
            logger.error("openctp-ctp not installed: pip install openctp-ctp")
            return False
        except Exception as e:
            logger.error(f"CTP connect error: {e}")
            return False

    def _do_login(self):
        """发送登录请求"""
        from openctp_ctp import tdapi

        req = tdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.config.broker_id
        req.UserID = self.config.user_id
        req.Password = self.config.password
        self._api.ReqUserLogin(req, self._next_request_id())

    def disconnect(self):
        if self._api:
            self._api.Release()
            self._api = None
        self._status = CTPStatus.DISCONNECTED

    @property
    def connected(self) -> bool:
        return self._status == CTPStatus.LOGGED_IN

    # --- 交易接口 ---

    def buy(self, code: str, price: float, volume: int) -> dict:
        """买入股票"""
        return self._insert_order(code, price, volume, direction="0")

    def sell(self, code: str, price: float, volume: int) -> dict:
        """卖出股票"""
        return self._insert_order(code, price, volume, direction="1")

    def _insert_order(self, code: str, price: float, volume: int, direction: str) -> dict:
        """提交报单"""
        if not self.connected:
            return {"status": "error", "error": "CTP not connected"}

        from openctp_ctp import tdapi

        req = tdapi.CThostFtdcInputOrderField()
        req.BrokerID = self.config.broker_id
        req.InvestorID = self.config.user_id
        req.InstrumentID = code
        req.OrderRef = self._next_order_ref()
        req.Direction = direction
        req.CombOffsetFlag = "0"  # 开仓 (股票用开仓)
        req.CombHedgeFlag = "1"   # 投机
        req.LimitPrice = price
        req.VolumeTotalOriginal = volume
        req.OrderPriceType = tdapi.THOST_FTDC_OPT_LimitPrice
        req.TimeCondition = tdapi.THOST_FTDC_TC_GFD  # 当日有效
        req.VolumeCondition = tdapi.THOST_FTDC_VC_AV  # 任何数量
        req.ContingentCondition = tdapi.THOST_FTDC_CC_Immediately
        req.MinVolume = 1

        ret = self._api.ReqOrderInsert(req, self._next_request_id())
        if ret == 0:
            dir_str = "买入" if direction == "0" else "卖出"
            logger.info(f"CTP {dir_str}: {code} @ {price} x {volume}")
            return {"status": "submitted", "order_ref": req.OrderRef}
        else:
            return {"status": "error", "error": f"ReqOrderInsert returned {ret}"}

    def cancel_order(self, code: str, order_ref: str) -> dict:
        """撤单"""
        if not self.connected:
            return {"status": "error", "error": "CTP not connected"}

        from openctp_ctp import tdapi

        req = tdapi.CThostFtdcInputOrderActionField()
        req.BrokerID = self.config.broker_id
        req.InvestorID = self.config.user_id
        req.InstrumentID = code
        req.OrderRef = order_ref
        req.FrontID = self._front_id
        req.SessionID = self._session_id
        req.ActionFlag = tdapi.THOST_FTDC_AF_Delete

        ret = self._api.ReqOrderAction(req, self._next_request_id())
        return {"status": "submitted" if ret == 0 else "error"}

    # --- 查询接口 ---

    def get_balance(self) -> dict:
        """查询资金"""
        if not self.connected:
            return {}

        from openctp_ctp import tdapi

        self._account_data = {}
        self._spi._event.clear()

        req = tdapi.CThostFtdcQryTradingAccountField()
        req.BrokerID = self.config.broker_id
        req.InvestorID = self.config.user_id
        self._api.ReqQryTradingAccount(req, self._next_request_id())

        self._spi._event.wait(timeout=5)
        return self._account_data

    def get_positions(self) -> list[dict]:
        """查询持仓"""
        if not self.connected:
            return []

        from openctp_ctp import tdapi

        self._positions_data = []
        self._spi._event.clear()

        req = tdapi.CThostFtdcQryInvestorPositionField()
        req.BrokerID = self.config.broker_id
        req.InvestorID = self.config.user_id
        self._api.ReqQryInvestorPosition(req, self._next_request_id())

        self._spi._event.wait(timeout=5)
        return self._positions_data

    def on_order(self, callback):
        """注册报单回调"""
        self._order_callback = callback

    def on_trade(self, callback):
        """注册成交回调"""
        self._trade_callback = callback
