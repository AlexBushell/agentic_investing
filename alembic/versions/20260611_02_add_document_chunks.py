"""Add document_chunks table.

Revision ID: 20260611_02
Revises: 20260611_01
Create Date: 2026-06-11 21:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260611_02"
down_revision = "20260611_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("narrative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_name", sa.String(length=255), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("source_confidence", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
        sa.ForeignKeyConstraint(["extraction_id"], ["document_extractions.extraction_id"]),
        sa.ForeignKeyConstraint(["narrative_id"], ["narrative_extracts.narrative_id"]),
        sa.PrimaryKeyConstraint("chunk_id"),
    )
    op.create_index("ix_document_chunks_company_id", "document_chunks", ["company_id"], unique=False)
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"], unique=False)
    op.create_index("ix_document_chunks_extraction_id", "document_chunks", ["extraction_id"], unique=False)
    op.create_index("ix_document_chunks_narrative_id", "document_chunks", ["narrative_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_document_chunks_narrative_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_extraction_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_company_id", table_name="document_chunks")
    op.drop_table("document_chunks")
