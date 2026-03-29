"""
日志配置

统一的日志格式和级别管理
"""

import sys
from pathlib import Path
from loguru import logger


def setup_logging(
    level: str = "INFO",
    log_file: str = None,
    rotation: str = "10 MB",
    retention: int = 7
):
    """
    配置日志系统

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR)
        log_file: 日志文件路径
        rotation: 日志轮转大小
        retention: 保留天数
    """
    # 移除默认处理器
    logger.remove()

    # 添加控制台处理器
    logger.add(
        sys.stdout,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
        colorize=True
    )

    # 添加文件处理器
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation=rotation,
            retention=f"{retention} days",
            encoding="utf-8"
        )

    return logger


def get_logger(name: str = None):
    """
    获取logger实例

    Args:
        name: 模块名称

    Returns:
        logger实例
    """
    return logger.bind(name=name)


# 默认配置
setup_logging()
