"""Схема домену: місця, звірі, документи з версіями, фрагменти, черга, журнал.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 20:32:05.881867
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_bot_settings")),
    )
    op.create_table(
        "fsm_states",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_fsm_states")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "done",
                "failed",
                name="jobstatus",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name=op.f("ck_jobs_jobstatus")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_queued",
        "jobs",
        ["run_after"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_table(
        "places",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "forest",
                "part",
                "mitten",
                name="placekind",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('forest', 'part', 'mitten')", name=op.f("ck_places_placekind")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_places")),
    )
    op.create_table(
        "qa_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "asked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("place_ids", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("chunk_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "rating",
            sa.Enum(
                "useful",
                "wrong",
                name="rating",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rating IN ('useful', 'wrong')", name=op.f("ck_qa_log_rating")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_log")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("real_name", sa.String(length=100), nullable=True),
        sa.Column("name_key", sa.String(length=100), nullable=True),
        sa.Column("member_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "joined_via",
            sa.Enum(
                "first",
                "invite",
                name="joinedvia",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("joined_via IN ('first', 'invite')", name=op.f("ck_users_joinedvia")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("name_key", name=op.f("uq_users_name_key")),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("place_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("title_key", sa.String(length=300), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "archive",
                "telegram",
                name="documentsource",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "processing",
                "ready",
                "failed",
                name="documentstatus",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="processing",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=True),
        sa.Column("archive_file_id", sa.String(length=128), nullable=True),
        sa.Column("archive_modified", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(source = 'archive') = (sender_id IS NULL)",
            name=op.f("ck_documents_sender_matches_source"),
        ),
        sa.CheckConstraint(
            "NOT is_current OR (status = 'ready' AND deleted_at IS NULL)",
            name=op.f("ck_documents_current_is_ready"),
        ),
        sa.CheckConstraint(
            "source IN ('archive', 'telegram')", name=op.f("ck_documents_documentsource")
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'failed')", name=op.f("ck_documents_documentstatus")
        ),
        sa.ForeignKeyConstraint(
            ["place_id"], ["places.id"], name=op.f("fk_documents_place_id_places")
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["users.id"], name=op.f("fk_documents_sender_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("lineage_id", "version", name=op.f("uq_documents_lineage_id_version")),
    )
    op.create_index(
        "ix_documents_current_place",
        "documents",
        ["place_id"],
        unique=False,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(op.f("ix_documents_lineage_id"), "documents", ["lineage_id"], unique=False)
    op.create_index(
        "ix_documents_title_key_trgm",
        "documents",
        ["title_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title_key": "gin_trgm_ops"},
    )
    op.create_index(
        "uq_documents_alive_sha256",
        "documents",
        ["file_sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_documents_current_lineage",
        "documents",
        ["lineage_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "place_parts",
        sa.Column("mitten_id", sa.String(length=64), nullable=False),
        sa.Column("part_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["mitten_id"],
            ["places.id"],
            name=op.f("fk_place_parts_mitten_id_places"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["places.id"],
            name=op.f("fk_place_parts_part_id_places"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mitten_id", "part_id", name=op.f("pk_place_parts")),
    )
    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("place_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_subscriptions_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "place_id", name=op.f("pk_subscriptions")),
    )
    op.create_table(
        "unanswered",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("place_id", sa.String(length=64), nullable=True),
        sa.Column(
            "asked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_unanswered_place_id_places"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unanswered")),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', text)", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("document_id", "ord", name=op.f("uq_chunks_document_id_ord")),
    )
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False)
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_text_trgm",
        "chunks",
        ["text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks", postgresql_using="gin")
    op.drop_index(
        "ix_chunks_text_trgm",
        table_name="chunks",
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_chunks_embedding_hnsw",
        table_name="chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("unanswered")
    op.drop_table("subscriptions")
    op.drop_table("place_parts")
    op.drop_index(
        "uq_documents_current_lineage",
        table_name="documents",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(
        "uq_documents_alive_sha256",
        table_name="documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_documents_title_key_trgm",
        table_name="documents",
        postgresql_using="gin",
        postgresql_ops={"title_key": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_documents_lineage_id"), table_name="documents")
    op.drop_index(
        "ix_documents_current_place", table_name="documents", postgresql_where=sa.text("is_current")
    )
    op.drop_table("documents")
    op.drop_table("users")
    op.drop_table("qa_log")
    op.drop_table("places")
    op.drop_index(
        "ix_jobs_queued", table_name="jobs", postgresql_where=sa.text("status = 'queued'")
    )
    op.drop_table("jobs")
    op.drop_table("fsm_states")
    op.drop_table("bot_settings")
