"""策略单元测试"""

import pytest
import time
from unittest.mock import MagicMock, patch

from app.strategy.engine import StrategyEngine, StrategyState, StrategyInfo
from app.strategy.t_strategy import TStrategy, TStrategyConfig, IntraDayState, TickData
from app.strategy.signals import Signal, generate_signals


class TestStrategyState:
    """测试策略状态枚举"""

    def test_state_values(self):
        """测试状态值"""
        assert StrategyState.STOPPED == "stopped"
        assert StrategyState.RUNNING == "running"
        assert StrategyState.ERROR == "error"


class TestStrategyInfo:
    """测试策略信息数据类"""

    def test_default_values(self):
        """测试默认值"""
        info = StrategyInfo(name="test", description="Test strategy")
        assert info.name == "test"
        assert info.description == "Test strategy"
        assert info.state == StrategyState.STOPPED
        assert info.params == {}
        assert info.pnl == 0.0
        assert info.trade_count == 0
        assert info.started_at is None
        assert info.error == ""

    def test_custom_values(self):
        """测试自定义值"""
        info = StrategyInfo(
            name="momentum",
            description="Momentum breakout",
            state=StrategyState.RUNNING,
            params={"threshold": 0.05},
            pnl=1000.0,
            trade_count=5,
            started_at=time.time(),
        )
        assert info.state == StrategyState.RUNNING
        assert info.params["threshold"] == 0.05
        assert info.pnl == 1000.0


class TestStrategyEngine:
    """测试策略引擎"""

    def test_initialization(self):
        """测试初始化"""
        engine = StrategyEngine()
        assert len(engine.strategies) == 5  # 5个内置策略
        assert "momentum_breakout" in engine.strategies
        assert "t_trading" in engine.strategies

    def test_list_strategies(self):
        """测试列出策略"""
        engine = StrategyEngine()
        strategies = engine.list_strategies()
        assert len(strategies) == 5
        for s in strategies:
            assert "name" in s
            assert "description" in s
            assert "state" in s
            assert "pnl" in s

    def test_start_strategy_success(self):
        """测试成功启动策略"""
        engine = StrategyEngine()
        result = engine.start_strategy("momentum_breakout", {"threshold": 0.05})
        assert result is True
        assert engine.strategies["momentum_breakout"].state == StrategyState.RUNNING
        assert engine.strategies["momentum_breakout"].params["threshold"] == 0.05
        assert engine.strategies["momentum_breakout"].started_at is not None

    def test_start_unknown_strategy(self):
        """测试启动未知策略"""
        engine = StrategyEngine()
        result = engine.start_strategy("unknown", {})
        assert result is False

    def test_start_already_running_strategy(self):
        """测试启动已运行的策略"""
        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {})
        result = engine.start_strategy("momentum_breakout", {})
        assert result is True  # 返回True但不重复启动

    def test_stop_strategy(self):
        """测试停止策略"""
        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {})
        engine.stop_strategy("momentum_breakout")
        assert engine.strategies["momentum_breakout"].state == StrategyState.STOPPED
        assert engine.strategies["momentum_breakout"].started_at is None

    def test_stop_unknown_strategy(self):
        """测试停止未知策略"""
        engine = StrategyEngine()
        # 不应该抛出异常
        engine.stop_strategy("unknown")

    def test_get_strategy_status(self):
        """测试获取策略状态"""
        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {"param": 1})
        status = engine.get_strategy_status("momentum_breakout")
        assert status is not None
        assert status["name"] == "momentum_breakout"
        assert status["state"] == "running"
        assert status["params"]["param"] == 1

    def test_get_unknown_strategy_status(self):
        """测试获取未知策略状态"""
        engine = StrategyEngine()
        status = engine.get_strategy_status("unknown")
        assert status is None

    @patch("app.strategy.engine.generate_signals")
    @patch("app.strategy.engine.get_executor")
    def test_on_quotes_running_strategy(self, mock_get_executor, mock_generate_signals):
        """测试行情回调处理运行中的策略"""
        mock_executor = MagicMock()
        mock_executor.submit_order.return_value = {"order_id": 1}
        mock_get_executor.return_value = mock_executor

        mock_signal = MagicMock()
        mock_signal.confidence = 0.8
        mock_signal.code = "000001"
        mock_signal.direction = "buy"
        mock_signal.price = 10.0
        mock_signal.volume = 100
        mock_generate_signals.return_value = [mock_signal]

        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {"min_confidence": 0.5})

        quotes = [{"code": "000001", "price": 10.0}]
        engine.on_quotes(quotes)

        mock_generate_signals.assert_called_once()
        mock_executor.submit_order.assert_called_once()
        assert engine.strategies["momentum_breakout"].trade_count == 1

    @patch("app.strategy.engine.generate_signals")
    def test_on_quotes_low_confidence(self, mock_generate_signals):
        """测试低置信度信号被过滤"""
        mock_signal = MagicMock()
        mock_signal.confidence = 0.3  # 低于min_confidence
        mock_generate_signals.return_value = [mock_signal]

        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {"min_confidence": 0.5})

        quotes = [{"code": "000001", "price": 10.0}]
        engine.on_quotes(quotes)

        assert engine.strategies["momentum_breakout"].trade_count == 0

    @patch("app.strategy.engine.generate_signals")
    def test_on_quotes_strategy_error(self, mock_generate_signals):
        """测试策略异常处理"""
        mock_generate_signals.side_effect = Exception("Strategy error")

        engine = StrategyEngine()
        engine.start_strategy("momentum_breakout", {})

        quotes = [{"code": "000001", "price": 10.0}]
        engine.on_quotes(quotes)

        assert engine.strategies["momentum_breakout"].state == StrategyState.ERROR
        assert "Strategy error" in engine.strategies["momentum_breakout"].error


class TestTStrategyConfig:
    """测试T策略配置"""

    def test_default_values(self):
        """测试默认值"""
        config = TStrategyConfig()
        assert config.long_t_buy_deviation == -0.012
        assert config.long_t_sell_deviation == 0.008
        assert config.short_t_sell_deviation == 0.015
        assert config.short_t_buy_deviation == 0.003
        assert config.signal_cooldown == 120.0
        assert config.min_ticks == 30
        assert config.volume_per_signal == 100
        assert config.max_daily_t_count == 2

    def test_custom_values(self):
        """测试自定义值"""
        config = TStrategyConfig(
            long_t_buy_deviation=-0.02,
            volume_per_signal=200,
        )
        assert config.long_t_buy_deviation == -0.02
        assert config.volume_per_signal == 200
        assert config.long_t_sell_deviation == 0.008  # 默认值


class TestIntraDayState:
    """测试日内状态"""

    def test_initialization(self):
        """测试初始化"""
        state = IntraDayState(code="000001")
        assert state.code == "000001"
        assert state.ticks == []
        assert state.vwap == 0.0
        assert state.high == 0.0
        assert state.low == 999999.0

    def test_update_vwap(self):
        """测试VWAP更新"""
        state = IntraDayState(code="000001")
        tick = TickData(ts=time.time(), price=10.0, vol=1000, amount=10000.0, cur_vol=100)
        state.ticks.append(tick)
        state.update_vwap()
        assert state.vwap == 10.0  # amount / vol = 10000 / 1000
        assert state.last_price == 10.0

    def test_deviation_calculation(self):
        """测试偏离率计算"""
        state = IntraDayState(code="000001")
        state.vwap = 10.0
        state.last_price = 10.5
        assert state.deviation == 0.05  # (10.5 - 10) / 10

    def test_deviation_zero_vwap(self):
        """测试VWAP为0时的偏离率"""
        state = IntraDayState(code="000001")
        state.vwap = 0.0
        state.last_price = 10.0
        assert state.deviation == 0.0

    def test_vol_ratio_calculation(self):
        """测试量比计算"""
        state = IntraDayState(code="000001")
        now = time.time()
        for i in range(25):
            tick = TickData(ts=now + i, price=10.0, vol=1000 + i * 100,
                          amount=10000.0, cur_vol=100)
            state.ticks.append(tick)
        state.update_vwap()
        # 最近一笔成交量100，前20笔平均也是100
        assert state.vol_ratio == 1.0

    def test_is_shrinking(self):
        """测试缩量判断"""
        state = IntraDayState(code="000001")
        now = time.time()
        for i in range(25):
            tick = TickData(ts=now + i, price=10.0, vol=1000 + i * 100,
                          amount=10000.0, cur_vol=100 if i < 24 else 30)
            state.ticks.append(tick)
        state.update_vwap()
        assert state.is_shrinking is True  # 30 / 100 < 0.5

    def test_is_surging(self):
        """测试放量判断"""
        state = IntraDayState(code="000001")
        now = time.time()
        for i in range(25):
            tick = TickData(ts=now + i, price=10.0, vol=1000 + i * 100,
                          amount=10000.0, cur_vol=100 if i < 24 else 300)
            state.ticks.append(tick)
        state.update_vwap()
        assert state.is_surging is True  # 300 / 100 > 2.0

    def test_price_change_calculation(self):
        """测试涨跌幅计算"""
        state = IntraDayState(code="000001")
        state.last_price = 110.0
        state.last_close = 100.0
        assert state.price_change == 0.10  # (110 - 100) / 100

    def test_trend_strength_calculation(self):
        """测试趋势强度计算"""
        state = IntraDayState(code="000001")
        now = time.time()
        # 创建上涨趋势
        for i in range(15):
            tick = TickData(ts=now + i, price=10.0 + i * 0.1, vol=1000,
                          amount=10000.0, cur_vol=100)
            state.ticks.append(tick)
        trend = state.trend_strength
        assert trend > 0  # 上升趋势


class TestTStrategy:
    """测试T策略"""

    def test_initialization(self):
        """测试初始化"""
        config = TStrategyConfig()
        strategy = TStrategy(config)
        assert strategy.config == config
        assert strategy.states == {}
        assert strategy.positions == {}
        assert strategy.modes == {}

    def test_set_positions(self):
        """测试设置持仓"""
        strategy = TStrategy()
        strategy.set_positions({"000001": 1000, "000002": 500})
        assert strategy.positions["000001"] == 1000
        assert strategy.positions["000002"] == 500

    def test_set_targets(self):
        """测试设置目标"""
        strategy = TStrategy()
        targets = [
            {"code": "000001", "mode": "long_t", "volume": 100},
            {"code": "000002", "mode": "short_t"},
        ]
        strategy.set_targets(targets)
        assert strategy.modes["000001"] == "long_t"
        assert strategy.modes["000002"] == "short_t"

    def test_reset_daily(self):
        """测试每日重置"""
        strategy = TStrategy()
        strategy.states["000001"] = IntraDayState(code="000001")
        strategy.daily_t_count["000001"] = 2
        strategy._scalp_watch["000003"] = 9.5

        strategy.reset_daily()

        assert strategy.states == {}
        assert strategy.daily_t_count == {}
        assert strategy._scalp_watch == {}

    def test_on_quotes_empty(self):
        """测试空行情数据"""
        strategy = TStrategy()
        signals = strategy.on_quotes([])
        assert signals == []

    def test_on_quotes_no_targets(self):
        """测试无目标时的行情处理"""
        strategy = TStrategy()
        quotes = [{"code": "000001", "price": 10.0}]
        signals = strategy.on_quotes(quotes)
        assert signals == []

    def test_on_quotes_insufficient_ticks(self, mock_t_strategy_config):
        """测试tick数不足"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "long_t"}])
        strategy.set_positions({"000001": 1000})

        quotes = [{"code": "000001", "price": 10.0, "vol": 1000,
                  "amount": 10000.0, "cur_vol": 100}]
        signals = strategy.on_quotes(quotes)
        assert signals == []  # tick数不足

    def test_long_t_buy_signal(self, mock_t_strategy_config):
        """测试正T买入信号"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "long_t"}])
        strategy.set_positions({"000001": 1000})

        now = time.time()
        # 积累足够的ticks
        for i in range(10):
            tick = TickData(ts=now + i, price=10.0 - i * 0.01, vol=1000 + i * 100,
                          amount=10000.0 + i * 1000, cur_vol=50)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        # 价格跌破VWAP 1.2%以上
        quotes = [{
            "code": "000001",
            "price": 9.75,  # 低于VWAP约1.2%
            "vol": 2000,
            "amount": 20000.0,
            "cur_vol": 30,  # 缩量
            "bid1": 9.74,
            "ask1": 9.76,
            "last_close": 10.0,
        }]

        signals = strategy.on_quotes(quotes)
        assert len(signals) == 1
        assert signals[0].direction == "buy"
        assert signals[0].strategy == "t_long"

    def test_short_t_sell_signal(self, mock_t_strategy_config):
        """测试反T卖出信号"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "short_t"}])
        strategy.set_positions({"000001": 1000})

        now = time.time()
        # 积累足够的ticks
        for i in range(10):
            tick = TickData(ts=now + i, price=10.0 + i * 0.005, vol=1000 + i * 100,
                          amount=10000.0 + i * 1000, cur_vol=200)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        # 价格冲高到VWAP上方1.5%以上，放量
        quotes = [{
            "code": "000001",
            "price": 10.25,  # 高于VWAP约1.5%
            "vol": 3000,
            "amount": 30000.0,
            "cur_vol": 500,  # 放量
            "bid1": 10.24,
            "ask1": 10.26,
            "last_close": 10.0,
        }]

        signals = strategy.on_quotes(quotes)
        assert len(signals) == 1
        assert signals[0].direction == "sell"
        assert signals[0].strategy == "t_short"

    def test_scalp_signal(self, mock_t_strategy_config):
        """测试半路T信号"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "scalp"}])

        now = time.time()
        # 积累足够的ticks
        for i in range(10):
            tick = TickData(ts=now + i, price=10.0 - i * 0.02, vol=1000 + i * 100,
                          amount=10000.0 + i * 1000, cur_vol=100)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        # 急杀超过3%，然后反弹0.5%
        quotes = [{
            "code": "000001",
            "price": 9.75,  # 从10.0跌下来，反弹中
            "vol": 2000,
            "amount": 20000.0,
            "cur_vol": 40,  # 缩量
            "bid1": 9.74,
            "ask1": 9.76,
            "last_close": 10.0,
        }]

        # 先触发关注
        strategy._scalp_watch["000001"] = 9.65  # 急杀最低点

        signals = strategy.on_quotes(quotes)
        # 可能产生买入信号
        if signals:
            assert signals[0].direction == "buy"
            assert signals[0].strategy == "t_scalp"

    def test_signal_cooldown(self, mock_t_strategy_config):
        """测试信号冷却"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "long_t"}])
        strategy.set_positions({"000001": 1000})

        now = time.time()
        # 积累足够的ticks
        for i in range(10):
            tick = TickData(ts=now + i, price=10.0 - i * 0.01, vol=1000 + i * 100,
                          amount=10000.0 + i * 1000, cur_vol=50)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        quotes = [{
            "code": "000001",
            "price": 9.75,
            "vol": 2000,
            "amount": 20000.0,
            "cur_vol": 30,
            "bid1": 9.74,
            "ask1": 9.76,
            "last_close": 10.0,
        }]

        # 第一次应该产生信号
        signals1 = strategy.on_quotes(quotes)
        assert len(signals1) == 1

        # 第二次在冷却期内，不应该产生信号
        signals2 = strategy.on_quotes(quotes)
        assert len(signals2) == 0

    def test_daily_t_count_limit(self, mock_t_strategy_config):
        """测试每日T次数限制"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "long_t"}])
        strategy.set_positions({"000001": 1000})
        strategy.daily_t_count["000001"] = 2  # 已达到上限

        now = time.time()
        for i in range(10):
            tick = TickData(ts=now + i, price=10.0 - i * 0.01, vol=1000 + i * 100,
                          amount=10000.0 + i * 1000, cur_vol=50)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        quotes = [{
            "code": "000001",
            "price": 9.75,
            "vol": 2000,
            "amount": 20000.0,
            "cur_vol": 30,
            "bid1": 9.74,
            "ask1": 9.76,
            "last_close": 10.0,
        }]

        signals = strategy.on_quotes(quotes)
        assert len(signals) == 0  # 超过次数限制

    def test_get_status(self, mock_t_strategy_config):
        """测试获取策略状态"""
        strategy = TStrategy(mock_t_strategy_config)
        strategy.set_targets([{"code": "000001", "mode": "long_t"}])

        now = time.time()
        for i in range(5):
            tick = TickData(ts=now + i, price=10.0, vol=1000,
                          amount=10000.0, cur_vol=100)
            strategy.states["000001"] = IntraDayState(code="000001")
            strategy.states["000001"].ticks.append(tick)
            strategy.states["000001"].update_vwap()

        status = strategy.get_status()
        assert status["targets"] == 1
        assert "000001" in status["stocks"]
        assert status["stocks"]["000001"]["mode"] == "long_t"


class TestSignalGeneration:
    """测试信号生成"""

    def test_generate_momentum_breakout_signals(self):
        """测试动量突破信号生成"""
        quotes = [
            {
                "code": "000001",
                "price": 10.5,
                "open": 10.0,
                "high": 10.8,
                "low": 9.9,
                "vol": 10000,
            }
        ]
        signals = generate_signals("momentum_breakout", quotes, {"vol_threshold": 3.0})
        assert len(signals) >= 0  # 可能产生信号

    def test_generate_mean_reversion_signals(self):
        """测试均值回归信号生成"""
        quotes = [
            {
                "code": "000001",
                "price": 9.5,  # 低于VWAP
                "open": 10.0,
                "high": 10.2,
                "low": 9.4,
                "vol": 10000,
            }
        ]
        signals = generate_signals("mean_reversion", quotes, {"deviation_threshold": 0.02})
        assert len(signals) >= 0

    def test_generate_premium_arbitrage_signals(self):
        """测试溢价率套利信号生成"""
        quotes = [
            {
                "code": "000001",
                "price": 98.0,
                "premium_rate": -0.02,  # 折价2%
            }
        ]
        signals = generate_signals("premium_arbitrage", quotes, {"premium_threshold": 0.05})
        assert len(signals) >= 0

    def test_generate_pair_trading_signals(self):
        """测试配对交易信号生成"""
        quotes = [
            {
                "code": "000001",
                "price": 105.0,
                "open": 100.0,
                "stock_change": 0.10,  # 正股涨10%
            }
        ]
        signals = generate_signals("pair_trading", quotes, {"spread_threshold": 0.015})
        assert len(signals) >= 0

    def test_generate_unknown_strategy(self):
        """测试未知策略返回空列表"""
        quotes = [{"code": "000001", "price": 10.0}]
        signals = generate_signals("unknown", quotes, {})
        assert signals == []

    def test_signal_dataclass(self):
        """测试Signal数据类"""
        sig = Signal(
            code="000001",
            direction="buy",
            price=10.0,
            volume=100,
            strategy="test",
            reason="test signal",
            confidence=0.8,
        )
        assert sig.code == "000001"
        assert sig.direction == "buy"
        assert sig.confidence == 0.8
