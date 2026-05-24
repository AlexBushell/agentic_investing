# Company Intelligence Platform

A local-first company intelligence store and framework runner. The first working slice acquires annual reports and half-year results from the FCA National Storage Mechanism, parses iXBRL facts, and runs an LLM-powered IVF pre-screen.

**Current status:** end-to-end slice proven. Tesco PLC returns PASS_WITH_FLAGS / HIGH confidence from Gemma 4 via Ollama.

The full roadmap and next work are in [IVF_PreScreen_First_Slice_Roadmap.md](IVF_PreScreen_First_Slice_Roadmap.md).

---

## Local Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
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

Key values to set in `.env`:

| Variable | Description |
|---|---|
| `LLM_PROVIDER` | `ollama` or `openrouter` |
| `LLM_MODEL` | e.g. `gemma4` for Ollama |
| `OLLAMA_BASE_URL` | Default `http://localhost:11434` |
| `OPENROUTER_API_KEY` | Required when using OpenRouter |
| `DATABASE_URL` | PostgreSQL connection string for local backup/restore and future persistence work |
| `BACKUP_TARGET_DIR` | Default root directory for local backup snapshots |
| `BACKUP_PG_DUMP_PATH` | Optional explicit path to `pg_dump` if it is not on `PATH` |
| `BACKUP_PSQL_PATH` | Optional explicit path to `psql` if it is not on `PATH` |

### 5. Confirm the CLI works

```bash
research list-frameworks
```

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | Deployment secrets — API keys, DB URL, model choice |
| `config/nsm.yaml` | NSM site selectors and timeouts — versioned, update when FCA markup changes |
| `config/framework_runner.yaml` | Per-framework LLM settings — temperature, repair attempts |

---

## Backup And Restore

The project includes a local backup/restore workflow designed for a single-machine setup.

### Create a backup

```bash
research backup
research backup --target "G:\My Drive\company-intelligence-backups"
```

Each backup snapshot contains:
- a PostgreSQL SQL dump created with `pg_dump`
- a full copy of `DATA_DIR`
- a small `manifest.json`

Backups are stored in timestamped directories under `BACKUP_TARGET_DIR` by default.

### Restore from a backup

```bash
# Dry run with checklist only
research restore --from "G:\My Drive\company-intelligence-backups\20260524-123045"

# Apply after preflight passes
research restore --from "G:\My Drive\company-intelligence-backups\20260524-123045" --apply
```

Restore modes:
- `--mode full`
- `--mode files-only`
- `--mode db-only`

Safety defaults:
- restore runs a preflight checklist before any live changes
- `--apply` is blocked if preflight has any `FAIL` items
- a pre-restore safety backup is created by default
- file restore keeps a rollback copy of the previous `DATA_DIR`
- database restore validates the dump in a temporary database before touching the live target

Useful options:
- `--target-data-dir` to restore files somewhere other than `DATA_DIR`
- `--target-db-url` to restore into a different PostgreSQL database
- `--no-pre-backup` to skip the safety backup if you explicitly want that

Notes:
- the PostgreSQL checks assume a local setup where the `postgres` maintenance database exists
- temp-database validation requires a role that can create and drop databases

---

## Full Pipeline

### Acquire documents

```bash
# Annual report (XBRL package — most recent)
research ingest-nsm-report --query "Tesco PLC" --document-type annual-report \
  --out data/artifacts/nsm/tesco-plc/tesco_annual_meta.json

# Half-year report (HTML RNS announcement or XBRL — most recent)
research ingest-nsm-report --query "Tesco PLC" --document-type interim-report \
  --out data/artifacts/nsm/tesco-plc/tesco_interim_meta.json
```

Add `--headed` to watch the browser and debug site changes.

### Inspect the iXBRL facts

```bash
research extract-ixbrl-facts \
  --file "data/downloads/nsm/tesco-plc/<annual-xhtml-path>" \
  --out data/artifacts/nsm/tesco-plc/tesco_ixbrl.json

research summarize-ixbrl \
  --file "data/downloads/nsm/tesco-plc/<annual-xhtml-path>" \
  --out data/artifacts/nsm/tesco-plc/tesco_fact_set.json
```

### Build the IVF packet

```bash
research build-ivf-packet-from-ixbrl \
  --file "data/downloads/nsm/tesco-plc/<annual-xhtml-path>" \
  --out data/artifacts/nsm/tesco-plc/tesco_ivf_packet.json
```

Pass `--post-period-file` with the path from the interim meta JSON to include the half-year update in the packet.

### Run the IVF pre-screen

```bash
research run-ivf-pre-screen \
  --packet-file data/artifacts/nsm/tesco-plc/tesco_ivf_packet.json \
  --out data/artifacts/nsm/tesco-plc/tesco_ivf_result.json \
  --prompt-out data/artifacts/nsm/tesco-plc/tesco_prompt.txt \
  --raw-response-out data/artifacts/nsm/tesco-plc/tesco_raw_response.json
```

Requires a running Ollama instance (`ollama serve`) or a valid OpenRouter API key.

---

## Running Tests

```bash
# Unit tests only (no data files required)
pytest tests/unit/

# Integration tests (requires downloaded XHTML files in data/downloads/)
pytest tests/integration/ -m integration

# Regenerate golden files after intentional output changes
python tests/integration/regenerate_goldens.py
```

---

## Environment Strategy

One repo, one `.venv`, one `pyproject.toml`. Do not create separate virtual environments per subsystem. If separation is needed later, prefer dependency groups or optional extras.
