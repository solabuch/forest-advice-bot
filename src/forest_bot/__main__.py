"""Точка входу: python -m forest_bot."""

import logging
import sys

from forest_bot import __version__
from forest_bot.logs import setup_logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Запускає застосунок і повертає код виходу."""
    setup_logging()
    logger.info("forest_bot %s", __version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
