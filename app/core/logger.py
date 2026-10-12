"""日志：同时输出到控制台与文件，供运维排查"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE, LOG_LEVEL

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger("zqc")
    root.setLevel(LOG_LEVEL)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"zqc.{name}")
