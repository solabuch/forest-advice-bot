"""Liveness через heartbeat-файл: HTTP-порт для long polling не потрібен."""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "forest_bot.heartbeat"
HEARTBEAT_INTERVAL = 15.0
HEARTBEAT_MAX_AGE = 60.0


def beat(path: Path = HEARTBEAT_FILE) -> None:
    path.touch()


async def heartbeat_loop(path: Path = HEARTBEAT_FILE, interval: float = HEARTBEAT_INTERVAL) -> None:
    """Оновлює файл, доки живий event loop; завис loop — файл старіє."""
    while True:
        try:
            beat(path)
        except OSError:
            logger.exception("Не вдалося оновити heartbeat %s", path)
        await asyncio.sleep(interval)


def is_alive(path: Path = HEARTBEAT_FILE, max_age: float = HEARTBEAT_MAX_AGE) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age <= max_age
