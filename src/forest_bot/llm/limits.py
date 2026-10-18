"""Стан лімітів кожної моделі: із заголовків x-ratelimit-* і помилок.

Groq рахує ліміти окремо для кожної моделі. Заголовки `*-requests` — це запити
на добу, `*-tokens` — токени на хвилину; TPM резервується одразу під
prompt + max_completion_tokens.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TPM = 8000

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration(value: str | None) -> float | None:
    """«1m26.4s», «7.66s», «120ms» чи «2» (секунди) → секунди; інше → None."""
    if not value:
        return None
    value = value.strip()
    parts = _DURATION_PART.findall(value)
    if parts and "".join(number + unit for number, unit in parts) == value:
        return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)
    try:
        return float(value)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class ModelState:
    """Що відомо про модель зараз; час — за монотонним годинником клієнта."""

    tpm_limit: int = DEFAULT_TPM
    tokens_left: int | None = None
    tokens_reset_at: float = 0.0
    requests_left: int | None = None
    requests_reset_at: float = 0.0
    blocked_until: float = 0.0
    reason: str = ""

    def update(self, headers: Mapping[str, str], now: float) -> None:
        if (limit := _int(headers.get("x-ratelimit-limit-tokens"))) is not None:
            self.tpm_limit = limit
        if (left := _int(headers.get("x-ratelimit-remaining-tokens"))) is not None:
            self.tokens_left = left
            self.tokens_reset_at = now + (
                parse_duration(headers.get("x-ratelimit-reset-tokens")) or 60.0
            )
        if (left := _int(headers.get("x-ratelimit-remaining-requests"))) is not None:
            self.requests_left = left
            self.requests_reset_at = now + (
                parse_duration(headers.get("x-ratelimit-reset-requests")) or 3600.0
            )

    def block(self, until: float, reason: str) -> None:
        self.blocked_until = max(self.blocked_until, until)
        self.reason = reason

    def reserve(self, tokens: int) -> None:
        """Резерв до відповіді: паралельні запити не розраховують на ті самі токени."""
        if self.tokens_left is not None:
            self.tokens_left -= tokens
        if self.requests_left is not None:
            self.requests_left -= 1

    def available_at(self, tokens: int, now: float) -> float:
        """Коли модель прийме запит на `tokens` токенів; `now` — уже зараз."""
        at = max(now, self.blocked_until)
        if self.requests_left is not None and self.requests_left <= 0:
            at = max(at, self.requests_reset_at)
        if self.tokens_left is not None and self.tokens_left < tokens:
            at = max(at, self.tokens_reset_at)
        return at
