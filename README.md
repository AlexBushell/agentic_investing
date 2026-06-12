# Company Intelligence Store

A local-first company data store for gathering, persisting, and exposing company context to downstream analytical frameworks and agents.

This repository's job is the `company context store` itself — gathering, storing, and serving company data through a stable `access` layer. Analytical frameworks (e.g. the Intrinsic Value Framework pre-screen) are external consumers that live in their own repositories and integrate against `access`; none of that framework-specific logic lives here.

The current store-first blueprint lives in [docs/store_first_blueprint.md](docs/store_first_blueprint.md).

## Current Focus

The store already supports:

- company identity persistence from GLEIF (UK) and SEC/EDGAR (US)
- EDGAR and NSM document persistence
- structured fact and narrative extraction persistence
- chunking and passage retrieval for downstream consumers
- local backup and restore tooling

The main near-term focus is broadening ingestion coverage and making store access more ergonomic for agentic consumers.

## Repository Direction

The long-term structure is:

```text
gather -> store -> access -> downstream consumers
```

Where:

- `gather` acquires source data and artifacts
- `store` persists canonical company data plus provenance
- `access` provides neutral retrieval and context-building interfaces
- downstream consumers integrate against `access` from their own repositories

## Current Layout

Core source code lives in `src/research_platform/`.

Key areas today:

- `core/` for shared config and logging
- `sources/` for external source adapters
- `documents/` for parsing and extraction
- `backup.py` for local store protection workflows
- `store/` for persistence and provenance
- `access/` for framework-neutral context retrieval

## Local Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/Scripts/activate
# or on PowerShell: .\.venv\Scripts\Activate.ps1
```

### 2. Install the project in editable mode with dev dependencies

```bash
pip install -e ".[dev]"
```

### 3. Install Playwright browsers

```bash
python -m playwright install
```

### 4. Copy and fill in the environment file

```bash
cp .env.example .env
```

Important values in `.env`:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string for the store |
| `BACKUP_TARGET_DIR` | Root directory for local backup snapshots |
| `BACKUP_PG_DUMP_PATH` | Optional explicit path to `pg_dump` |
| `BACKUP_PSQL_PATH` | Optional explicit path to `psql` |

### 5. Confirm the CLI works

```bash
research init-db
```

`init-db` applies the current Alembic migrations for the company data store.

## Configuration

| File | Purpose |
|---|---|
| `.env` | Runtime configuration and secrets |
| `config/nsm.yaml` | NSM selectors and timeouts |
| `docs/store_first_blueprint.md` | Store-first architecture and refactor plan |

## Backup And Restore

The project includes a local backup and restore workflow designed to protect the company data store.

### Create a backup

```bash
research backup
research backup --target "G:\My Drive\company-intelligence-backups"
```

Each snapshot contains:

- a PostgreSQL SQL dump created with `pg_dump`
- a full copy of `DATA_DIR`
- a small `manifest.json`

### Restore from a backup

```bash
research restore --from "G:\My Drive\company-intelligence-backups\20260524-123045"
research restore --from "G:\My Drive\company-intelligence-backups\20260524-123045" --apply
```

Safety defaults:

- restore runs a preflight checklist before live changes
- `--apply` is blocked if preflight has any `FAIL` items
- a pre-restore safety backup is created by default
- file restore keeps a rollback copy of the previous `DATA_DIR`
- database restore validates the dump before touching the live target

## Store-Oriented Commands Available Today

The core `research` CLI is centered on the company data store. Key commands include:

- `research resolve-company`
- `research build-company-context`
- `research find-uk-company`
- `research find-us-company`
- `research ingest-nsm-report`
- `research ingest-nsm-company`
- `research ingest-edgar-filings`
- `research extract-text`
- `research parse-xhtml-report`
- `research extract-ixbrl-facts`
- `research show-company`
- `research list-documents`
- `research list-artifacts`
- `research show-document`
- `research show-artifact`
- `research show-facts`
- `research derive-document-context`
- `research derive-company-context`
- `research chunk-document`
- `research search-passages`
- `research backup`
- `research restore`

## Running Tests

```bash
pytest tests/unit/
pytest tests/integration/ -m integration
python tests/integration/regenerate_goldens.py
```

## Next Steps

The next repo-level implementation steps are:

1. add batch ingestion workflows for EDGAR and NSM
2. improve retrieval ergonomics for downstream agents
3. add vector-backed passage retrieval when needed
4. document and stabilize the `access` layer contract for external framework consumers
