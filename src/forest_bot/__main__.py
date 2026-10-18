"""Точка входу: python -m forest_bot <команда>."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from forest_bot import __version__, bot, health, startup
from forest_bot.config import ConfigError, load_settings
from forest_bot.logs import setup_logging

logger = logging.getLogger("forest_bot")

EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forest_bot", description="Бот порад для звірів лісу")
    parser.add_argument("--version", action="version", version=f"forest_bot {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="самоперевірка → полінг (за замовчуванням)")
    check = commands.add_parser("check", help="лише самоперевірка")
    check.add_argument("--offline", action="store_true", help="без Telegram і Groq")
    check.add_argument("--no-db", action="store_true", help="без бази даних")
    check.add_argument("--strict", action="store_true", help="попередження = помилка")
    commands.add_parser("health", help="для HEALTHCHECK: процес живий")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Розбирає команду, читає конфіг і запускає; повертає код виходу."""
    args = build_parser().parse_args(argv)
    command = args.command or "run"

    # health викликає Docker кожні N секунд — без конфігу й логів
    if command == "health":
        return 0 if health.is_alive() else 1

    try:
        settings = load_settings()
    except ConfigError as error:
        setup_logging()
        for problem in error.problems:
            logger.error(problem)
        return EXIT_CONFIG

    setup_logging(settings.log_level, settings.log_format, settings.secret_values())
    logger.info("forest_bot %s, команда %s", __version__, command)

    if command == "check":
        results = asyncio.run(
            startup.run_checks(settings, offline=args.offline, database=not args.no_db)
        )
        return startup.exit_code(results, strict=args.strict)
    try:
        return asyncio.run(bot.run(settings))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
