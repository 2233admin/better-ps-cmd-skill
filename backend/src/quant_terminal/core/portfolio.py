
"""投资组合管理模块

支持多品种仓位管理、风险控制、资金分配等功能
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum
import polars as pl
from loguru import logger


class PositionDirection(Enum):
    """持仓方向"""
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Position:
    """持仓数据类"""
    code: str
    direction: PositionDirection
    volume: int
    avg_price: float
    open_time: datetime
    margin: float = 0.0  # 期货保证金
    unrealized_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.direction == PositionDirection.LONG

    @property
    def is_short(self) -> bool:
        return self.direction == PositionDirection.SHORT

    @property
    def is_flat(self) -> bool:
        return self.direction == PositionDirection.FLAT

    def update_unrealized_pnl(self, current_price: float, contract_multiplier: float = 1.0) -> float:
        """更新浮动盈亏"""
        if self.direction == PositionDirection.LONG:
            self.unrealized_pnl = (current_price - self.avg_price) * self.volume * contract_multiplier
        elif self.direction == PositionDirection.SHORT:
            self.unrealized_pnl = (self.avg_price - current_price) * self.volume * contract_multiplier
        else:
            self.unrealized_pnl = 0.0
        return self.unrealized_pnl

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'code': self.code,
            'direction': self.direction.name,
            'volume': self.volume,
            'avg_price': self.avg_price,
            'open_time': self.open_time.isoformat(),
            'margin': self.margin,
            'unrealized_pnl': self.unrealized_pnl
        }


@dataclass
class PortfolioConfig:
    """投资组合配置"""
    initial_capital: float = 1_000_000.0
    max_position_pct: float = 0.8  # 最大仓位比例
    max_single_position_pct: float = 0.2  # 单个品种最大仓位
    margin_ratio: float = 0.15  # 保证金比例
    contract_multiplier: float = 1.0  # 合约乘数
    risk_free_rate: float = 0.02  # 无风险利率


class Portfolio:
    """投资组合管理器

    管理多个品种的持仓、资金和风险控制

    Example:
        >>> config = PortfolioConfig(initial_capital=1_000_000)
        >>> portfolio = Portfolio(config)
        >>> portfolio.open_position('IF0', PositionDirection.LONG, 1, 4000)
        >>> portfolio.update_prices({'IF0': 4100})
        >>> print(f"总资产: {portfolio.total_value:.2f}")
    """

    def __init__(self, config: Optional[PortfolioConfig] = None):
        """初始化投资组合

        Args:
            config: 组合配置
        """
        self.config = config or PortfolioConfig()
        self.cash: float = self.config.initial_capital
        self.positions: Dict[str, Position] = {}
        self.history: List[Dict] = []
        self._price_cache: Dict[str, float] = {}

    @property
    def total_value(self) -> float:
        """总资产价值 (现金 + 持仓市值)"""
        position_value = sum(
            pos.volume * pos.avg_price * self.config.contract_multiplier
            for pos in self.positions.values()
        )
        return self.cash + position_value

    @property
    def available_cash(self) -> float:
        """可用资金"""
        return self.cash

    @property
    def total_margin_used(self) -> float:
        """总占用保证金"""
        return sum(pos.margin for pos in self.positions.values())

    @property
    def margin_ratio(self) -> float:
        """当前保证金使用率"""
        if self.total_value == 0:
            return 0.0
        return self.total_margin_used / self.total_value

    @property
    def position_count(self) -> int:
        """当前持仓品种数"""
        return len([p for p in self.positions.values() if not p.is_flat])

    @property
    def total_unrealized_pnl(self) -> float:
        """总浮动盈亏"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def open_position(
        self,
        code: str,
        direction: PositionDirection,
        volume: int,
        price: float,
        open_time: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """开仓

        Args:
            code: 品种代码
            direction: 方向
            volume: 手数
            price: 开仓价格
            open_time: 开仓时间

        Returns:
            (是否成功, 消息)
        """
        if volume <= 0:
            return False, "手数必须大于0"

        if direction == PositionDirection.FLAT:
            return False, "方向不能为FLAT"

        # 计算所需资金
        notional_value = volume * price * self.config.contract_multiplier
        required_margin = notional_value * self.config.margin_ratio

        # 风险控制检查
        if self.cash < required_margin:
            return False, f"资金不足，需要 {required_margin:.2f}，可用 {self.cash:.2f}"

        # 单个品种仓位限制
        if code in self.positions:
            existing = self.positions[code]
            new_volume = existing.volume + volume
            position_value = new_volume * price * self.config.contract_multiplier
            if position_value > self.total_value * self.config.max_single_position_pct:
                return False, f"超出单个品种最大仓位限制"

        # 总仓位限制
        new_total_margin = self.total_margin_used + required_margin
        if new_total_margin > self.total_value * self.config.max_position_pct:
            return False, f"超出总仓位限制"

        # 执行开仓
        if code in self.positions and self.positions[code].direction == direction:
            # 加仓 - 更新均价
            existing = self.positions[code]
            total_volume = existing.volume + volume
            total_cost = (existing.avg_price * existing.volume + price * volume)
            existing.avg_price = total_cost / total_volume
            existing.volume = total_volume
            existing.margin += required_margin
        else:
            # 新仓位
            self.positions[code] = Position(
                code=code,
                direction=direction,
                volume=volume,
                avg_price=price,
                open_time=open_time or datetime.now(),
                margin=required_margin
            )

        self.cash -= required_margin

        # 记录历史
        self.history.append({
            'time': open_time or datetime.now(),
            'code': code,
            'action': 'OPEN',
            'direction': direction.name,
            'volume': volume,
            'price': price,
            'margin': required_margin
        })

        logger.info(f"[Portfolio] 开仓 {code} {direction.name} {volume}手 @ {price:.2f}")
        return True, "开仓成功"

    def close_position(
        self,
        code: str,
        volume: Optional[int] = None,
        price: Optional[float] = None,
        close_time: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """平仓

        Args:
            code: 品种代码
            volume: 平仓手数，None表示全部平仓
            price: 平仓价格
            close_time: 平仓时间

        Returns:
            (是否成功, 消息)
        """
        if code not in self.positions or self.positions[code].is_flat:
            return False, f"{code} 没有持仓"

        position = self.positions[code]
        close_volume = volume or position.volume

        if close_volume > position.volume:
            return False, f"平仓手数 {close_volume} 大于持仓 {position.volume}"

        close_price = price or self._price_cache.get(code, position.avg_price)

        # 计算盈亏
        if position.is_long:
            pnl = (close_price - position.avg_price) * close_volume * self.config.contract_multiplier
        else:
            pnl = (position.avg_price - close_price) * close_volume * self.config.contract_multiplier

        # 释放保证金
        released_margin = position.margin * (close_volume / position.volume)
        self.cash += released_margin + pnl

        # 更新持仓
        if close_volume == position.volume:
            position.direction = PositionDirection.FLAT
            position.volume = 0
            position.margin = 0
        else:
            position.volume -= close_volume
            position.margin -= released_margin

        # 记录历史
        self.history.append({
            'time': close_time or datetime.now(),
            'code': code,
            'action': 'CLOSE',
            'volume': close_volume,
            'price': close_price,
            'pnl': pnl
        })

        logger.info(f"[Portfolio] 平仓 {code} {close_volume}手 @ {close_price:.2f}, 盈亏: {pnl:+.2f}")
        return True, f"平仓成功，盈亏: {pnl:+.2f}"

    def update_prices(self, prices: Dict[str, float]) -> None:
        """更新价格并重新计算浮动盈亏

        Args:
            prices: 品种代码到最新价格的映射
        """
        self._price_cache.update(prices)

        for code, price in prices.items():
            if code in self.positions:
                self.positions[code].update_unrealized_pnl(
                    price, self.config.contract_multiplier
                )

    def get_position(self, code: str) -> Optional[Position]:
        """获取指定品种的持仓"""
        return self.positions.get(code)

    def get_all_positions(self) -> List[Position]:
        """获取所有非空仓持仓"""
        return [p for p in self.positions.values() if not p.is_flat]

    def get_position_df(self) -> pl.DataFrame:
        """获取持仓DataFrame"""
        positions = self.get_all_positions()

        if not positions:
            return pl.DataFrame({
                'code': [],
                'direction': [],
                'volume': [],
                'avg_price': [],
                'margin': [],
                'unrealized_pnl': []
            })

        data = [p.to_dict() for p in positions]
        return pl.DataFrame(data)

    def get_history_df(self) -> pl.DataFrame:
        """获取交易历史DataFrame"""
        if not self.history:
            return pl.DataFrame()
        return pl.DataFrame(self.history)

    def get_summary(self) -> Dict:
        """获取组合摘要"""
        positions = self.get_all_positions()

        return {
            'total_value': self.total_value,
            'cash': self.cash,
            'available_cash': self.available_cash,
            'total_margin_used': self.total_margin_used,
            'margin_ratio': self.margin_ratio,
            'position_count': len(positions),
            'total_unrealized_pnl': self.total_unrealized_pnl,
            'total_return': (self.total_value - self.config.initial_capital) / self.config.initial_capital
        }

    def liquidate_all(self, prices: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """清仓所有持仓

        Args:
            prices: 价格字典，如未提供使用缓存价格

        Returns:
            各品种平仓盈亏
        """
        results = {}
        use_prices = prices or self._price_cache

        for code in list(self.positions.keys()):
            position = self.positions[code]
            if not position.is_flat and code in use_prices:
                success, msg = self.close_position(code, price=use_prices[code])
                if success:
                    # 从history中提取盈亏
                    last_trade = self.history[-1] if self.history else {}
                    results[code] = last_trade.get('pnl', 0.0)

        return results
