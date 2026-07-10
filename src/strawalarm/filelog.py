"""Persistent session log in the XDG state directory, so incidents can
be diagnosed after the window is gone (both real-world bugs so far
needed exactly this)."""

import logging
import os
from logging.handlers import RotatingFileHandler


def log_path() -> str:
    state = os.environ.get("XDG_STATE_HOME",
                           os.path.expanduser("~/.local/state"))
    return os.path.join(state, "strawalarm", "strawalarm.log")


def get_logger() -> logging.Logger:
    """Logger writing to ~/.local/state/strawalarm/strawalarm.log with
    rotation (1 MB x 3). Falls back to a no-op logger if the state dir
    can't be created (read-only home, sandbox)."""
    logger = logging.getLogger("strawalarm")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger
