# Store-First Blueprint

**Status:** Proposed  
**Date:** 2026-06-11  
**Scope:** Refactor the repository so the durable product is the company data store and downstream analytical frameworks live outside the core source tree.

## 1. Summary

This repository should be treated as a **company context store**.

Its job is to:

- gather company data from primary and supporting sources
- store raw and derived company data with provenance
- expose framework-neutral company context to downstream consumers

Its job is **not** to:

- own analytical frameworks
- store framework outputs as part of the company store
- let one test framework define the core architecture

Analytical frameworks such as the Intrinsic Value Framework pre-screen are external consumers that live in their own repositories and depend on the neutral `access` layer from the store. None of that framework-specific logic lives in this repository.

## 2. Target Boundary

### In scope for the store

- company identity and identifier resolution
- listings / instruments
- source document acquisition
- raw artifact storage and hashing
- structured extraction from documents
- narrative extraction from documents
- market and reference snapshots
- provenance, timestamps, source metadata, extractor versions
- storage, retrieval, and framework-neutral access to company context

### Out of scope for the store

- analytical framework packet/prompt/result schemas
- analytical framework orchestration commands as core product paths
- storage of analysis runs, framework results, or prompt artifacts in the core data model

## 3. Architectural Shape

The core repo should be organized around three responsibilities:

1. `gather`
   Resolve identity, fetch source data, download artifacts, and collect source metadata.

2. `store`
   Persist canonical company data, artifacts, extractions, facts, narratives, and snapshots.

3. `access`
   Provide stable, framework-neutral ways for agents or external consumers to retrieve company context.

Analytical frameworks become external consumers:

```text
Raw sources
  -> gather
  -> store
  -> access
  -> downstream frameworks (external repos)
```

## 4. Proposed Repository Layout

### Core repo layout

```text
src/research_platform/
  __init__.py
  cli.py
  backup.py

  core/
    config.py
    logging.py

  sources/
    gleif.py
    sec_tickers.py
    edgar.py
    nsm.py
    nsm_manifest.py
    market.py

  documents/
    xhtml_parser.py
    xhtml_markdown.py
    ixbrl_extractor.py
    ixbrl_summary.py
    text_extractor.py

  store/
    __init__.py
    models.py
    session.py
    repositories/
    services/
    migrations/

  access/
    __init__.py
    company_context.py
    queries.py
    dto.py
```

## 5. File Moves From The Current Repo

### Keep in `src/research_platform/`

- `core/`
- `sources/`
- most of `documents/`
- `backup.py`
- store-related CLI commands

## 6. Schema V1

The first persistence schema should store only company data used by analysis systems.

### 6.1 `companies`

Purpose:
- canonical company identity
- stable internal ID
- issuer-level metadata

Suggested fields:
- `company_id`
- `name`
- `legal_name`
- `country`
- `created_at`
- `updated_at`

### 6.2 `identifiers`

Purpose:
- separate identifier history from the company record
- allow one company to carry multiple identifiers cleanly

Suggested fields:
- `identifier_id`
- `company_id`
- `id_type` such as `ISIN`, `FIGI`, `LEI`, `TICKER`, `YAHOO_TICKER`
- `id_value`
- `source`
- `is_primary`
- `valid_from`
- `valid_to`

### 6.3 `listings`

Purpose:
- represent tradable instruments and exchange-specific context

Suggested fields:
- `listing_id`
- `company_id`
- `ticker`
- `exchange_code`
- `security_type`
- `market_sector`
- `currency`
- `is_primary`

### 6.4 `documents`

Purpose:
- register source documents at the business level

Suggested fields:
- `document_id`
- `company_id`
- `source`
- `document_role` such as `ANNUAL_REPORT`, `INTERIM_REPORT`, `TRADING_UPDATE`
- `title`
- `publication_date`
- `period_end`
- `source_url`
- `source_reference`
- `created_at`

### 6.5 `document_artifacts`

Purpose:
- track physical files and raw representations
- support deduplication and provenance

Suggested fields:
- `artifact_id`
- `document_id`
- `artifact_kind` such as `PRIMARY_FILE`, `HTML_SNAPSHOT`, `SCREENSHOT`, `RAW_JSON`
- `file_path`
- `file_hash`
- `mime_type`
- `format`
- `size_bytes`
- `created_at`

Rule:
- deduplication should happen at the artifact level via content hash, not by filename

### 6.6 `document_extractions`

Purpose:
- store extraction outputs separately from raw artifacts
- allow reprocessing with new extractor versions

Suggested fields:
- `extraction_id`
- `document_id`
- `artifact_id`
- `extraction_type` such as `IXBRL_FACT_SET`, `NARRATIVE_TEXT`, `XHTML_PARSE`
- `extractor_name`
- `extractor_version`
- `payload_json`
- `created_at`

### 6.7 `facts`

Purpose:
- store normalized structured company facts from iXBRL or other structured sources

Suggested fields:
- `fact_id`
- `company_id`
- `document_id`
- `extraction_id`
- `concept`
- `namespace`
- `period_start`
- `period_end`
- `instant_date`
- `unit`
- `value_numeric`
- `value_text`
- `dimensions_json`
- `source_confidence`
- `created_at`

### 6.8 `narrative_extracts`

Purpose:
- store narrative passages with explicit provenance

Suggested fields:
- `narrative_id`
- `company_id`
- `document_id`
- `extraction_id`
- `section_name`
- `text`
- `char_count`
- `source_confidence`
- `created_at`

### 6.9 `market_snapshots`

Purpose:
- store point-in-time market and lightweight financial reference data

Suggested fields:
- `snapshot_id`
- `company_id`
- `listing_id`
- `source`
- `as_of_date`
- `currency`
- `price`
- `market_cap`
- `enterprise_value`
- `shares_outstanding`
- `week_52_high`
- `week_52_low`
- `payload_json`
- `created_at`

### 6.10 `ingestion_runs`

Purpose:
- operational audit of gather/store activity
- not analysis history

Suggested fields:
- `ingestion_run_id`
- `company_id`
- `run_type` such as `IDENTITY_SYNC`, `NSM_SYNC`, `MARKET_REFRESH`, `DOCUMENT_EXTRACT`
- `status`
- `started_at`
- `finished_at`
- `details_json`

## 7. Access Layer Contract

Frameworks should not reach into raw extraction modules or files directly. They should consume store data through a stable access interface.

Suggested first interface:

```python
class CompanyContextStore:
    def get_company(self, company_ref: str) -> CompanyRecord: ...
    def get_primary_listing(self, company_id: str) -> ListingRecord | None: ...
    def get_latest_documents(self, company_id: str) -> list[DocumentRecord]: ...
    def get_document_artifacts(self, document_id: str) -> list[ArtifactRecord]: ...
    def get_fact_set(self, company_id: str, *, document_role: str | None = None) -> FactSet: ...
    def get_narrative_extracts(self, company_id: str, *, document_role: str | None = None) -> list[NarrativeExtract]: ...
    def get_market_snapshot(self, company_id: str) -> MarketSnapshot | None: ...
    def build_company_context(self, company_ref: str) -> CompanyContextBundle: ...
```

The important point is not the exact method names. The important point is that downstream consumers ask the store for company context instead of reconstructing it themselves from the filesystem.

## 8. CLI Redesign

The CLI should be re-centered around store operations.

### Primary commands to keep or add

- `research init-db`
- `research register-company --isin ...`
- `research sync-company-identity --isin ...`
- `research sync-company-documents --company ...`
- `research extract-document --document-id ...`
- `research refresh-market-data --company ...`
- `research build-company-context --company ...`
- `research show-company --company ...`
- `research list-documents --company ...`
- `research show-document --document-id ...`

The backup and restore commands remain valid because they protect the store itself.

## 9. Refactor Stages

This should be done in staged increments so the repo stays usable throughout.

### Stage 1: Declare the boundary

Deliverables:
- this blueprint
- README rewrite to describe the repo as a company data store

Success criteria:
- the repo description is no longer framework-led

### Stage 2: Introduce store modules

Deliverables:
- `src/research_platform/store/`
- `src/research_platform/access/`
- SQLAlchemy models and Alembic setup for schema v1

Success criteria:
- a company can be registered and stored without involving any downstream framework code

### Stage 3: Persist current ingestion outputs

Deliverables:
- GLEIF (UK) and SEC/EDGAR (US) results stored as company, identifiers, and listings
- NSM acquisition stored as documents plus document artifacts
- market fetch stored as market snapshots
- extraction outputs stored as document extractions, facts, and narratives

Success criteria:
- the current pipeline can populate the store end-to-end without producing any framework-specific output

### Stage 4: Stabilize the access layer as an external contract

Deliverables:
- `access` interfaces documented as the stable integration surface for external framework repos
- no framework-specific config or modules remain in the core settings model

Success criteria:
- an external framework repo can build company context using only `access`, without touching `sources`, `documents`, or `store` internals

### Stage 5: Simplify the core CLI

Deliverables:
- store-first commands promoted
- no framework-specific entry points in the core CLI

Success criteria:
- a new user can understand the store without reading any framework code

## 10. Current Code Mapping

This is the practical mapping from today's code to the target architecture.

### Already aligned with the store

- `sources/gleif.py`
- `sources/sec_tickers.py`
- `sources/edgar.py`
- `sources/nsm.py`
- `sources/nsm_manifest.py`
- `sources/market.py`
- `documents/ixbrl_extractor.py`
- `documents/ixbrl_summary.py`
- `documents/text_extractor.py`
- `documents/xhtml_parser.py`
- `documents/xhtml_markdown.py`
- `backup.py`

## 11. Key Design Rules

These rules should guide every refactor decision:

1. If a table or module exists only because one downstream consumer needs it, it does not belong in the store.
2. Store provenance-rich company data, not framework judgments.
3. Separate raw artifacts from extraction outputs.
4. Make extraction outputs versionable and re-runnable.
5. Make consumers depend on `access`, not on `sources` or `documents` internals.
6. Preserve filesystem artifacts for audit and reproducibility, but make the database the registry of truth.
7. The store must still make sense if every downstream framework repo is deleted.

## 12. Immediate Next Actions

Recommended next implementation steps:

1. Update `README.md` to reflect the store-first mission.
2. Add `store/` and `access/` packages with empty scaffolding and clear module docstrings.
3. Add schema v1 SQLAlchemy models for `companies`, `identifiers`, `listings`, `documents`, `document_artifacts`, `document_extractions`, `facts`, `narrative_extracts`, `market_snapshots`, and `ingestion_runs`.
4. Refactor `lookup-isin` and NSM ingestion paths to write to the store.

That sequence preserves momentum while changing the architecture for the right long-term outcome.
