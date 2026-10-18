"""Telegram-бот порад для звірів лісу."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("forest-bot")
except PackageNotFoundError:  # запуск із сирців без встановленого пакета
    __version__ = "0.0.0"
