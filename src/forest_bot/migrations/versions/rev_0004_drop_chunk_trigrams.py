"""Без триграм-індексу на тексті фрагментів: пошук за словами — FTS із префіксами (ADR-05)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19 03:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_chunks_text_trgm", table_name="chunks", postgresql_using="gin")


def downgrade() -> None:
    op.create_index(
        "ix_chunks_text_trgm",
        "chunks",
        ["text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
