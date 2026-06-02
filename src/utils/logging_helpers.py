"""Structured logging setup using loguru."""
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_dir: str = "./logs", level: str = "INFO") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - {message}",
    )
    logger.add(
        f"{log_dir}/vol_infra_{{time:YYYY-MM-DD}}.log",
        level=level,
        rotation="10 MB",
        retention="30 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    )


def get_logger(name: str):
    return logger.bind(name=name)
