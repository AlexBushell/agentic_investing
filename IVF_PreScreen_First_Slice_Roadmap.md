# IVF Pre-Screen First Slice Roadmap

## Goal

Build an `NSM-acquisition-first, annual-report-led IVF pre-screen` vertical slice.

The goal of this first slice is not to build the full company intelligence platform. The goal is to prove that, given one UK-listed company, we can acquire its annual report from the FCA National Storage Mechanism, parse it, and produce a credible, auditable IVF pre-screen result from primary evidence.

The key change to the earlier plan is that NSM document acquisition is now the first hard problem. Since there is no usable NSM API for this workflow, browser automation becomes part of the core path rather than a later convenience.

One important implementation insight now confirmed by Tesco's filing is that NSM may deliver the annual report as an ESEF or iXBRL package rather than a standalone PDF. That means the first slice should treat the filed XHTML and tagged facts as first-class inputs, with PDF as an optional human-friendly derivative artifact rather than the canonical source.

Another important architecture insight is that not every successfully ingested issuer should flow into IVF. Some issuers, such as closed-end funds, investment trusts, asset-backed vehicles, banks, or shells, may be valid filings to ingest but invalid candidates for the Intrinsic Value Framework. The platform therefore needs a framework-neutral routing or eligibility step before any IVF packet is built.

## What This First Slice Should Do

Input:

- `LSE:XYZ`
- a company name or search term for NSM lookup
- optionally a manually supplied report artifact at first if NSM search or download is brittle

Output:

- stored company record
- stored document acquisition metadata
- stored annual report artifact zip or PDF
- stored extracted primary XHTML when present
- stored issuer routing and framework eligibility assessment
- parsed annual report text, sections, and iXBRL facts
- extracted IVF-relevant evidence blocks
- minimal IVF pre-screen packet
- valid strict JSON IVF pre-screen result
- stored raw prompt, raw response, and validated result

This gives us a real end-to-end proof without needing full ingestion breadth.

## Recommended Stack

Use the stack from the spec, but keep it narrow for `v0`:

- Python `3.12+`
- PostgreSQL `18` on native Windows
- `pgvector` when available, but not required for the first slice
- SQLAlchemy
- Alembic
- Pydantic
- Typer
- `httpx`
- `tenacity`
- `python-dotenv`
- `PyMuPDF` for PDF extraction
- stdlib XML parsing for the first lightweight iXBRL extractor
- Playwright for NSM browser automation and downloads
- OpenAI-compatible LLM client for JSON framework runs
- local filesystem for PDFs, parsed text, prompts, packets, and results

Planned later addition:

- `Arelle` as the production-grade XBRL processor once the first slice is proven

Arelle is not required for `v0`, but it should be an explicit roadmap item because it gives us taxonomy-aware labels, filing validation, richer context handling, and a path we can reuse for SEC EDGAR work later.

For `v0`, do not require embeddings yet. Keep `pgvector` in the stack because it is part of the target architecture, but do not let vector search block the first slice.

## Architecture For The First Slice

Keep the target architecture, but only implement the minimum live path:

- `core/`
  - config, enums, ids, logging
- `db/`
  - session, models, migrations
- `schemas/`
  - company, documents, evidence, frameworks
- `sources/`
  - `nsm.py`
  - `manual.py` for fallback local registration
- `documents/`
  - storage, XHTML parsing, iXBRL extraction, PDF parsing when available
- `routing/`
  - issuer archetype detection
  - framework eligibility
- `intelligence/`
  - annual-report evidence extraction
- `data_products/`
  - minimal packet components
- `frameworks/ivf_pre_screen/`
  - packet, prompt, schema
- `llm/`
  - client, JSON runner, validation
- `cli.py`

## Guiding Approach

Use a narrow acquisition target first:

1. open NSM
2. search for a company
3. identify an annual report result
4. download the filing artifact
5. store the artifact and metadata

Do not start by trying to fully automate all document discovery logic. Start with deterministic acquisition for a report of our choosing, then harden search and selection once the downstream parsing and packet-building path exists.

Also do not assume that every acquired annual report should immediately feed IVF. Add an early routing decision once enough structured facts and narrative evidence exist.

## Increments

### Increment 0: Project skeleton

Goal: repo is runnable and shaped correctly.

Build:

- `pyproject.toml`
- `src/research_platform/`
- Typer CLI with placeholder commands
- config and env loading
- native Windows PostgreSQL configuration assumptions
- Alembic setup
- framework registry YAML

Success:

- `research list-frameworks`
- `research init-db`

### Increment 1: NSM browser automation scaffold

Goal: prove that we can drive a browser and control file downloads reliably.

Build:

- Playwright integration
- persistent download staging folder
- CLI command for NSM browser automation
- support for headed mode while developing
- simple run metadata capture

Target command:

- `research ingest-nsm-report --query "Company Name" --headed`

Success:

- browser launches
- NSM page opens
- downloads are captured in a controlled local folder
- failures are surfaced cleanly

### Increment 2: Deterministic report download

Goal: reliably download a specific annual report of our choosing.

Build:

- manual URL or deterministic page-flow mode
- download completion handling
- file hashing
- document acquisition metadata capture

Prefer the smallest dependable target first:

- given a chosen NSM result or document page, download the report

Success:

- annual report artifact copied and stored locally
- acquisition metadata captured
- hash stored
- idempotent re-run behavior

### Increment 3: Search and candidate selection

Goal: move from manual document selection to company-driven NSM search.

Build:

- search by company name or ticker
- collect candidate rows
- basic filtering for annual reports
- ranked selection of likely best match
- manual review output if confidence is low

Success:

- search returns candidate documents
- likely annual report can be selected deterministically for known examples
- ambiguous cases are explicit rather than silently guessed

### Increment 4: Core persistence

Goal: store the minimum entities needed for one-company, one-document, one-run flow.

Tables to implement first:

- `companies`
- `documents`
- `evidence_items`
- `framework_packets`
- `framework_runs`

Add soon after:

- `issuer_routing_runs`
- `issuer_routing_profiles`

Optional early:

- `raw_api_responses`
- `document_acquisition_runs`

Postpone most of the broader schema until needed. The spec's full schema is good, but we do not need every table to prove the first slice.

Success:

- create a company
- register a document
- store a routing outcome
- store a packet
- store a run result

### Increment 5: XHTML and iXBRL extraction

Goal: convert the filed annual report artifact into structured facts and usable narrative text.

Build:

- detect whether the acquired artifact is a PDF, zip, or XHTML
- extract NSM zip packages
- identify the primary report file, typically `reports/*.xhtml`
- lightweight iXBRL fact extraction from:
  - `ix:nonFraction`
  - `ix:nonNumeric`
  - `ix:continuation`
  - `xbrli:context`
- parsed fact artifacts on disk as JSON or JSONL
- summary command for headline numbers and tagged disclosures

Why this matters:

- the iXBRL semantic layer is a better source of truth for financial facts than asking an LLM to re-read tables
- it preserves concepts, periods, units, scale, and dimensions
- it will generalize better to future EDGAR support

Success:

- structured facts extracted from a real NSM filing
- numeric values normalized correctly with scale and sign
- continuation-linked narrative facts are stitched together
- latest-period summary metrics can be produced deterministically

### Increment 6: Narrative parsing and section extraction

Goal: convert the report into usable text and rough sections.

Build:

- XHTML visible-text extraction for the report's narrative layer
- PDF text extraction with `PyMuPDF`
- parsed text artifact on disk
- heuristic section splitter for headings such as:
  - strategy / business model
  - chairman / CEO review
  - financial review
  - risk factors
  - going concern
  - viability
  - audit report
  - borrowings / debt
  - liquidity
  - notes to accounts
  - segment reporting

Do not over-engineer sectioning yet. Start with heading heuristics and page windows.

Success:

- text extracted from a real annual report
- sections identified well enough to target retrieval
- parse failures recorded cleanly

### Increment 7: Issuer routing and framework eligibility

Goal: determine whether the issuer is structurally suitable for IVF before building any IVF packet.

Build:

- issuer archetype classification, for example:
  - operating company
  - closed-end fund / investment trust
  - REIT
  - financial institution
  - holdco
  - shell / SPAC
- framework eligibility decision:
  - `IVF_ELIGIBLE`
  - `IVF_INELIGIBLE`
  - `MANUAL_REVIEW`
- explicit ineligibility reasons
- preferred next framework or route

Greencoat UK Wind is a good example of why this step matters:

- the platform should ingest it successfully
- the platform should understand it successfully
- but it should not automatically route it into IVF

Success:

- at least one operating company is marked IVF-eligible
- at least one non-operating-company issuer is marked IVF-ineligible or rerouted
- routing output is stored and auditable

### Increment 8: IVF-critical evidence extraction

Goal: pull the specific evidence the pre-screen actually needs.

Extract first:

- business description and operating status
- revenue and profitability history
- debt and liquidity
- maturities and refinancing
- going concern
- audit qualifications or emphasis
- share count and issuance or dilution clues
- segment context
- asset base, tangible book, impairment, and goodwill clues
- major risks relevant to survivability or obsolescence

Store evidence as:

- `topic`
- `label`
- `snippet`
- `source_document_id`
- `page`
- `section`
- confidence or extraction method

For `v0`, use deterministic extraction plus keyword rules first. LLM extraction can come later.

Success:

- evidence items are queryable and traceable back to page and section
- packet can cite real report evidence

### Increment 9: Minimal structured facts from the annual report

Goal: stop depending on external APIs for the first useful packet.

Extract or store manually where needed:

- company name
- ticker
- sector if available
- report date and fiscal year end
- revenue, operating profit, net income
- cash
- total debt
- net debt if inferable
- shares outstanding if available
- tangible equity clues

Prefer the iXBRL fact layer for these wherever possible, and fall back to narrative extraction only when the data is not tagged.

Success:

- packet has enough hard facts to support the framework
- unknowns are explicit, not silently omitted

### Increment 10: Minimal packet builder

Goal: build an IVF pre-screen packet from annual-report-derived facts and evidence.

Packet should include at least:

- issuer routing profile
- company profile
- report metadata
- key financial snapshot
- debt and liquidity evidence
- going concern and audit evidence
- downside-floor evidence
- time-direction clues
- explicit evidence gaps
- source references

This packet should be compact. The point is to help the model reason, not dump the whole report.

Success:

- packet JSON saved to disk and DB
- packet is stable enough to inspect manually

### Increment 11: IVF pre-screen schema and prompt

Goal: run the first real framework.

Build:

- Pydantic schema for IVF pre-screen result
- prompt template based on the spec
- JSON runner
- validation
- one repair retry
- raw response retention

Success:

- `research run-framework LSE:XYZ --framework IVF_PRE_SCREEN`
- valid strict JSON result stored

### Increment 12: Review loop and golden tests

Goal: make the slice trustworthy before expanding scope.

Build tests for:

- NSM acquisition metadata shaping
- file hashing
- iXBRL fact extraction
- XHTML narrative parsing
- issuer routing and eligibility
- section detection
- evidence extraction
- packet building
- JSON validation

Add golden files for one to two companies:

- acquisition metadata output
- annual report parsed output
- iXBRL facts output
- packet output
- expected result shape

Success:

- changes to acquisition, extraction, and prompting are detectable
- pipeline is not fragile

### Increment 13: Arelle-backed XBRL path

Goal: add a production-grade XBRL engine after the first slice is proven.

Build:

- optional Arelle-backed extractor path alongside the lightweight extractor
- taxonomy-aware concept labels
- filing validation
- richer unit and dimension handling
- comparison tests between lightweight extraction and Arelle output

Why later instead of first:

- the lightweight extractor is enough to prove the end-to-end NSM flow now
- Arelle adds weight and complexity, but is likely the right long-term XBRL core
- this also positions the platform for future SEC EDGAR ingestion

Success:

- Arelle can parse the same Tesco-style NSM filing
- concept labels and validation are available when needed
- the pipeline can switch between lightweight and Arelle-backed extraction paths

### Increment 14: Recency layer

Goal: patch the main weakness of annual-report-first.

Add next:

- latest interim or half-year report
- or latest trading update
- explicit `post-period update available / missing` logic

This is probably the highest-value addition after the first slice.

## What To Leave Out Initially

For the first slice, do not build:

- embeddings
- vector search
- hybrid retrieval
- RNS classifier
- Companies House
- macro sources
- full 10-year financial history
- NDF or full IVF
- sophisticated event taxonomy
- complete financial normalization engine

Do not make Arelle a hard dependency for the first proving slice. Keep it as the planned upgrade path once the surrounding ingestion and packet workflow is stable.

These are good phase-2 or phase-3 items, but they dilute focus right now.

## Suggested Command Set For v0

Target these commands first:

- `research init-db`
- `research list-frameworks`
- `research ingest-nsm-report --query "Company Name" --headed`
- `research ingest-nsm-report --query "Company Name" --document-type annual-report`
- `research register-annual-report LSE:XYZ --file ./report.zip`
- `research extract-ixbrl-facts --file ./report.xhtml`
- `research summarize-ixbrl --file ./report.xhtml`
- `research route-issuer LSE:XYZ`
- `research parse-documents LSE:XYZ`
- `research extract-evidence LSE:XYZ`
- `research build-packet LSE:XYZ --framework IVF_PRE_SCREEN`
- `research run-framework LSE:XYZ --framework IVF_PRE_SCREEN`
- `research show-result LSE:XYZ --framework IVF_PRE_SCREEN --latest`

Later:

- `research refresh-intelligence LSE:XYZ --mode light`

## Key Design Rules To Hold Firmly

Be strict about these from day one:

- facts and evidence are framework-neutral
- issuer routing should be framework-neutral and precede IVF packet construction
- framework judgement lives only in packet and result layers
- unknowns must stay unknown
- missing evidence must be explicit
- every evidence item should trace back to a source page and section
- every acquired report should retain its acquisition metadata
- semantic facts should be preferred over visual table re-reading where tagged data exists
- store raw prompt and raw LLM output for auditability
- keep the packet compact and decision-oriented
- keep manual document registration as a fallback

## Practical Build Sequence

Recommended order:

1. scaffold repo, CLI, and config
2. add Playwright-based NSM downloader skeleton
3. make deterministic annual-report artifact download work
4. add lightweight iXBRL fact extraction and summary
5. add minimal DB, migrations, and document metadata storage
6. add issuer routing and framework eligibility
7. parse narrative text and section it
8. extract IVF-relevant evidence
9. build minimal packet
10. run IVF pre-screen with strict JSON validation
11. add tests and golden files
12. add stronger NSM search and annual report selection logic
13. add Arelle-backed extraction later
14. add interim and trading update support

## Definition Of Done For The First Slice

The first slice is successful when we can do this for one real company:

1. search for it in NSM
2. acquire its annual report artifact
3. extract structured iXBRL facts and narrative evidence
4. decide whether the issuer is structurally eligible for IVF
5. if eligible, build a packet with explicit gaps
6. run the IVF pre-screen
7. inspect a stored, valid result with citations back to the report

That would prove the most important claim in the project: primary filings can drive a reusable, auditable framework runner, even when source acquisition requires browser automation.
