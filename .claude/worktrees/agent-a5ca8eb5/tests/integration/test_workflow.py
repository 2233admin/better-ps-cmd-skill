"""集成测试 - 测试完整工作流程"""

import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch, AsyncMock

from app.strategy.engine import StrategyEngine, StrategyState
from app.strategy.t_strategy import TStrategy, TStrategyConfig
from app.strategy.backtest import VectorBacktester
from app.trade.executor import OrderExecutor, OrderStatus


class TestStrategyWorkflow:
    """测试策略工作流程"""

    @pytest.fixture
    def setup_workflow(self):
        """设置完整的工作流环境"""
        engine = StrategyEngine()
        executor = OrderExecutor(mode="paper")
        return engine, executor

    def test_full_strategy_lifecycle(self, setup_workflow):
        """测试策略完整生命周期"""
        engine, executor = setup_workflow

        # 1. 列出可用策略
        strategies = engine.list_strategies()
        assert len(strategies) == 5
        strategy_names = [s["name"] for s in strategies]
        assert "momentum_breakout" in strategy_names
        assert "t_trading" in strategy_names

        # 2. 启动策略
        result = engine.start_strategy("momentum_breakout", {
            "min_confidence": 0.6,
            "vol_threshold": 2.5,
        })
        assert result is True
        assert engine.strategies["momentum_breakout"].state == StrategyState.RUNNING

        # 3. 获取策略状态
        status = engine.get_strategy_status("momentum_breakout")
        assert status["state"] == "running"
        assert status["params"]["min_confidence"] == 0.6

        # 4. 停止策略
        engine.stop_strategy("momentum_breakout")
        assert engine.strategies["momentum_breakout"].state == StrategyState.STOPPED

    @patch("app.strategy.engine.generate_signals")
    def test_quote_to_signal_to_order_flow(self, mock_generate_signals, setup_workflow):
        """测试从行情到信号到订单的完整流程"""
        engine, executor = setup_workflow

        # 创建模拟信号
        mock_signal = MagicMock()
        mock_signal.code = "000001"
        mock_signal.direction = "buy"
        mock_signal.price = 10.5
        mock_signal.volume = 100
        mock_signal.strategy = "momentum_breakout"
        mock_signal.confidence = 0.8
        mock_generate_signals.return_value = [mock_signal]

        # 启动策略
        engine.start_strategy("momentum_breakout", {"min_confidence": 0.6})

        # 推送行情
        quotes = [{
            "code": "000001",
            "price": 10.5,
            "open": 10.0,
            "high": 10.8,
            "low": 10.2,
            "vol": 50000,
        }]

        engine.on_quotes(quotes)

        # 验证信号生成被调用
        mock_generate_signals.assert_called_once()

        # 验证订单被提交
        assert len(executor.orders) == 1
        order = list(executor.orders.values())[0]
        assert order.code == "000001"
        assert order.direction == "buy"
        assert order.status == OrderStatus.FILLED

    def test_multiple_strategies_concurrent(self, setup_workflow):
        """测试多个策略并发运行"""
        engine, executor = setup_workflow

        # 启动多个策略
        engine.start_strategy("momentum_breakout", {"min_confidence": 0.6})
        engine.start_strategy("mean_reversion", {"min_confidence": 0.5})
        engine.start_strategy("t_trading", {"min_confidence": 0.7})

        # 验证所有策略都在运行
        assert engine.strategies["momentum_breakout"].state == StrategyState.RUNNING
        assert engine.strategies["mean_reversion"].state == StrategyState.RUNNING
        assert engine.strategies["t_trading"].state == StrategyState.RUNNING

        # 停止其中一个
        engine.stop_strategy("momentum_breakout")
        assert engine.strategies["momentum_breakout"].state == StrategyState.STOPPED
        assert engine.strategies["mean_reversion"].state == StrategyState.RUNNING

    def test_strategy_error_recovery(self, setup_workflow):
        """测试策略错误和恢复"""
        engine, executor = setup_workflow

        with patch("app.strategy.engine.generate_signals") as mock_gen:
            mock_gen.side_effect = Exception("Strategy error")

            engine.start_strategy("momentum_breakout", {})

            quotes = [{"code": "000001", "price": 10.0}]
            engine.on_quotes(quotes)

            # 策略应该进入错误状态
            assert engine.strategies["momentum_breakout"].state == StrategyState.ERROR
            assert "Strategy error" in engine.strategies["momentum_breakout"].error

        # 重启策略
        engine.start_strategy("momentum_breakout", {})
        assert engine.strategies["momentum_breakout"].state == StrategyState.RUNNING
        assert engine.strategies["momentum_breakout"].error == ""


class TestTStrategyIntegration:
    """测试T策略集成"""

    def test_t_strategy_with_executor(self):
        """测试T策略与执行器集成"""
        t_strategy = TStrategy(TStrategyConfig(min_ticks=5))
        executor = OrderExecutor(mode="paper")

        # 设置持仓和目标
        t_strategy.set_positions({"000001": 1000})
        t_strategy.set_targets([{"code": "000001", "mode": "long_t"}])

        # 积累tick数据
        now = time.time()
        for i in range(10):
            quote = {
                "code": "000001",
                "price": 10.0 - i * 0.01,
                "vol": 1000 + i * 100,
                "amount": 10000.0 + i * 1000,
                "cur_vol": 50,
                "bid1": 9.99,
                "ask1": 10.01,
                "last_close": 10.0,
            }
            t_strategy.on_quotes([quote])

        # 触发买入信号的价格
        quote = {
            "code": "000001",
            "price": 9.75,  # 跌破VWAP 1.2%
            "vol": 2000,
            "amount": 20000.0,
            "cur_vol": 30,  # 缩量
            "bid1": 9.74,
            "ask1": 9.76,
            "last_close": 10.0,
        }

        signals = t_strategy.on_quotes([quote])

        if signals:
            # 提交订单
            for sig in signals:
                result = executor.submit_order(
                    code=sig.code,
                    direction=sig.direction,
                    price=sig.price,
                    volume=sig.volume,
                    strategy=sig.strategy,
                )
                assert "error" not in result
                assert result["status"] == "filled"

    def test_t_strategy_position_sync(self):
        """测试T策略持仓同步"""
        t_strategy = TStrategy()
        executor = OrderExecutor(mode="paper")

        # 模拟执行器持仓
        executor.positions["000001"] = MagicMock()
        executor.positions["000001"].volume = 1000

        # 同步持仓到T策略
        t_strategy.set_positions({p.code: p.volume for p in executor.positions.values()})

        assert t_strategy.positions["000001"] == 1000


class TestBacktestIntegration:
    """测试回测集成"""

    def test_end_to_end_backtest(self):
        """测试端到端回测流程"""
        import polars as pl
        from datetime import datetime, timedelta
        import numpy as np

        # 1. 生成测试数据
        n = 200
        dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(n)]
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.01, n)
        prices = 100 * np.exp(np.cumsum(returns))

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices * (1 + np.random.normal(0, 0.001, n)),
            "high": prices * (1 + abs(np.random.normal(0, 0.005, n))),
            "low": prices * (1 - abs(np.random.normal(0, 0.005, n))),
            "close": prices,
            "volume": np.random.randint(1000, 10000, n),
        })

        # 2. 生成简单均线策略信号
        signals_list = [0] * n
        for i in range(20, n):
            if prices[i] > np.mean(prices[i-20:i]):  # 价格上穿均线
                signals_list[i] = 1
            elif prices[i] < np.mean(prices[i-20:i]):  # 价格下穿均线
                signals_list[i] = -1

        signals = pl.DataFrame({
            "datetime": dates,
            "signal": signals_list,
        })

        # 3. 运行回测
        backtester = VectorBacktester(
            initial_capital=100000,
            commission=0.0005,
            slippage=0.001,
        )
        result = backtester.run(data, signals)

        # 4. 验证结果
        assert result.total_trades > 0
        assert len(result.equity_curve) == n
        assert result.total_return is not None
        assert result.sharpe_ratio is not None
        assert result.max_drawdown >= 0

        # 5. 结果可序列化
        result_dict = result.to_dict()
        assert "total_return" in result_dict
        assert "sharpe_ratio" in result_dict

    def test_backtest_with_different_parameters(self):
        """测试不同参数的回测"""
        import polars as pl
        from datetime import datetime, timedelta

        dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(100)]
        prices = [100.0 + i * 0.1 for i in range(100)]  # 稳定上涨趋势

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000] * 100,
        })

        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [1] + [0] * 99,  # 买入并持有
        })

        # 测试不同手续费
        for commission in [0.0001, 0.0005, 0.001]:
            backtester = VectorBacktester(
                initial_capital=100000,
                commission=commission,
                slippage=0.001,
            )
            result = backtester.run(data, signals)
            assert result.total_trades == 0  # 只有买入没有卖出


class TestRiskManagementIntegration:
    """测试风控集成"""

    def test_risk_check_blocks_order(self):
        """测试风控阻止订单"""
        executor = OrderExecutor(mode="paper")

        with patch.object(executor.risk_manager, "check_order") as mock_check:
            mock_check.return_value = {"passed": False, "reason": "Risk limit exceeded"}

            result = executor.submit_order(
                code="000001",
                direction="buy",
                price=10.0,
                volume=10000,
                strategy="test",
            )

            assert "error" in result
            assert "Risk limit exceeded" in result["error"]
            assert len(executor.orders) == 0

    def test_risk_check_allows_order(self):
        """测试风控允许订单"""
        executor = OrderExecutor(mode="paper")

        with patch.object(executor.risk_manager, "check_order") as mock_check:
            mock_check.return_value = {"passed": True}

            result = executor.submit_order(
                code="000001",
                direction="buy",
                price=10.0,
                volume=100,
                strategy="test",
            )

            assert "error" not in result
            assert result["status"] == "filled"


class TestPositionManagement:
    """测试持仓管理"""

    def test_position_update_on_buy(self):
        """测试买入更新持仓"""
        executor = OrderExecutor(mode="paper")

        # 第一次买入
        executor.submit_order("000001", "buy", 10.0, 100, "test")
        assert "000001" in executor.positions
        assert executor.positions["000001"].volume == 100
        assert executor.positions["000001"].avg_price == 10.0

        # 第二次买入
        executor.submit_order("000001", "buy", 11.0, 100, "test")
        assert executor.positions["000001"].volume == 200
        # 平均成本 = (10*100 + 11*100) / 200 = 10.5
        assert abs(executor.positions["000001"].avg_price - 10.5) < 0.01

    def test_position_update_on_sell(self):
        """测试卖出更新持仓"""
        executor = OrderExecutor(mode="paper")

        # 先买入
        executor.submit_order("000001", "buy", 10.0, 100, "test")
        assert executor.positions["000001"].volume == 100

        # 部分卖出
        executor.submit_order("000001", "sell", 11.0, 50, "test")
        assert executor.positions["000001"].volume == 50

        # 全部卖出
        executor.submit_order("000001", "sell", 11.0, 50, "test")
        assert "000001" not in executor.positions

    def test_position_pnl_calculation(self):
        """测试持仓盈亏计算"""
        executor = OrderExecutor(mode="paper")

        # 买入
        executor.submit_order("000001", "buy", 10.0, 100, "test")

        # 更新价格
        executor.update_prices([{"code": "000001", "price": 11.0}])

        pos = executor.positions["000001"]
        assert pos.current_price == 11.0
        assert pos.pnl == 100.0  # (11 - 10) * 100
        assert pos.pnl_pct == 0.1  # (11 - 10) / 10


class TestOrderLifecycle:
    """测试订单生命周期"""

    def test_order_creation(self):
        """测试订单创建"""
        executor = OrderExecutor(mode="paper")

        result = executor.submit_order(
            code="000001",
            direction="buy",
            price=10.0,
            volume=100,
            strategy="test",
        )

        assert "order_id" in result
        assert result["code"] == "000001"
        assert result["direction"] == "buy"
        assert result["status"] == "filled"

        order = executor.orders[result["order_id"]]
        assert order.status == OrderStatus.FILLED
        assert order.filled_price == 10.0
        assert order.filled_volume == 100

    def test_order_cancellation(self):
        """测试订单取消（仅对非paper模式有意义）"""
        executor = OrderExecutor(mode="paper")

        result = executor.submit_order("000001", "buy", 10.0, 100, "test")
        order_id = result["order_id"]

        # paper模式下订单立即成交，无法取消
        cancel_result = executor.cancel_order(order_id)
        assert cancel_result is False

    def test_get_pending_orders(self):
        """测试获取待处理订单"""
        executor = OrderExecutor(mode="paper")

        # paper模式下订单立即成交，没有待处理订单
        executor.submit_order("000001", "buy", 10.0, 100, "test")

        pending = executor.get_pending_orders()
        assert len(pending) == 0

    def test_get_pnl_summary(self):
        """测试盈亏汇总"""
        executor = OrderExecutor(mode="paper")

        # 买入并更新价格
        executor.submit_order("000001", "buy", 10.0, 100, "test")
        executor.update_prices([{"code": "000001", "price": 11.0}])

        summary = executor.get_pnl_summary()
        assert summary["total_pnl"] == 100.0
        assert summary["position_count"] == 1
        assert summary["today_trades"] == 1


class TestCryptoSupport:
    """测试加密货币支持"""

    def test_okx_code_format(self):
        """测试OKX代码格式"""
        executor = OrderExecutor(mode="paper")

        result = executor.submit_order(
            code="BTC-USDT",
            direction="buy",
            price=50000.0,
            volume=0.1,
            strategy="crypto",
        )

        assert "error" not in result
        order = executor.orders[result["order_id"]]
        assert order.market == 99  # OKX市场代码

    def test_crypto_position_update(self):
        """测试加密货币持仓更新"""
        executor = OrderExecutor(mode="paper")

        executor.submit_order("BTC-USDT", "buy", 50000.0, 0.1, "crypto")
        assert "BTC-USDT" in executor.positions
        assert executor.positions["BTC-USDT"].volume == 0.1
