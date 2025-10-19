# app/services/logger.py
import logging
import sys
from typing import Optional

_LOG_LEVEL = logging.INFO
_initialized = False


def setup_logging(level: Optional[int] = None):
    global _initialized
    if _initialized:
        return
    log_level = level if level is not None else _LOG_LEVEL
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    _initialized = True
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: level={logging.getLevelName(log_level)}")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_log_level(level: int):
    global _LOG_LEVEL
    _LOG_LEVEL = level
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)
    logger = get_logger(__name__)
    logger.info(f"Log level changed to: {logging.getLevelName(level)}")


setup_logging()