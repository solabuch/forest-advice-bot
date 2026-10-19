"""Eval питань і відповідей (S-08, D-12): eval/qa.yaml на справжньому пошуку й Groq.

Потрібні БД з архівом (немає — імпортує сам), модель embeddings і ключ Groq із .env,
як у бота. Усе, що пишеться в БД (журнал, завантаження), — у транзакції, яку наприкінці
відкочуємо: живу базу eval не змінює.

    uv run python scripts/eval_qa.py                # усі кейси
    uv run python scripts/eval_qa.py fees-reference # лише ці
    docker compose run --rm --entrypoint python app scripts/eval_qa.py  # без uv; підсумок — у вивід

Один прогін — ~50 відповідей, ~150 тис. токенів Groq, тобто чверть добового ліміту
трьох моделей: не запускати на кожен коміт (у CI — лише вручну).
"""

import asyncio
import hashlib
import json
import logging
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from datetime import time as day_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot.config import load_settings
from forest_bot.db import create_engine
from forest_bot.embeddings import Embedder, load_embedder
from forest_bot.ingest.archive import import_archive
from forest_bot.ingest.index import index_document
from forest_bot.ingest.parse import parse
from forest_bot.ingest.place import detect_place
from forest_bot.llm.client import GroqClient, LlmError
from forest_bot.llm.profiles import DEFAULT_CHAIN
from forest_bot.llm.prompts import ANSWER_PROMPT
from forest_bot.models import Document, DocumentSource, JoinedVia
from forest_bot.places import PlaceGraph, load_places
from forest_bot.qa.answer import Answerer
from forest_bot.qa.search import scope
from forest_bot.repo import documents, users

logger = logging.getLogger("eval_qa")

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "qa.yaml"
RESULTS = ROOT / "eval" / "results"
EVAL_USER = 990_001
Sessions = async_sessionmaker[AsyncSession]


def normalize(text: str) -> str:
    return (
        text.casefold()
        .replace("’", "'")
        .replace("ʼ", "'")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", " ")
    )


def contains(text: str, fact: str) -> bool:
    """«а | б» — будь-який із варіантів."""
    return any(normalize(option.strip()) in normalize(text) for option in fact.split("|"))


@dataclass
class Result:
    id: str
    req: list[str]
    question: str
    answer: str = ""
    places: list[str] = field(default_factory=list)
    place_ok: bool = True
    # Очікування кейса: питає кнопками / порада є / джерела
    asks_place: bool = False
    expect_found: bool = True
    expected_sources: int = 0
    found: bool = True
    found_ok: bool = True
    sources: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    foreign_sources: list[str] = field(default_factory=list)
    first_ok: bool | None = None
    facts_total: int = 0
    facts_missing: list[str] = field(default_factory=list)
    absent_present: list[str] = field(default_factory=list)
    hit: bool | None = None
    seconds: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.place_ok
            and self.found_ok
            and not self.missing_sources
            and not self.foreign_sources
            and self.first_ok is not False
            and not self.facts_missing
            and not self.absent_present
        )


class Eval:
    def __init__(
        self,
        sessions: Sessions,
        embedder: Embedder,
        graph: PlaceGraph,
        llm: GroqClient,
        *,
        today: date,
        timezone: ZoneInfo,
    ) -> None:
        self.sessions = sessions
        self.embedder = embedder
        self.graph = graph
        self.llm = llm
        self.today = today
        self.timezone = timezone
        self.senders: dict[str, int] = {}

    async def current_files(self) -> dict[int, tuple[str, str]]:
        """id чинного документа → (файл, місце)."""
        async with self.sessions() as session:
            rows = await session.execute(
                select(Document.id, Document.file_name, Document.place_id).where(
                    Document.is_current.is_(True)
                )
            )
            return {doc_id: (name, place) for doc_id, name, place in rows}

    async def run_case(self, case: dict[str, Any]) -> Result:
        result = Result(case["id"], case.get("req", []), case["question"])
        result.asks_place = not case.get("places")
        result.expect_found = case.get("found", True)
        result.expected_sources = len(case.get("sources", []))
        today = date.fromisoformat(str(case.get("today", self.today)))
        moment = datetime.combine(today, day_time(10), tzinfo=UTC)
        answerer = Answerer(
            self.sessions,
            self.embedder,
            self.graph,
            self.llm,
            self.timezone,
            clock=lambda: moment,
        )
        started = time.monotonic()
        try:
            reply = await answerer.ask(case["question"])
        except LlmError as error:
            result.error = str(error)
            return result
        result.seconds = time.monotonic() - started
        expected_places = list(case.get("places", []))
        result.places = list(reply.place_ids)
        if not expected_places:
            result.place_ok = reply.needs_place
            return result
        result.place_ok = list(reply.place_ids) == expected_places
        result.answer = reply.text
        result.found = reply.found
        result.found_ok = reply.found == case.get("found", True)

        files = await self.current_files()
        allowed = {p for place in reply.place_ids for p in scope(place, self.graph)}
        result.sources = [files[s.document_id][0] for s in reply.sources if s.document_id in files]
        # Джерело поза областю місця чи не чинна версія — найгірша помилка (R-10, R-15)
        result.foreign_sources = [
            str(s.document_id)
            for s in reply.sources
            if s.document_id not in files or files[s.document_id][1] not in allowed
        ]
        expected = list(case.get("sources", []))
        result.missing_sources = [name for name in expected if name not in result.sources]
        if first := case.get("first"):
            result.first_ok = bool(result.sources) and result.sources[0] == first
        retrieved = [files[d][0] for d in reply.retrieved if d in files]
        if expected:
            result.hit = all(name in retrieved for name in expected)
        facts = [str(f) for f in case.get("facts", [])]
        result.facts_total = len(facts)
        result.facts_missing = [f for f in facts if not contains(reply.text, f)]
        result.absent_present = [a for a in case.get("absent", []) if contains(reply.text, a)]
        return result

    async def upload(self, item: dict[str, Any]) -> None:
        """Документ «від звіра»: розбір, місце, заміна архівного, індексація (R-15, R-12)."""
        path = ROOT / item["file"]
        data = path.read_bytes()
        parsed = parse(data, path.name)
        decision = await detect_place(parsed, self.graph)
        if decision.place_id is None:
            raise RuntimeError(f"{path.name}: місце не визначено")
        async with self.sessions.begin() as session:
            sender_id = self.senders.setdefault(item["sender"], EVAL_USER + len(self.senders))
            user = await users.admit(session, sender_id, JoinedVia.INVITE)
            if not user.real_name:
                await users.set_name(session, user, item["sender"])
            replaced = None
            if target := item.get("replaces"):
                replaced = await session.scalar(
                    select(Document).where(Document.is_current, Document.file_name == target)
                )
            document = await documents.create(
                session,
                place_id=decision.place_id,
                title=parsed.title or path.stem,
                source=DocumentSource.TELEGRAM,
                file=documents.FileInfo(
                    path.name, len(data), hashlib.sha256(data).hexdigest(), telegram_file_id="eval"
                ),
                sender_id=user.id,
                lineage_id=replaced.lineage_id if replaced else None,
            )
            await index_document(session, document, parsed, self.embedder, self.graph)


def summary(results: list[Result]) -> dict[str, Any]:
    answering = [r for r in results if not r.asks_place and r.error is None]
    with_sources = [r for r in answering if r.expected_sources]
    facts_total = sum(r.facts_total for r in answering)
    facts_found = facts_total - sum(len(r.facts_missing) for r in answering)
    no_advice = [r for r in answering if not r.expect_found]
    seconds = sorted(r.seconds for r in results if r.seconds)
    hits = [r.hit for r in results if r.hit is not None]
    firsts = [r.first_ok for r in results if r.first_ok is not None]
    return {
        "cases": len(results),
        "passed": sum(r.ok for r in results),
        "place_ok": sum(r.place_ok for r in results),
        "sources_ok": sum(not r.missing_sources for r in with_sources),
        "sources_total": len(with_sources),
        "foreign_sources": sum(len(r.foreign_sources) for r in results),
        "facts_found": facts_found,
        "facts_total": facts_total,
        "not_found_ok": sum(not r.found for r in no_advice),
        "not_found_cases": len(no_advice),
        "false_not_found": sum(1 for r in answering if r.expect_found and not r.found),
        "first_ok": sum(bool(f) for f in firsts),
        "first_total": len(firsts),
        "hit": sum(hits),
        "hit_total": len(hits),
        "p50_s": statistics.median(seconds) if seconds else 0.0,
        "p95_s": seconds[min(len(seconds) - 1, round(0.95 * (len(seconds) - 1)))]
        if seconds
        else 0.0,
        "errors": sum(r.error is not None for r in results),
    }


def report(results: list[Result], stats: dict[str, Any], models: tuple[str, ...]) -> str:
    lines = [
        f"# Eval відповідей · {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"Промпт `{ANSWER_PROMPT}`, моделі: {' → '.join(models)}",
        "",
        "| Метрика | Результат |",
        "|---|---|",
        f"| Кейсів пройдено повністю | {stats['passed']}/{stats['cases']} |",
        f"| Місце з питання | {stats['place_ok']}/{stats['cases']} |",
        f"| Очікувані джерела у відповіді | {stats['sources_ok']}/{stats['sources_total']} |",
        f"| Джерела з чужого місця чи неактивної версії | {stats['foreign_sources']} |",
        f"| Ключові факти | {stats['facts_found']}/{stats['facts_total']} |",
        f"| «Не знайшов», коли поради немає | {stats['not_found_ok']}/{stats['not_found_cases']} |",
        f"| Хибне «не знайшов» | {stats['false_not_found']} |",
        f"| Конкретна порада першою | {stats['first_ok']}/{stats['first_total']} |",
        f"| Пошук: потрібні документи серед знайдених | {stats['hit']}/{stats['hit_total']} |",
        f"| Час відповіді p50 / p95 | {stats['p50_s']:.1f} / {stats['p95_s']:.1f} с |",
        "",
        "## Кейси",
        "",
    ]
    for r in results:
        mark = "✅" if r.ok else "❌"
        lines.append(f"### {mark} {r.id} ({', '.join(r.req)})")
        lines.append(f"**Питання:** {r.question}")
        lines.append("")
        if r.answer:
            lines.extend(f"> {line}" if line else ">" for line in r.answer.splitlines())
        elif not r.places:
            lines.append("> (бот питає місце кнопками)")
        problems = []
        if r.error:
            problems.append(f"помилка: {r.error}")
        if not r.place_ok:
            problems.append(f"місце: {r.places}")
        if not r.found_ok:
            problems.append("знайшов/не знайшов — не так")
        if r.missing_sources:
            problems.append(f"бракує джерел: {r.missing_sources}")
        if r.foreign_sources:
            problems.append(f"чужі джерела: {r.foreign_sources}")
        if r.first_ok is False:
            problems.append("першою не конкретна порада")
        if r.facts_missing:
            problems.append(f"бракує фактів: {r.facts_missing}")
        if r.absent_present:
            problems.append(f"зайве: {r.absent_present}")
        if problems:
            lines.append("")
            lines.append("⚠️ " + "; ".join(problems))
        lines.append("")
    return "\n".join(lines)


async def main(only: list[str]) -> int:
    settings = load_settings()
    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    graph = load_places(settings.places_file)
    embedder = load_embedder(settings.embedding_model_dir)
    models = tuple(settings.groq_models) or DEFAULT_CHAIN
    llm = GroqClient(settings.groq_api_key.get_secret_value(), models, max_wait=180)
    engine = create_engine(settings)
    results: list[Result] = []
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            sessions = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            runner = Eval(
                sessions,
                embedder,
                graph,
                llm,
                today=date.fromisoformat(str(spec["today"])),
                timezone=settings.timezone,
            )
            async with sessions() as session:
                current = await session.scalar(
                    select(func.count()).select_from(Document).where(Document.is_current)
                )
            if not current:
                logger.info("Архіву в БД немає — імпортую (скани — OCR, кілька десятків секунд)")
                await import_archive(sessions, settings.archive_dir, embedder, graph, llm)

            def wanted(case: dict[str, Any]) -> bool:
                return not only or case["id"] in only

            for case in filter(wanted, spec["cases"]):
                results.append(await runner.run_case(case))
                logger.info("%s %s", "✅" if results[-1].ok else "❌", case["id"])
            after = spec.get("after_uploads") or {}
            later = [c for c in after.get("cases", []) if wanted(c)]
            if later:
                for item in after.get("uploads", []):
                    await runner.upload(item)
                for case in later:
                    results.append(await runner.run_case(case))
                    logger.info("%s %s", "✅" if results[-1].ok else "❌", case["id"])
            await transaction.rollback()
    finally:
        await llm.aclose()
        await engine.dispose()

    stats = summary(results)
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y%m%d_%H%M}"
    (RESULTS / f"qa_{stamp}.json").write_text(
        json.dumps(
            {"stats": stats, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    markdown = report(results, stats, models)
    (RESULTS / f"qa_{stamp}.md").write_text(markdown, encoding="utf-8")
    logger.info("\n%s\n\nЗвіт: %s", markdown.split("## Кейси")[0], RESULTS / f"qa_{stamp}.md")
    return 0 if stats["foreign_sources"] == 0 and not stats["errors"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main(sys.argv[1:])))
