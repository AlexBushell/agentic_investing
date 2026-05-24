# Company Intelligence Store — Layer Specification

**Version:** 0.1  
**Status:** Design  
**Depends on:** Company_Intelligence_Store_Framework_Runner_Spec.md

---

## 1. Purpose

The Company Intelligence Store is the persistent, queryable knowledge base that sits between raw data sources and all downstream analysis layers. It is the single source of truth for everything known about a company.

The store is not designed around any specific framework or decision. It is framework-neutral. The IVF pre-screen, the NDF framework, and any future analytical layer are consumers of the store — they do not own it.

The store is designed to be queried naturally by an LLM-powered analyst layer that can ask "what do we know about going concern risk for this company?" and receive ranked, cited evidence in response.

---

## 2. Architecture

Three distinct layers sit above the store. The store serves all of them.

```
┌─────────────────────────────────────────────────────────┐
│                     DATA SOURCES                        │
│  NSM filings · yfinance · Web research agent · Brokers  │
└─────────────────────┬───────────────────────────────────┘
                      │ ingestion
┌─────────────────────▼───────────────────────────────────┐
│              COMPANY INTELLIGENCE STORE                 │
│                                                         │
│   Filesystem          PostgreSQL + pgvector             │
│   ──────────          ────────────────────              │
│   Raw PDFs            companies                         │
│   XHTML packages      documents                         │
│   Research dossiers   document_chunks  ← embedding      │
│   Market snapshots    evidence_items                    │
│                       financials_annual / interim       │
│                       market_snapshots                  │
│                       framework_packets                 │
│                       framework_runs                    │
└─────────────────────┬───────────────────────────────────┘
                      │ retrieve
┌─────────────────────▼───────────────────────────────────┐
│             ANALYST / INTELLIGENCE LAYER                │
│                                                         │
│  Understands the PM's question. Pulls from the store.   │
│  Identifies gaps. Enriches if needed (web research,     │
│  deeper store search). Iterates until the evidence is   │
│  sufficient. Produces a structured dossier.             │
└─────────────────────┬───────────────────────────────────┘
                      │ dossier
┌─────────────────────▼───────────────────────────────────┐
│              FRAMEWORK / DECISION LAYER                 │
│                                                         │
│   IVF pre-screen · NDF · Full IVF · Custom frameworks  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Storage Architecture

### 3.1 PostgreSQL + pgvector

PostgreSQL is the single database for all structured data, full-text search, and vector similarity search. A separate vector database is not required at this scale.

**Why one database:**
- Hybrid queries (keyword filter + vector similarity) execute in a single statement
- Entity-scoped retrieval ("evidence about going concern for company X in the last 18 months") is a single WHERE + ORDER BY — no orchestration across systems
- pgvector with HNSW indexes handles millions of vectors with sub-100ms query times
- Operational simplicity: one connection pool, one backup, one schema migration path

Required PostgreSQL extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector: semantic search
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- trigram: fast LIKE/ILIKE and full-text
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- UUID generation
```

### 3.2 Filesystem

Raw source files are stored on the local filesystem. The database stores file paths and hashes — not file content. Large files (PDFs, XHTML packages, research dossiers) are never stored in the database.

```
data/
  downloads/
    nsm/<company-slug>/     ← raw NSM downloads (ZIP, XHTML, PDF, HTML)
  artifacts/
    nsm/<company-slug>/     ← screenshots, HTML snapshots
  research/
    <isin>/<run-id>/        ← web research dossiers (JSON/markdown)
  results/
    <isin>/<date>/          ← framework run outputs
```

### 3.3 Embedding Model

Local embedding via Ollama. No external API calls for financial research.

- **Model:** `nomic-embed-text` (768 dimensions) or `mxbai-embed-large` (1024 dimensions)
- **Why local:** investment research is sensitive; external embedding APIs create unnecessary exposure
- **Vector column:** `vector(768)` or `vector(1024)` depending on chosen model — fix at schema creation

---

## 4. Core Schema

### 4.1 `companies`

```sql
CREATE TABLE companies (
  company_id      uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  isin            text UNIQUE,
  lei             text,
  figi            text,
  ticker          text,
  exchange_code   text,
  yahoo_ticker    text,
  name            text NOT NULL,
  security_type   text,
  market_sector   text,
  sector          text,          -- yfinance sector (for pre-screen filtering)
  industry        text,
  currency        text,
  excluded        boolean DEFAULT false,
  exclusion_reason text,         -- "Regulated bank — IVF not applicable"
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
);
```

The `excluded` flag and `exclusion_reason` implement the sector filter that runs before any framework execution. Banks, REITs, and investment trusts are flagged here, not inside the framework prompt.

### 4.2 `documents`

```sql
CREATE TABLE documents (
  document_id     uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      uuid REFERENCES companies(company_id),

  source          text NOT NULL,  -- NSM | WEB_RESEARCH | BROKER | RNS | YFINANCE | MANUAL
  document_type   text NOT NULL,  -- ANNUAL_REPORT | HALF_YEAR | TRADING_UPDATE |
                                  -- RESEARCH_DOSSIER | BROKER_NOTE | MARKET_SNAPSHOT
  title           text,
  period_end      date,
  publication_date date,

  file_path       text,           -- null for structured-only sources
  file_hash       text,           -- SHA-256, for deduplication

  format          text,           -- XBRL | PDF | HTML | JSON | MARKDOWN
  char_count      int,
  indexed         boolean DEFAULT false,   -- has been chunked and embedded
  index_version   int DEFAULT 0,

  metadata        jsonb,          -- source-specific: NSM candidate title, run_id, etc.

  created_at      timestamptz DEFAULT now(),

  UNIQUE (company_id, document_type, publication_date, file_hash)
);
```

### 4.3 `document_chunks`

The retrieval unit. Every document is split into chunks before embedding. This is what the analyst layer searches.

```sql
CREATE TABLE document_chunks (
  chunk_id        uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  document_id     uuid REFERENCES documents(document_id),
  company_id      uuid REFERENCES companies(company_id),

  -- Content
  chunk_text      text NOT NULL,
  chunk_index     int NOT NULL,   -- position within document
  section         text,           -- "Going Concern", "Principal Risks", etc.
  token_count     int,
  chunk_hash      text,           -- dedup

  -- Retrieval
  embedding       vector(768),    -- pgvector: semantic similarity
  topic_tags      text[],         -- ["going_concern", "debt_maturity", ...]
  
  -- Provenance
  source          text,           -- inherited from document
  source_date     date,           -- inherited from document.period_end
  confidence      text,           -- HIGH | MEDIUM | LOW
  extraction_method text,         -- IXBRL | PYMUPDF | HTML_PARSER | LLM | WEB_RESEARCH

  created_at      timestamptz DEFAULT now()
);

-- Indexes
CREATE INDEX chunks_company_idx    ON document_chunks(company_id);
CREATE INDEX chunks_topic_tags_idx ON document_chunks USING gin(topic_tags);
CREATE INDEX chunks_source_date_idx ON document_chunks(source_date);
CREATE INDEX chunks_embedding_idx  ON document_chunks 
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_fts_idx        ON document_chunks 
  USING gin(to_tsvector('english', chunk_text));
```

### 4.4 `evidence_items`

Structured, citable findings extracted from chunks. The difference between a chunk and an evidence item: a chunk is a passage, an evidence item is a specific claim.

```sql
CREATE TABLE evidence_items (
  evidence_id     uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      uuid REFERENCES companies(company_id),
  document_id     uuid REFERENCES documents(document_id),
  chunk_id        uuid REFERENCES document_chunks(chunk_id),

  evidence_type   text NOT NULL,  -- GOING_CONCERN | FLOOR_ANCHOR | DEBT_MATURITY |
                                  -- FINANCIAL_FACT | COVENANT | EQUITY_ISSUANCE | ...
  topic_tags      text[],

  fact            text NOT NULL,  -- "Directors confirm no material uncertainty re going concern"
  quote           text,           -- exact supporting text
  value           numeric,        -- for quantitative facts (£24.2m PBT)
  unit            text,           -- GBP | % | x

  source_date     date,
  confidence      text,           -- HIGH | MEDIUM | LOW
  extraction_method text,         -- IXBRL | LLM | WEB_RESEARCH_AGENT | MANUAL

  created_at      timestamptz DEFAULT now()
);

CREATE INDEX evidence_company_idx  ON evidence_items(company_id);
CREATE INDEX evidence_type_idx     ON evidence_items(evidence_type);
CREATE INDEX evidence_topic_idx    ON evidence_items USING gin(topic_tags);
```

### 4.5 Supporting Tables

```sql
-- Structured financial history (from yfinance + iXBRL)
CREATE TABLE financials_annual (
  financial_id    uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      uuid REFERENCES companies(company_id),
  document_id     uuid REFERENCES documents(document_id),
  period_end      date NOT NULL,
  source          text NOT NULL,     -- YFINANCE | IXBRL
  currency        text,
  revenue         numeric,
  gross_profit    numeric,
  operating_profit numeric,
  net_income      numeric,
  free_cash_flow  numeric,
  net_debt        numeric,
  total_assets    numeric,
  total_equity    numeric,
  gross_margin    numeric,
  operating_margin numeric,
  fcf_margin      numeric,
  raw_json        jsonb,
  created_at      timestamptz DEFAULT now(),
  UNIQUE(company_id, period_end, source)
);

-- Point-in-time market data
CREATE TABLE market_snapshots (
  snapshot_id     uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      uuid REFERENCES companies(company_id),
  as_of_date      date NOT NULL,
  source          text NOT NULL,     -- YFINANCE
  currency        text,
  price           numeric,
  market_cap      numeric,
  enterprise_value numeric,
  shares_outstanding numeric,
  week_52_high    numeric,
  week_52_low     numeric,
  raw_json        jsonb,
  created_at      timestamptz DEFAULT now()
);

-- Framework run history
CREATE TABLE framework_runs (
  run_id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      uuid REFERENCES companies(company_id),
  framework_code  text NOT NULL,     -- IVF_PRE_SCREEN | IVF_FULL | NDF
  run_date        timestamptz DEFAULT now(),
  isin            text,
  model           text,
  prompt_version  text,
  status          text,              -- PASS_WITH_FLAGS | REJECT | REROUTE | ...
  confidence      text,
  result_json     jsonb NOT NULL,
  packet_json     jsonb,
  prompt_path     text,
  raw_response_path text,
  output_dir      text,
  created_at      timestamptz DEFAULT now()
);
```

---

## 5. Document Extraction Pipeline

Every ingested document passes through a four-stage pipeline before it is queryable.

```
Raw file (PDF / HTML / XHTML / JSON dossier)
         │
         ▼ Stage 1: Text Extraction
         │   iXBRL   → IXBRLExtractor (facts) + narrative text
         │   PDF      → pymupdf4llm → markdown
         │   HTML     → extract_text() → clean text
         │   Dossier  → parse JSON/markdown structure
         │
         ▼ Stage 2: Chunking
         │   Strategy depends on document type (see 5.1)
         │   Output: list of (text, section, metadata) tuples
         │
         ▼ Stage 3: Topic Tagging
         │   Keyword rules  → deterministic, fast, free
         │   LLM pass       → optional, for ambiguous sections only
         │   Output: topic_tags[] per chunk
         │
         ▼ Stage 4: Embedding + Storage
             embed each chunk → vector(768)
             INSERT INTO document_chunks
             optional: extract evidence_items from high-value chunks
```

### 5.1 Chunking Strategies by Document Type

**iXBRL numeric facts:**  
Do not chunk. Each fact is already atomic. Store directly as `evidence_items` with `extraction_method = IXBRL`. Concept name maps to `evidence_type` and `topic_tags`.

**iXBRL narrative facts:**  
Chunk the text (500–800 tokens), preserve the XBRL concept name as `section` metadata. The concept gives you a head start on topic tagging (e.g. `ifrs-full:DisclosureOfGoingConcernExplanatory` → `going_concern`).

**PDF / HTML annual reports and announcements:**  
Section-aware chunking:
1. Detect headings in the markdown output (H1–H4)
2. Keep chunks within sections — never split across a section boundary
3. 500–800 tokens per chunk, 100-token overlap within a section
4. Tables are atomic — never split a table

**Web research dossiers:**  
Do not chunk uniformly. Exploit the existing structure:
- Each "finding" → one `evidence_item` (fact, quote, confidence, topic)
- Each "evidence gap" → one `evidence_item` with `evidence_type = EVIDENCE_GAP`
- Branch summaries → `document_chunks` with section = branch topic
- The tree hierarchy is stored as metadata, not flattened into text

### 5.2 Topic Taxonomy

Topic tags drive filtered retrieval. Apply keyword rules first; use LLM only when rules are insufficient.

| Tag | Keyword triggers |
|---|---|
| `going_concern` | going concern, material uncertainty, viability |
| `debt_maturity` | maturity, refinancing, repayment, facility expiry |
| `covenant` | covenant, breach, waiver, headroom |
| `liquidity` | liquidity risk, cash runway, undrawn facility |
| `floor_anchor` | tangible book, net assets, replacement cost, freehold |
| `equity_issuance` | placing, subscription, dilution, new shares |
| `impairment` | impairment, write-down, goodwill, intangible |
| `structural_decline` | obsolescence, structural, secular decline |
| `profit_warning` | profit warning, guidance reduction, below expectations |
| `capital_allocation` | buyback, dividend, M&A, disposal |
| `cycle_position` | above trend, peak, trough, normalised earnings |
| `regulatory` | regulatory risk, FCA, competition, compliance |
| `decommissioning` | decommission, site restoration, provision, end of life |

### 5.3 Source Confidence

Confidence reflects the reliability of the source, not the content.

| Source | Confidence | Rationale |
|---|---|---|
| iXBRL tagged fact | HIGH | Primary filing, machine-readable, audited |
| Primary filing PDF/HTML | HIGH | Primary document, audited or regulated |
| Interim / trading update | MEDIUM | Unaudited, management statement |
| Web research finding | MEDIUM | Secondary, cited but not primary |
| Broker note | MEDIUM | Non-independent, may be marketing |
| Web research gap | LOW | Absence of evidence, not evidence of absence |

---

## 6. Retrieval Interface

The analyst layer never writes SQL directly. It calls the retrieval interface, which executes hybrid queries and returns ranked, cited results.

### 6.1 Primary retrieval method

```python
archive.retrieve(
    company_id: str,           # ISIN or internal company_id
    query: str,                # natural language question
    topics: list[str] = [],    # optional topic filter
    source_types: list[str] = [], # PRIMARY_FILING | WEB_RESEARCH | ...
    since: date = None,
    limit: int = 20,
) -> list[EvidenceResult]
```

**Under the hood (single SQL statement):**

```sql
SELECT 
    chunk_id,
    chunk_text,
    section,
    source,
    source_date,
    confidence,
    topic_tags,
    1 - (embedding <=> $query_vector) AS semantic_score
FROM document_chunks
WHERE company_id = $company_id
  AND ($topics IS NULL OR topic_tags && $topics)
  AND ($source_types IS NULL OR source = ANY($source_types))
  AND ($since IS NULL OR source_date >= $since)
ORDER BY 
    CASE confidence WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
    semantic_score DESC
LIMIT $limit;
```

Topic filter prunes the search space before the vector distance runs. Confidence ranking ensures primary filing evidence surfaces before web research on equal relevance.

### 6.2 Structured evidence lookup

For specific fact types where a structured answer is needed:

```python
archive.get_evidence(
    company_id: str,
    evidence_type: str,          # GOING_CONCERN | FLOOR_ANCHOR | ...
    since: date = None,
    confidence_min: str = "MEDIUM",
) -> list[EvidenceItem]
```

### 6.3 Return type

```python
@dataclass
class EvidenceResult:
    chunk_id: str
    text: str
    section: str
    source: str          # NSM | WEB_RESEARCH | ...
    source_date: date
    confidence: str
    topic_tags: list[str]
    semantic_score: float
    document_title: str  # for citation
    document_type: str
```

---

## 7. Web Research Agent Integration

The web research agent (tree-search, produces structured dossiers) integrates with the store as a first-class document source.

**Ingest flow:**

1. Agent completes a research run for a company
2. Dossier (JSON/markdown) saved to `data/research/<isin>/<run-id>/`
3. `documents` record created: `source = WEB_RESEARCH`, `document_type = RESEARCH_DOSSIER`
4. Extraction pipeline runs:
   - Each finding → `evidence_item`
   - Each evidence gap → `evidence_item` with `evidence_type = EVIDENCE_GAP`
   - Branch summaries → `document_chunks` with embeddings
5. All web research evidence is now retrievable alongside primary filings

**Analyst loop interaction:**

The analyst layer can invoke the web research agent when it identifies a gap in the store:

```
analyst: retrieve("covenant headroom", company=Arbuthnot)
result: no relevant chunks found
analyst: invoke web_research_agent(topic="Arbuthnot covenant headroom 2025")
agent: runs tree search, stores dossier
analyst: retrieve("covenant headroom", company=Arbuthnot)  ← second pass
result: [finding: "RCF covenant headroom estimated at 2.1x per H1 report"]
```

The store is the handoff point — the analyst does not pass content directly from the web agent to the framework. Everything goes into the store first.

---

## 8. Sector Exclusion Filter

Before any framework run, `run-ivf-screen` checks `companies.excluded`. If true, the pipeline exits cleanly with a logged reason and no LLM calls are made.

**Exclusion criteria (set at company registration):**

| Sector / Type | Reason |
|---|---|
| Regulated banks, building societies | IVF methodology not applicable to deposit-taking institutions |
| Insurance companies | Liability-heavy structure incompatible with IVF asset floor analysis |
| REITs, property investment companies | NAV-discount situations — NDF framework applies |
| Investment trusts, closed-ended funds | Same as REITs |
| Shells, SPACs | No earning power to screen |

Source: OpenFIGI `market_sector` + `security_type`, supplemented by yfinance `sector` and `industryDisp`.

---

## 9. Implementation Phases

**Phase 1 — Document registry (Increment 4)**  
Schema creation and Alembic migrations. Company identity, document metadata, run history. No chunking or embeddings yet. Deduplication via file hash. The pre-screen pipeline writes run results to `framework_runs`.

**Phase 2 — Chunking and keyword search**  
Extraction pipeline: text → section-aware chunks → topic tagging → store in `document_chunks`. Full-text search via pg_trgm. No embeddings yet. The analyst layer can retrieve by topic and keyword.

**Phase 3 — Embeddings and semantic search**  
Add `nomic-embed-text` embeddings to all chunks. Enable vector similarity queries via pgvector. Hybrid retrieval (keyword + semantic) becomes available. Evidence item extraction for high-value sections.

**Phase 4 — Analyst layer**  
The reasoning loop that receives a PM question, retrieves from the store, identifies gaps, requests web research if needed, and iterates. Produces structured dossiers for framework consumption.

**Phase 5 — Multi-source enrichment**  
Web research agent integration, broker note ingestion, RNS event archive, multi-year financial history.
