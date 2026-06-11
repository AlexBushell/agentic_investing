# Company Intelligence Store

A local-first company data store for gathering, persisting, and exposing company context to downstream analytical frameworks and agents.

The product in this repository is the `company context store`, not any single analysis framework. The current IVF pre-screen work is being treated as a test consumer of the store rather than the architectural center of the project.

The current store-first blueprint lives in [docs/store_first_blueprint.md](docs/store_first_blueprint.md).

## Current Focus

The repository already has useful ingestion and extraction building blocks:

- OpenFIGI-based identifier resolution
- FCA NSM filing acquisition
- iXBRL extraction and fact-set building
- narrative text extraction for HTML and PDF documents
- local backup and restore tooling

The next major milestone is persistence:

- define the canonical store schema
- write ingestion outputs into the store
- expose framework-neutral company context through an access layer

## Repository Direction

The long-term structure is:

```text
gather -> store -> access -> downstream consumers
```

Where:

- `gather` acquires source data and artifacts
- `store` persists canonical company data plus provenance
- `access` provides neutral retrieval and context-building interfaces
- downstream consumers such as IVF sit outside the core source tree

## Current Layout

Core source code lives in `src/research_platform/`.

Key areas today:

- `core/` for shared config and logging
- `sources/` for external source adapters
- `documents/` for parsing and extraction
- `backup.py` for local store protection workflows
- `store/` for persistence scaffolding
- `access/` for framework-neutral context access scaffolding

Experimental or downstream consumers should live outside the core tree under `labs/`.

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
| `OPENFIGI_API_KEY` | Optional API key for OpenFIGI |
| `LLM_PROVIDER` | Still used by experimental framework consumers |
| `LLM_MODEL` | Still used by experimental framework consumers |

### 5. Confirm the CLI works

```bash
research init-db
```

`init-db` is still a scaffold today. The store schema implementation is the next major build step.

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

The CLI still contains some legacy framework-oriented commands, but the store-relevant commands available now include:

- `research lookup-isin`
- `research ingest-nsm-report`
- `research extract-text`
- `research parse-xhtml-report`
- `research extract-ixbrl-facts`
- `research summarize-ixbrl`
- `research fetch-market-data`
- `research backup`
- `research restore`

These are transitional building blocks on the way to a store-first CLI.

## Running Tests

```bash
pytest tests/unit/
pytest tests/integration/ -m integration
python tests/integration/regenerate_goldens.py
```

## Next Steps

The next repo-level implementation steps are:

1. add the store schema and persistence layer
2. write current ingestion outputs into the store
3. expose a neutral company-context access layer
4. move IVF-specific work into `labs/ivf_pre_screen/`

That sequence keeps the useful ingestion work while making the store the durable product.
