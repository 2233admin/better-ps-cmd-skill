"""
配置管理

支持YAML配置文件和环境变量
"""

import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional


@dataclass
class Config:
    """配置类"""
    # 回测配置
    backtest: Dict[str, Any] = field(default_factory=lambda: {
        "initial_capital": 1_000_000.0,
        "commission": 0.0001,
        "slippage": 0.0002,
    })

    # 数据源配置
    data: Dict[str, Any] = field(default_factory=lambda: {
        "source": "akshare",
        "cache_dir": "./data/cache",
        "use_cache": True,
    })

    # 风控配置
    risk: Dict[str, Any] = field(default_factory=lambda: {
        "max_position_pct": 0.8,
        "max_single_position_pct": 0.2,
        "stop_loss_pct": 0.02,
    })

    # GPU配置
    gpu: Dict[str, Any] = field(default_factory=lambda: {
        "use_gpu": True,
        "device_id": 0,
    })

    # 日志配置
    logging: Dict[str, Any] = field(default_factory=lambda: {
        "level": "INFO",
        "log_file": "logs/quant_terminal.log",
    })

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """从字典创建配置"""
        return cls(
            backtest=data.get("backtest", {}),
            data=data.get("data", {}),
            risk=data.get("risk", {}),
            gpu=data.get("gpu", {}),
            logging=data.get("logging", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "backtest": self.backtest,
            "data": self.data,
            "risk": self.risk,
            "gpu": self.gpu,
            "logging": self.logging,
        }


def load_config(config_path: str = None) -> Config:
    """
    加载配置文件

    优先级:
    1. 指定的配置文件路径
    2. 环境变量 QUANT_TERMINAL_CONFIG
    3. 默认路径 ./configs/default.yaml
    4. 内置默认配置

    Args:
        config_path: 配置文件路径

    Returns:
        配置对象
    """
    # 确定配置文件路径
    if config_path is None:
        config_path = os.getenv("QUANT_TERMINAL_CONFIG", "configs/default.yaml")

    path = Path(config_path)

    # 如果文件存在则加载
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return Config.from_dict(data)

    # 否则返回默认配置
    return Config()


def save_config(config: Config, config_path: str):
    """
    保存配置到文件

    Args:
        config: 配置对象
        config_path: 保存路径
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False, allow_unicode=True)
