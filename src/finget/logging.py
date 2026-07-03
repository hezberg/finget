"""日志配置，基于 loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(level: str = "INFO", log_dir: Path | str | None = None) -> None:
    """初始化全局日志.

    Args:
        level: 日志级别.
        log_dir: 日志文件目录; None 则仅输出到控制台.
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "finget_{time:YYYY-MM-DD}.log",
            level=level,
            rotation="00:00",
            retention="30 days",
            encoding="utf-8",
        )


# 默认导出全局 logger
log = logger
