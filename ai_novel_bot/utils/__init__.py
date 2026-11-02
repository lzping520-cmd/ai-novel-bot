"""
实用工具
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logger(name: str = "ai_novel_bot", level: int = logging.INFO) -> logging.Logger:
    """统一日志"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(h)
    return logger
