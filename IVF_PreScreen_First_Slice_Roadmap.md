# IVF Pre-Screen First Slice Roadmap

## Goal

Build an `NSM-acquisition-first, annual-report-led IVF pre-screen` vertical slice.

The goal of this first slice is not to build the full company intelligence platform. The goal is to prove that, given one UK-listed company, we can acquire its annual report from the FCA National Storage Mechanism, parse it, and produce a credible, auditable IVF pre-screen result from primary evidence.

---

## Current Status (May 2026)

**Single command, end-to-end:**
```bash
research run-ivf-screen --isin GB0008847096   # Tesco PLC
research run-ivf-screen --isin GB00B06QFB75   # IG Group Holdings
```

The pipeline resolves the ISIN via OpenFIGI, downloads both the annual report and latest half-year update from NSM, extracts content (iXBRL or PDF/HTML narrative), fetches live market data from yfinance, assembles a fully rendered LLM-readable packet, runs the IVF pre-screen via Ollama, and writes all outputs to `data/results/<isin>/<date>/`.

**Proven on two different issuer types and document formats:**

| Company | Annual format | Result | Confidence |
|---|---|---|---|
| Tesco PLC | XBRL package (iXBRL facts) | PASS_WITH_FLAGS | HIGH |
| IG Group Holdings | PDF (narrative text only) | PASS_WITH_FLAGS | MEDIUM |

The pipeline correctly adapts to document format: iXBRL companies get structured fact tables; PDF-only companies get narrative text extraction with the model self-calibrating to MEDIUM confidence and flagging the missing structure in Gate 1.

**What the packet contains:**
- Company identity from OpenFIGI (name, exchange, Yahoo ticker, ISIN)
- Annual report content: iXBRL facts (XBRL companies) or PDF narrative text (PDF-only companies)
- Post-period update: iXBRL facts if XBRL half-year, or HTML/PDF narrative text for RNS-style announcements
- Live market data: price, market cap, EV, 52-week range, 4-year financial history with margins (yfinance)
- Recency metadata and staleness detection

**Gate performance observed:**
- Gate 0 correctly passes operating companies and would reroute investment trusts (tested in prior runs)
- Gate 1 self-calibrates: PASS for iXBRL companies, PARTIAL for PDF-only
- Gate 2 correctly identifies ABOVE_TREND cycle position from margin history
- Gate 6 correctly identifies dislocation source when market data is present (was UNKNOWN before market data was added)

---

## Increments

### Increment 0: Project skeleton ✅

Repo runnable and shaped correctly. Typer CLI, config/env loading, Alembic setup, framework registry YAML.

### Increment 1: NSM browser automation scaffold ✅

Playwright integration, persistent download staging, CLI command, headed mode, run metadata capture.

### Increment 2: Deterministic report download ✅

File hashing, document acquisition metadata capture, idempotent re-run behaviour.

### Increment 3: Search and candidate selection ✅

Search by company name, collect candidate rows, filter by document type, ranked selection of best match.

Key behaviour confirmed in production:
- NSM renders results in two DOM batches (migrated records first, then current-system records). The code waits for `#tablerow-10` (attached state) before reading, which catches both batches.
- Candidate selection uses `(org_score, date, title_score, href+match_score)` ordering — recency is primary after org match, preventing older well-named rows from beating recent ones.
- Two document types supported in separate sessions: `annual-report` and `interim-report`.
- PDF downloads bypass the browser (httpx) to avoid browser PDF viewer interception.

### Increment 4: Core persistence ⏳ Not yet started

Database tables for `companies`, `documents`, `framework_packets`, `framework_runs`. Currently stateless — all outputs are JSON files on disk. SQLAlchemy and Alembic are in the dependency list and ready.

### Increment 5: XHTML and iXBRL extraction ✅

- `ix:nonFraction`, `ix:nonNumeric`, `ix:continuation`, `xbrli:context` fully handled.
- Unit references resolved to actual measures (e.g. `u-1` → `GBP`).
- Numeric values normalised with scale, sign, parenthesis, and suffix handling.
- Continuation chains stitched.

### Increment 6: Narrative parsing and section extraction ✅

XHTML page text, table of contents, and heuristic section detection. Markdown renderer for human inspection.

### Increment 7: Issuer routing and framework eligibility ✅ (then deliberately removed)

A keyword-based router was built, then removed after finding it could not reliably distinguish infrastructure funds (Greencoat UK Wind has `ifrs-full:RevenueAndOperatingIncome`) from true operating companies without introducing fragile heuristics. Gate 0 of the IVF pre-screen now handles archetype classification with the full narrative and numeric context available.

### Increment 8–9: Evidence extraction and structured facts ✅

Rather than a fixed metric mapping (which was initially written against Tesco-specific XBRL concepts), the pipeline now feeds **all deduped iXBRL facts** to the packet — no hardcoded concept lists, no company-specific naming. The `IXBRLFactSetBuilder` deduplicates by `(concept, period, dimensions)` and sorts numeric facts latest-period-first.

### Increment 10: Packet builder ✅

`IVFFIXBRLPacketBuilder` assembles a clean packet rendered into LLM-readable markdown by `packet_renderer.py`:

- Company identity header (name, ticker, ISIN)
- Recency section (annual age, post-period availability)
- Market context: price, EV, 52-week range, 4-year margin history table
- Income statement and balance sheet as year-on-year markdown tables (iXBRL companies)
- Annual narrative section for PDF-only companies
- Key disclosures from tagged iXBRL narratives (truncated to 1 000 chars each)
- Post-period update section (HTML/PDF narrative or merged iXBRL facts)
- Evidence gaps

Concept names are human-readable (camelCase split, namespace stripped). Values are human-scaled (£69.9bn), currency symbols resolved, GBp pence automatically normalised to GBP pounds.

### Increment 11: IVF pre-screen schema and prompt ✅

- Six-gate Pydantic schema: eligibility, data sufficiency, cycle quality, survivability, downside floor, time direction, dislocation source
- System prompt with immediate rejection triggers and gate-by-gate rules
- User prompt includes: full JSON schema, a valid example output, and the rendered packet
- Ollama structured output (`format: <json_schema>`) for grammar-constrained JSON generation
- OpenRouter `json_schema` mode for cloud models
- Repair loop on validation failure (1 attempt by default)
- Raw prompt, raw response, and repair attempts retained as audit artifacts

### Increment 12: Review loop and golden tests ✅

- 62 unit tests covering iXBRL extraction logic, fact set dedup/sorting, runner repair loop, and prompt building
- 6 integration golden file tests against real downloaded XHTML for Tesco, Gym Group, and Greencoat
- `tests/integration/regenerate_goldens.py` for intentional output changes
- `pytest.mark.integration` marker to skip data-dependent tests in CI

### Increment 13: Arelle-backed XBRL path ⏳ Deferred

The lightweight extractor is sufficient. Arelle provides taxonomy-aware labels and filing validation but adds weight. Planned for when the multi-company pipeline is stable.

### Increment 14: Recency layer ✅

- `run-ivf-screen` downloads both annual and latest interim in one session
- Post-period file type auto-detected: `.xhtml` → iXBRL facts, `.html`/`.pdf` → narrative text extraction
- `post_period_narrative` field in packet; rendered as "Post-period Update" section in the prompt
- `annual_narrative` field for PDF-only annual reports; rendered as "Annual Report (narrative)" section
- Recency metadata tracks annual age in months and post-period availability
- Evidence gap flagged when annual > 9 months old and no post-period supplied

---

## Next Work

### 1. REJECT and REROUTE validation

The pipeline has been proven on two PASS_WITH_FLAGS companies. The next priority is testing against:
- A company that should **REJECT** (going concern warning, loss-making, or no quantifiable floor)
- A company that should **REROUTE to NDF** (investment trust, REIT, or closed-ended fund)

These cases will validate Gate 0 rerouting and the immediate rejection trigger logic — the pre-screen is only half-tested if the reject path has never fired in production.

### 2. Core persistence — Increment 4

SQLAlchemy models and Alembic migrations for:

- `companies` — company identity, ticker, LEI, sector
- `documents` — document type, NSM acquisition metadata, file path, hash
- `framework_packets` — packet JSON, source document references
- `framework_runs` — result JSON, model, prompt version, latency, raw paths

Priority: currently every run is stateless. The same company can be downloaded repeatedly with no deduplication or audit trail. Persistence is required before running at any scale.

### 3. FMP structured data

FMP (Financial Modelling Prep) provides current market data and 5–10 years of normalised financials. Adding it fills the largest remaining gaps in the pre-screen packet:

- **Gate 6 (dislocation source)**: needs current price, market cap, EV. Without price data the model always returns `UNKNOWN` dislocation.
- **Gate 2 (cycle quality)**: a 5-year margin history makes cycle position judgements far more reliable than 2-year iXBRL comparatives.
- **Gate 4 (downside floor)**: tangible book value and price/tangible book from FMP gives the LLM a quantified floor anchor.
- Share count and diluted shares (not always tagged in iXBRL).

FMP requires the DB layer (Increment 4) to be useful — structured data should be stored, not re-fetched on every run.

### 4. Repair rate observability

The runner currently silently triggers a repair attempt on validation failure. Log a structured event (concept, gate, error type) each time repair fires, and track whether the repair succeeds. Without this signal there is no way to know if the single repair attempt limit is too low or if the repair prompt is effective.

### 5. RNS event summary (medium term)

Three years of RNS announcements (profit warnings, equity issuances, management changes, financing events) are critical context for Gate 3 (survivability) and Gate 6 (dislocation source). These are currently absent from the packet entirely.

---

## What To Leave Out

For now, do not build:

- Embeddings or vector search
- Full 10-year financial history
- NDF or full IVF
- Sophisticated event taxonomy
- Complete financial normalization engine
- Companies House integration
- Multi-user auth or API layer

---

## Definition Of Done For The First Slice

The first slice **is complete**:

1. ✅ Search for a real company in NSM
2. ✅ Acquire its annual report artifact
3. ✅ Extract structured iXBRL facts and narrative evidence
4. ✅ Archetype classification delegated to Gate 0 (router removed as over-engineered)
5. ✅ Build a packet with explicit evidence gaps and recency metadata
6. ✅ Run the IVF pre-screen — Tesco returned PASS_WITH_FLAGS / FULL_IVF_RUN
7. ✅ Inspect a stored valid result (JSON file with prompt and response artifacts)
