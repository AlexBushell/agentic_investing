"""Initial company store schema.

Revision ID: 20260611_01
Revises:
Create Date: 2026-06-11 20:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260611_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("company_id"),
    )

    op.create_table(
        "identifiers",
        sa.Column("identifier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_type", sa.String(length=32), nullable=False),
        sa.Column("id_value", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("identifier_id"),
    )
    op.create_index("ix_identifiers_company_id", "identifiers", ["company_id"], unique=False)
    op.create_index("ix_identifiers_type_value", "identifiers", ["id_type", "id_value"], unique=True)

    op.create_table(
        "listings",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("exchange_code", sa.String(length=32), nullable=True),
        sa.Column("security_type", sa.String(length=128), nullable=True),
        sa.Column("market_sector", sa.String(length=128), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("listing_id"),
    )
    op.create_index("ix_listings_company_id", "listings", ["company_id"], unique=False)

    op.create_table(
        "documents",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("document_role", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index("ix_documents_company_id", "documents", ["company_id"], unique=False)

    op.create_table(
        "document_artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_kind", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.String(length=128), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_document_artifacts_document_id", "document_artifacts", ["document_id"], unique=False)
    op.create_index("ix_document_artifacts_file_hash", "document_artifacts", ["file_hash"], unique=False)

    op.create_table(
        "document_extractions",
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extraction_type", sa.String(length=64), nullable=False),
        sa.Column("extractor_name", sa.String(length=128), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["document_artifacts.artifact_id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
        sa.PrimaryKeyConstraint("extraction_id"),
    )
    op.create_index("ix_document_extractions_document_id", "document_extractions", ["document_id"], unique=False)

    op.create_table(
        "facts",
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("instant_date", sa.Date(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("value_numeric", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
        sa.Column("source_confidence", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
        sa.ForeignKeyConstraint(["extraction_id"], ["document_extractions.extraction_id"]),
        sa.PrimaryKeyConstraint("fact_id"),
    )
    op.create_index("ix_facts_company_id", "facts", ["company_id"], unique=False)
    op.create_index("ix_facts_document_id", "facts", ["document_id"], unique=False)
    op.create_index("ix_facts_extraction_id", "facts", ["extraction_id"], unique=False)

    op.create_table(
        "narrative_extracts",
        sa.Column("narrative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_name", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("source_confidence", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
        sa.ForeignKeyConstraint(["extraction_id"], ["document_extractions.extraction_id"]),
        sa.PrimaryKeyConstraint("narrative_id"),
    )
    op.create_index("ix_narrative_extracts_company_id", "narrative_extracts", ["company_id"], unique=False)
    op.create_index("ix_narrative_extracts_document_id", "narrative_extracts", ["document_id"], unique=False)
    op.create_index("ix_narrative_extracts_extraction_id", "narrative_extracts", ["extraction_id"], unique=False)

    op.create_table(
        "ingestion_runs",
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("ingestion_run_id"),
    )
    op.create_index("ix_ingestion_runs_company_id", "ingestion_runs", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_company_id", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_narrative_extracts_extraction_id", table_name="narrative_extracts")
    op.drop_index("ix_narrative_extracts_document_id", table_name="narrative_extracts")
    op.drop_index("ix_narrative_extracts_company_id", table_name="narrative_extracts")
    op.drop_table("narrative_extracts")
    op.drop_index("ix_facts_extraction_id", table_name="facts")
    op.drop_index("ix_facts_document_id", table_name="facts")
    op.drop_index("ix_facts_company_id", table_name="facts")
    op.drop_table("facts")
    op.drop_index("ix_document_extractions_document_id", table_name="document_extractions")
    op.drop_table("document_extractions")
    op.drop_index("ix_document_artifacts_file_hash", table_name="document_artifacts")
    op.drop_index("ix_document_artifacts_document_id", table_name="document_artifacts")
    op.drop_table("document_artifacts")
    op.drop_index("ix_documents_company_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_listings_company_id", table_name="listings")
    op.drop_table("listings")
    op.drop_index("ix_identifiers_type_value", table_name="identifiers")
    op.drop_index("ix_identifiers_company_id", table_name="identifiers")
    op.drop_table("identifiers")
    op.drop_table("companies")

