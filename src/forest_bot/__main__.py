"""Точка входу: python -m forest_bot <команда>.

Важкі модулі (aiogram, SQLAlchemy, pydantic) імпортуються лише в командах, яким вони
потрібні: `health` Docker викликає кожні 30 с, і він має вкладатися в мілісекунди.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from forest_bot import __version__, health
from forest_bot.logs import setup_logging

if TYPE_CHECKING:
    from forest_bot.config import ConfigError, DatabaseSettings

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
    commands.add_parser("migrate", help="дочекатися БД і застосувати міграції")
    return parser


def _config_failed(error: ConfigError) -> int:
    setup_logging()
    for problem in error.problems:
        logger.error(problem)
    return EXIT_CONFIG


def _start_logging(settings: DatabaseSettings, command: str) -> None:
    setup_logging(settings.log_level, settings.log_format, settings.secret_values())
    logger.info("forest_bot %s, команда %s", __version__, command)


def _migrate() -> int:
    from forest_bot import db
    from forest_bot.config import ConfigError, load_database_settings

    # Міграціям потрібна лише БД: без токенів бота вони теж мають пройти
    try:
        settings = load_database_settings()
    except ConfigError as error:
        return _config_failed(error)
    _start_logging(settings, "migrate")
    return db.migrate(settings)


def _check_or_run(command: str, args: argparse.Namespace) -> int:
    import asyncio

    from forest_bot import bot, startup
    from forest_bot.config import ConfigError, load_settings

    try:
        settings = load_settings()
    except ConfigError as error:
        return _config_failed(error)
    _start_logging(settings, command)

    if command == "check":
        results = asyncio.run(
            startup.run_checks(settings, offline=args.offline, database=not args.no_db)
        )
        return startup.exit_code(results, strict=args.strict)
    try:
        return asyncio.run(bot.run(settings))
    except KeyboardInterrupt:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Розбирає команду й запускає її; повертає код виходу."""
    args = build_parser().parse_args(argv)
    command = args.command or "run"
    if command == "health":
        return 0 if health.is_alive() else 1
    if command == "migrate":
        return _migrate()
    return _check_or_run(command, args)


if __name__ == "__main__":
    sys.exit(main())
