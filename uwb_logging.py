# -*- coding: utf-8 -*-
"""Shared logging configuration for the UWB location tool."""
import logging
import os
import sys


LOGGER_NAME = "uwb_location"
_TRUE_VALUES = {"1", "true", "yes", "on", "debug"}
_CONFIGURED = False


def _env_enabled():
    return os.environ.get("UWB_DEBUG_LOG", "").strip().lower() in _TRUE_VALUES


def configure_logging(enabled=None, level=None, log_file=None):
    """Configure application logging.

    By default high-frequency debug logging is disabled. Set
    UWB_DEBUG_LOG=1 to enable console logging, and optionally UWB_LOG_FILE to
    mirror logs into a file.
    """
    global _CONFIGURED

    enabled = _env_enabled() if enabled is None else bool(enabled)
    level_name = os.environ.get("UWB_LOG_LEVEL", "").strip().upper()
    if level is None:
        level = getattr(logging, level_name, None) if level_name else None
    if level is None:
        level = logging.DEBUG if enabled else logging.WARNING

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers[:] = []
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    if enabled:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    log_file = log_file or os.environ.get("UWB_LOG_FILE", "").strip()
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    _CONFIGURED = True
    return logger


def get_logger(name=None):
    """Return the shared application logger or one of its child loggers."""
    if not _CONFIGURED:
        configure_logging()
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger("%s.%s" % (LOGGER_NAME, name))
