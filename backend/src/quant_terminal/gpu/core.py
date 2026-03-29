"""
GPU核心模块

PyTorch CUDA加速的基础功能
"""

from typing import Dict, List, Tuple, Optional
import torch
import numpy as np
from loguru import logger


class GPUCore:
    """
    GPU核心管理器

    管理CUDA设备和内存，提供基础的GPU操作接口

    Example:
        >>> gpu = GPUCore()
        >>> data_gpu = gpu.to_gpu({'prices': prices_array})
        >>> results = gpu.compute_returns(data_gpu)
    """

    def __init__(self, device_id: int = 0):
        """
        初始化GPU核心

        Args:
            device_id: CUDA设备ID
        """
        self.device_id = device_id
        self.device = self._get_device()
        self.stream = torch.cuda.Stream(device=self.device) if self.is_available else None

        if self.is_available:
            self._log_device_info()

    def _get_device(self) -> torch.device:
        """获取CUDA设备"""
        if torch.cuda.is_available():
            return torch.device(f'cuda:{self.device_id}')
        else:
            logger.warning("CUDA不可用，使用CPU")
            return torch.device('cpu')

    @property
    def is_available(self) -> bool:
        """CUDA是否可用"""
        return torch.cuda.is_available()

    @property
    def device_name(self) -> str:
        """设备名称"""
        if self.is_available:
            return torch.cuda.get_device_name(self.device_id)
        return "CPU"

    def _log_device_info(self):
        """记录设备信息"""
        props = torch.cuda.get_device_properties(self.device_id)
        logger.info(f"[GPUCore] 设备: {props.name}")
        logger.info(f"[GPUCore] 显存: {props.total_memory / 1e9:.1f} GB")
        logger.info(f"[GPUCore] 计算能力: {props.major}.{props.minor}")
        logger.info(f"[GPUCore] 多处理器: {props.multi_processor_count}")

    def to_gpu(self, data: Dict[str, np.ndarray]) -> Dict[str, torch.Tensor]:
        """
        将NumPy数组转移到GPU

        Args:
            data: 名称到数组的映射

        Returns:
            名称到GPU张量的映射
        """
        return {
            k: torch.tensor(v, dtype=torch.float32, device=self.device)
            for k, v in data.items()
        }

    def to_cpu(self, tensor: torch.Tensor) -> np.ndarray:
        """
        将GPU张量转回CPU

        Args:
            tensor: GPU张量

        Returns:
            NumPy数组
        """
        return tensor.cpu().numpy()

    def compute_returns(
        self,
        prices: torch.Tensor,
        signals: torch.Tensor,
        commission: float = 0.0001,
        slippage: float = 0.0002
    ) -> Dict[str, torch.Tensor]:
        """
        GPU计算收益率

        Args:
            prices: 价格序列 [n]
            signals: 信号序列 [n] (1=多, -1=空, 0=平)
            commission: 手续费率
            slippage: 滑点率

        Returns:
            包含returns, cumulative, equity的字典
        """
        n = len(prices)

        # 计算价格变化
        price_changes = (prices[1:] - prices[:-1]) / prices[:-1]
        trade_signals = signals[:-1].float()

        # 计算收益
        returns = price_changes * trade_signals

        # 扣除成本
        costs = (trade_signals != 0).float() * (commission + slippage)
        returns = returns - costs

        # 累计收益和权益
        cumulative = torch.cumsum(returns, dim=0)
        equity = 1.0 + cumulative

        return {
            'returns': returns,
            'cumulative': cumulative,
            'equity': equity
        }

    def compute_metrics(self, returns: torch.Tensor) -> Dict[str, float]:
        """
        计算绩效指标

        Args:
            returns: 收益率序列

        Returns:
            指标字典
        """
        # 过滤有效交易
        mask = returns != 0
        valid_returns = returns[mask]

        if len(valid_returns) < 2:
            return {
                'total_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'trades': 0
            }

        total_return = returns.sum().item()
        mean_ret = valid_returns.mean()
        std_ret = valid_returns.std()
        sharpe = (mean_ret / (std_ret + 1e-10) * np.sqrt(252)).item()

        # 最大回撤
        cumsum = torch.cumsum(returns, dim=0)
        running_max = torch.cummax(cumsum, dim=0)[0]
        drawdown = cumsum - running_max
        max_dd = torch.min(drawdown).item()

        return {
            'total_return': total_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': abs(max_dd),
            'trades': len(valid_returns)
        }

    def batch_backtest(
        self,
        prices: torch.Tensor,
        signals_batch: torch.Tensor
    ) -> List[Dict]:
        """
        批量回测多个信号序列

        Args:
            prices: 价格序列 [n]
            signals_batch: 信号批次 [m, n] (m个策略)

        Returns:
            各策略的指标列表
        """
        results = []

        for signals in signals_batch:
            metrics = self.compute_returns(prices, signals)
            indicators = self.compute_metrics(metrics['returns'])
            results.append(indicators)

        return results

    def rolling_window(
        self,
        data: torch.Tensor,
        window: int
    ) -> torch.Tensor:
        """
        创建rolling window视图

        Args:
            data: 输入数据 [n]
            window: 窗口大小

        Returns:
            Unfold后的数据 [n-window+1, window]
        """
        return data.unfold(0, window, 1)

    def rolling_max(self, data: torch.Tensor, window: int) -> torch.Tensor:
        """Rolling最大值"""
        unfolded = self.rolling_window(data, window)
        max_vals = unfolded.max(dim=1)[0]
        # 补齐到原长度
        pad = window - 1
        return torch.cat([torch.zeros(pad, device=self.device), max_vals])

    def rolling_min(self, data: torch.Tensor, window: int) -> torch.Tensor:
        """Rolling最小值"""
        unfolded = self.rolling_window(data, window)
        min_vals = unfolded.min(dim=1)[0]
        pad = window - 1
        return torch.cat([torch.zeros(pad, device=self.device), min_vals])

    def synchronize(self):
        """同步CUDA流"""
        if self.is_available and self.stream:
            self.stream.synchronize()

    def get_memory_info(self) -> Dict[str, float]:
        """获取显存信息"""
        if not self.is_available:
            return {'allocated': 0, 'reserved': 0, 'total': 0}

        return {
            'allocated': torch.cuda.memory_allocated(self.device_id) / 1e9,
            'reserved': torch.cuda.memory_reserved(self.device_id) / 1e9,
            'total': torch.cuda.get_device_properties(self.device_id).total_memory / 1e9
        }

    def __repr__(self) -> str:
        return f"GPUCore(device={self.device_name}, available={self.is_available})"
