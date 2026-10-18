"""Heartbeat: свіжий файл — живий, старий чи відсутній — ні."""

import os
import time
from pathlib import Path

from forest_bot.health import beat, is_alive


def test_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    assert not is_alive(path)
    beat(path)
    assert is_alive(path)
    old = time.time() - 120
    os.utime(path, (old, old))
    assert not is_alive(path, max_age=60)
