"""Відомі моделі Groq і параметри, з якими їх викликати.

Ліміти free-плану (18.10.2026, із заголовків відповіді): у кожної моделі свої
30 RPM, 1000 RPD, 8000 TPM, 200K TPD.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

# Порядок — за міні-бенчмарком українською (scripts/bench_llm.py)
DEFAULT_CHAIN = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b")


@dataclass(frozen=True)
class ModelProfile:
    # strict json_schema (constrained decoding); інакше — json_object і схема в промпті
    strict_json: bool = False
    params: Mapping[str, object] = field(default_factory=dict)
    # Службові токени шаблону чату поверх тексту повідомлень
    prompt_overhead: int = 10
    # Міркування рахуються в max_completion_tokens: без запасу відповідь обріжеться
    reasoning_reserve: int = 0


_GPT_OSS = ModelProfile(
    strict_json=True,
    params={"reasoning_effort": "low", "include_reasoning": False},
    prompt_overhead=110,
    reasoning_reserve=300,
)

PROFILES: Mapping[str, ModelProfile] = {
    "openai/gpt-oss-120b": _GPT_OSS,
    "openai/gpt-oss-20b": _GPT_OSS,
    # strict json_schema у qwen на Groq зривається (400 json_validate_failed, генерація
    # починається з «{thought»); json_object + валідація pydantic стабільні (18.10)
    "qwen/qwen3.8-27b": ModelProfile(params={"reasoning_effort": "none"}),
}


def profile_for(model: str) -> ModelProfile:
    """Невідома модель з GROQ_MODELS — без особливих параметрів, JSON у режимі json_object."""
    return PROFILES.get(model, ModelProfile())
