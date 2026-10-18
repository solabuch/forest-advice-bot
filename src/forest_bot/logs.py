"""Налаштування логування: усе в stdout, рівень — з конфігу."""

import logging.config

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Налаштовує кореневий логер; чужі логери не вимикає."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"text": {"format": TEXT_FORMAT}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "text",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"level": level.upper(), "handlers": ["stdout"]},
        }
    )
