"""Схема БД: SQLAlchemy 2 (typed ORM) + pgvector.

Інваріанти, від яких залежить правильність відповідей, тримає сама БД:
- у лінії версій не більше однієї чинної (partial unique index);
- чинна версія завжди проіндексована й не видалена (check);
- архівний документ не має відправника, документ із Telegram — має (check).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from forest_bot.places import PlaceKind

# multilingual-e5-small; інша модель = міграція розмірності + перерахунок embeddings
EMBEDDING_DIM = 384

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def str_enum[E: StrEnum](enum: type[E]) -> Enum:
    """Значення enum як VARCHAR + CHECK: міграції без ALTER TYPE."""
    return Enum(
        enum,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda e: [m.value for m in e],
        name=enum.__name__.lower(),
    )


def created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class DocumentSource(StrEnum):
    ARCHIVE = "archive"
    TELEGRAM = "telegram"


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JoinedVia(StrEnum):
    FIRST = "first"
    INVITE = "invite"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Rating(StrEnum):
    USEFUL = "useful"
    WRONG = "wrong"


class PlaceRow(Base):
    """Дзеркало places.yaml для зовнішніх ключів; джерело правди — файл (D-21)."""

    __tablename__ = "places"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[PlaceKind] = mapped_column(str_enum(PlaceKind))
    name: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[int]
    # Місце зникло з файлу → неактивне, але документи з ним не губляться
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))


class PlacePart(Base):
    """Рукавичка ↔ частина лісу: M2M, бо «Під вербою» — у двох частинах."""

    __tablename__ = "place_parts"

    mitten_id: Mapped[str] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), primary_key=True
    )
    part_id: Mapped[str] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), primary_key=True
    )


class User(Base):
    __tablename__ = "users"

    # Telegram user id
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Справжнє ім'я питаємо при першому документі (R-06, D-34)
    real_name: Mapped[str | None] = mapped_column(String(100))
    # Ім'я без урахування регістру — унікальне (D-44)
    name_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    # Членство за запрошенням (D-54); None — ще не в боті
    member_since: Mapped[datetime | None]
    joined_via: Mapped[JoinedVia | None] = mapped_column(str_enum(JoinedVia))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BotSetting(Base):
    """Значення, які бот генерує сам: напр. код запрошення, якщо не задано в env."""

    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Document(Base):
    """Версія документа. Лінія (`lineage_id`) — усі варіанти «того самого» документа."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version: Mapped[int]
    # Чинна версія лінії: лише вона бере участь у пошуку (R-15)
    is_current: Mapped[bool] = mapped_column(server_default=text("false"))
    # Рівно одне місце на документ (R-07)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.id"))
    title: Mapped[str] = mapped_column(String(300))
    # Нормалізована назва для пошуку кандидатів на заміну (D-37)
    title_key: Mapped[str] = mapped_column(String(300))
    source: Mapped[DocumentSource] = mapped_column(str_enum(DocumentSource))
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Коли надіслано боту — показується у відповіді (R-12)
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus), server_default=DocumentStatus.PROCESSING.value
    )
    error: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(String(500))
    # Щоб надіслати сам файл під відповіддю (D-28) без повторного завантаження
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    # «Лісовий архів»: id файлу на Диску й позначка зміни з лістингу (D-40)
    archive_file_id: Mapped[str | None] = mapped_column(String(128))
    archive_modified: Mapped[str | None] = mapped_column(String(64))
    # Видалені не беруть участі в пошуку, але лишаються в історії (D-13, D-43)
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("lineage_id", "version"),
        Index(
            "uq_documents_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # Той самий файл двічі не додається (A-09)
        Index(
            "uq_documents_alive_sha256",
            "file_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_current_place", "place_id", postgresql_where=text("is_current")),
        Index(
            "ix_documents_title_key_trgm",
            "title_key",
            postgresql_using="gin",
            postgresql_ops={"title_key": "gin_trgm_ops"},
        ),
        CheckConstraint(
            "NOT is_current OR (status = 'ready' AND deleted_at IS NULL)", name="current_is_ready"
        ),
        CheckConstraint("(source = 'archive') = (sender_id IS NULL)", name="sender_matches_source"),
    )


class Chunk(Base):
    """Фрагмент документа: текст, вектор і tsvector для гібридного пошуку."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    # Для підпису великого документа: розділ і сторінка PDF (D-18)
    section: Mapped[str | None] = mapped_column(String(300))
    page: Mapped[int | None]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    # Словник `simple`: без стемінгу, зате однаково для будь-якої мови документа
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True)
    )

    __table_args__ = (
        UniqueConstraint("document_id", "ord"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )


class Subscription(Base):
    """Підписка на нові документи місця (D-26)."""

    __tablename__ = "subscriptions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    place_id: Mapped[str] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = created_at()


class Unanswered(Base):
    """Питання, на які поради не знайшлося (D-36) — без того, хто питав."""

    __tablename__ = "unanswered"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    place_id: Mapped[str | None] = mapped_column(ForeignKey("places.id", ondelete="SET NULL"))
    asked_at: Mapped[datetime] = created_at()


class Job(Base):
    """Фонова задача; черга в Postgres переживає рестарт (ADR 0006)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus), server_default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_jobs_queued", "run_after", postgresql_where=text("status = 'queued'")),
    )


class QaLog(Base):
    """Журнал питань і відповідей для статистики й оцінок (D-50, D-52) — без автора."""

    __tablename__ = "qa_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asked_at: Mapped[datetime] = created_at()
    question: Mapped[str] = mapped_column(Text)
    place_ids: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    answer: Mapped[str | None] = mapped_column(Text)
    found: Mapped[bool]
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    rating: Mapped[Rating | None] = mapped_column(str_enum(Rating))
    rated_at: Mapped[datetime | None]


class FsmState(Base):
    """Стан діалогу aiogram (чекаємо ім'я, місце, підтвердження) — переживає рестарт."""

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
