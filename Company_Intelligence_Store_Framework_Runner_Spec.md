# Company Intelligence Store & Framework Runner Specification

**Version:** 0.2  
**Primary use case:** Feed the Intrinsic Value Framework Pre-Screen with compact, evidence-grounded data.  
**Broader goal:** Build a reusable company intelligence platform that can feed multiple investment frameworks, including the full IVF, Narrative Decay Framework, Energy Shock & AI filters, and future research workflows.  
**Runtime target:** Local laptop prototype, with a clean migration path to managed PostgreSQL/object storage later.  
**Core stack:** Python + PostgreSQL + pgvector + local filesystem + strict JSON LLM prompts.

---

## 1. Executive Summary

This repository implements a local-first **Company Intelligence Store** and **Framework Runner**.

The system ingests company data, filings, reports, market data, RNS announcements, extracted evidence, financial metrics, and document embeddings into a reusable store. Framework-specific packet builders then assemble compact context packets for different investment frameworks.

The first consumer is the **Intrinsic Value Framework Pre-Screen**, but the data store must not be designed as an IVF-only database.

There must also be an explicit routing step before IVF packet construction that answers:

```text
Can this issuer be analysed successfully?
What kind of issuer is it?
Which framework, if any, should consume it next?
```

This prevents the platform from forcing investment trusts, closed-end funds, asset-backed vehicles, banks, shells, or other structurally different issuers through an operating-company framework by default.

The core architecture is:

```text
Raw sources
   ↓
Company Intelligence Store
   ↓
Framework Packet Builders
   ↓
Framework Prompts / Analyses
   ↓
Framework Results
```

The key design rule:

```text
Raw documents, financials, market data, events, evidence items, chunks,
embeddings, and derived metrics are framework-neutral.

Gate/stage decisions, framework classifications, prompts, packet schemas,
and result schemas are framework-specific.
```

The IVF Pre-Screen should answer only:

```text
Does this company deserve the analytical cost of a full IVF run?
```

It must not produce a full valuation, investment thesis, scenario model, or buy/sell decision.

---

## 2. Design Principles

### 2.1 Build a reusable intelligence store, not an IVF database

The store should support multiple frameworks:

```text
IVF_PRE_SCREEN
IVF_FULL
NDF_PRE_SCREEN
NDF_FULL
ENERGY_AI_FILTER
QUALITY_SCREEN
SPECIAL_SITUATION_SCREEN
CUSTOM_FRAMEWORKS
```

The same underlying company data should feed all of them.

The platform should also support a framework-neutral routing layer before any framework packet is built.

Do not store framework-specific interpretations as core company facts.

For example:

```text
Core fact:
- "The company issued £20m of equity in March 2025."

Framework-specific interpretation:
- IVF Pre-Screen Gate 3 survivability = FLAG.
- Full IVF capital allocation grade = potentially dilutive.
- NDF narrative overhang = possible forced seller / recapitalisation signal.
```

The fact belongs in the intelligence store. The interpretation belongs in framework output.

Between those layers, the platform may also store reusable routing judgements such as:

```text
issuer archetype
framework eligibility
preferred next framework
framework ineligibility reasons
```

### 2.2 Deterministic facts first, LLM judgement second

The ingestion and intelligence layer should do the work that LLMs are weakest at:

```text
identifier resolution
document discovery
data ingestion
financial statement normalisation
derived metric calculation
file hashing
document parsing
chunking
embedding
event classification
evidence extraction
source conflict detection
evidence gap detection
```

The LLM should receive a compact, evidence-grounded packet and make a framework-specific judgement.

### 2.3 Primary-source discipline

For UK-listed companies, source hierarchy:

```text
Primary evidence:
1. FCA National Storage Mechanism annual / half-yearly financial reports
2. Company investor relations annual / interim report PDFs
3. RNS regulated announcements

Supplementary evidence:
4. FMP structured fundamentals and market data
5. Companies House statutory/legal filings
6. Other data providers
```

FMP can populate structured fields quickly, but load-bearing decisions should prefer primary filings where available.

### 2.4 Missing evidence is not safety

The system must distinguish:

```text
No risk found.
```

from:

```text
No evidence retrieved.
```

If a required evidence category is missing, the framework packet should include an explicit evidence gap.

The LLM must not infer that a risk is absent because the retrieval layer failed to find evidence.

### 2.5 Local-first, production-aware

The initial implementation should run locally on a laptop.

Use:

```text
Python
PostgreSQL
pgvector
local filesystem
Typer CLI
```

Avoid early complexity:

```text
microservices
Airflow
Kafka
separate vector DB
full frontend
multi-user auth
real-time market feeds
```

But preserve good production hygiene:

```text
stable IDs
file hashes
prompt versions
packet versions
framework versions
raw response retention
audit trail
idempotent ingestion
```

---

## 3. Conceptual Architecture

### 3.1 Layered architecture

```text
1. Source Layer
   Raw documents, API responses, filings, RNS, market data.

2. Intelligence Store
   Normalised company facts, financials, market data, events, evidence,
   document chunks, embeddings, derived metrics, quality flags.

3. Data Product Layer
   Reusable context bundles built from the intelligence store.

4. Framework Packet Layer
   Framework-specific context packets assembled from data products.

5. Framework Runner Layer
   LLM prompts, result schemas, validation, framework run storage.

6. Result Layer
   Stored decisions, outputs, model metadata, evidence used, audit trail.
```

### 3.2 High-level flow

```text
Ticker / company
   ↓
Identifier resolution
   ↓
Structured data ingestion
   ↓
Primary document ingestion
   ↓
RNS / event ingestion
   ↓
Document parsing + chunking + embeddings
   ↓
Reusable evidence extraction
   ↓
Derived metric calculation
   ↓
Data products
   ↓
Framework-specific packet
   ↓
Framework run
   ↓
Stored JSON result
```

---

## 4. Frameworks as Consumers

### 4.1 Framework registry

Frameworks should be registered with metadata.

Initial frameworks:

```text
IVF_PRE_SCREEN
IVF_FULL
NDF_FULL
ENERGY_AI_FILTER
```

Each framework has:

```text
framework_code
name
framework_version
packet_builder
prompt_template
result_schema
active flag
```

YAML is acceptable for v1. A database table can come later.

Example `frameworks.yaml`:

```yaml
frameworks:
  IVF_PRE_SCREEN:
    name: Intrinsic Value Framework Pre-Screen
    framework_version: v1.0
    packet_builder: ivf_pre_screen
    prompt_template: ivf_pre_screen_v1
    result_schema: IVFPreScreenResult
    active: true

  IVF_FULL:
    name: Intrinsic Value Framework
    framework_version: v2.7
    packet_builder: ivf_full
    prompt_template: ivf_full_v2_7
    result_schema: IVFResult
    active: true

  NDF_FULL:
    name: Narrative Decay Framework
    framework_version: v1.0
    packet_builder: ndf_full
    prompt_template: ndf_full_v1
    result_schema: NDFResult
    active: true

  ENERGY_AI_FILTER:
    name: Energy Shock & AI Quick Filter
    framework_version: v1.0
    packet_builder: energy_ai_filter
    prompt_template: energy_ai_filter_v1
    result_schema: EnergyAIFilterResult
    active: true
```

### 4.2 Framework packet builders

Each framework should have its own packet builder.

```text
frameworks/
  ivf_pre_screen/
    packet.py
    prompt.py
    schema.py

  ivf_full/
    packet.py
    prompt.py
    schema.py

  ndf/
    packet.py
    prompt.py
    schema.py

  energy_ai_filter/
    packet.py
    prompt.py
    schema.py
```

Packet builders select data from the common intelligence store.

### 4.3 Framework results

Every framework run should store:

```text
company_id
framework_code
framework_version
packet_id
prompt_version
model
result_json
status / decision
token usage
latency
raw prompt path
raw response
created_at
```

---

## 5. Data Products

A **data product** is a reusable context bundle generated from the intelligence store.

Framework packets should be assembled from data products rather than directly hard-coding all queries into each framework.

Examples:

```text
COMPANY_BASE_PROFILE
SECURITY_IDENTIFIER_SUMMARY
MARKET_SNAPSHOT
FINANCIAL_HISTORY_5Y
FINANCIAL_HISTORY_10Y
DERIVED_SCREENING_METRICS
CAPITAL_STRUCTURE_SUMMARY
DEBT_AND_LIQUIDITY_EVIDENCE
GOING_CONCERN_AND_AUDIT_EVIDENCE
RNS_EVENT_SUMMARY_3Y
CAPITAL_ALLOCATION_HISTORY
FLOOR_ANCHOR_EVIDENCE
DISLOCATION_EVIDENCE
SEGMENT_HISTORY
CASH_CONVERSION_ANALYSIS
INDUSTRY_STRUCTURE_EVIDENCE
MACRO_SENSITIVITY_CONTEXT
VALUATION_ANCHORS
NAV_HISTORY
ASSET_COMPOSITION
ENERGY_SENSITIVITY_SUMMARY
AI_EXPOSURE_SUMMARY
```

### 5.1 Example framework-to-data-product map

```yaml
IVF_PRE_SCREEN:
  data_products:
    - COMPANY_BASE_PROFILE
    - MARKET_SNAPSHOT
    - FINANCIAL_HISTORY_5Y
    - DERIVED_SCREENING_METRICS
    - RNS_EVENT_SUMMARY_3Y
    - GOING_CONCERN_AND_AUDIT_EVIDENCE
    - DEBT_AND_LIQUIDITY_EVIDENCE
    - FLOOR_ANCHOR_EVIDENCE
    - DISLOCATION_EVIDENCE

IVF_FULL:
  data_products:
    - COMPANY_BASE_PROFILE
    - MARKET_SNAPSHOT
    - FINANCIAL_HISTORY_10Y
    - SEGMENT_HISTORY
    - CASH_CONVERSION_ANALYSIS
    - CAPITAL_STRUCTURE_SUMMARY
    - DEBT_AND_LIQUIDITY_EVIDENCE
    - CAPITAL_ALLOCATION_HISTORY
    - INDUSTRY_STRUCTURE_EVIDENCE
    - MACRO_SENSITIVITY_CONTEXT
    - VALUATION_ANCHORS
    - RNS_EVENT_SUMMARY_3Y

NDF_FULL:
  data_products:
    - COMPANY_BASE_PROFILE
    - MARKET_SNAPSHOT
    - NAV_HISTORY
    - ASSET_COMPOSITION
    - DISCOUNT_HISTORY
    - MAJOR_SHAREHOLDER_EVENTS
    - BUYBACK_ISSUANCE_HISTORY
    - NARRATIVE_OVERHANG_EVENTS
    - RNS_EVENT_SUMMARY_3Y

ENERGY_AI_FILTER:
  data_products:
    - COMPANY_BASE_PROFILE
    - SEGMENT_HISTORY
    - ENERGY_SENSITIVITY_SUMMARY
    - AI_EXPOSURE_SUMMARY
    - MARGIN_AND_INPUT_COST_EVIDENCE
    - RNS_EVENT_SUMMARY_3Y
```

---

## 6. Topic Taxonomy

Use topic tags as the bridge between raw data and frameworks.

Documents, chunks, events, and evidence items may carry topic tags.

Initial topic tags:

```text
business_model
revenue_quality
customer_concentration
product_concentration
regulatory_dependency
segment_performance
cash_conversion
capex
working_capital
debt
liquidity
covenants
debt_maturity
refinancing
going_concern
audit_quality
restatement
cycle_position
pricing_power
input_costs
order_book
backlog
structural_decline
obsolescence
capital_allocation
buybacks
dividends
equity_issuance
m_and_a
disposals
insider_alignment
director_dealing
related_party
regulatory_risk
litigation
dislocation
forced_selling
major_shareholder
strategic_review
profit_warning
guidance_change
nav
asset_value
replacement_cost
tangible_book
goodwill
impairment
energy_sensitivity
ai_exposure
macro_sensitivity
valuation
```

Frameworks map topics to their own gates/stages.

Before those gates are applied, the platform should derive a reusable routing profile, for example:

```yaml
ISSUER_ROUTING_PROFILE:
  issuer_archetype:
    one_of:
      - OPERATING_COMPANY
      - CLOSED_END_FUND
      - INVESTMENT_TRUST
      - REIT
      - FINANCIAL_INSTITUTION
      - HOLDCO
      - SHELL_OR_SPAC
      - OTHER
  framework_eligibility:
    IVF_PRE_SCREEN: ELIGIBLE | INELIGIBLE | MANUAL_REVIEW
    NDF_PRE_SCREEN: ELIGIBLE | INELIGIBLE | MANUAL_REVIEW
  ineligibility_reasons: []
  preferred_next_framework: IVF_PRE_SCREEN | NDF_PRE_SCREEN | OTHER | null
```

Example:

```yaml
IVF_PRE_SCREEN:
  gate_0_eligibility:
    topics: [business_model, nav, asset_value]
  gate_1_data_sufficiency:
    topics: [audit_quality, going_concern, restatement]
  gate_2_cycle_and_earnings_quality:
    topics: [cycle_position, segment_performance, pricing_power, input_costs]
  gate_3_survivability:
    topics: [debt, liquidity, covenants, debt_maturity, refinancing, equity_issuance]
  gate_4_downside_floor:
    topics: [asset_value, replacement_cost, tangible_book, cash_conversion, goodwill, impairment]
  gate_5_time_direction:
    topics: [cash_conversion, buybacks, dividends, structural_decline, obsolescence]
  gate_6_dislocation_source:
    topics: [dislocation, profit_warning, forced_selling, strategic_review, major_shareholder]
```

---

## 7. Technology Stack

### 7.1 Runtime

Use:

```text
Python 3.12+
```

Core libraries:

```text
pydantic
sqlalchemy
alembic
psycopg
httpx
tenacity
pandas
typer
python-dotenv
structlog or loguru
```

Document parsing:

```text
PyMuPDF / fitz
BeautifulSoup
lxml
pypdf, optional
pdfplumber, optional
```

Embeddings and LLM:

```text
OpenAI or compatible LLM provider SDK
tiktoken or equivalent token counter, optional
```

### 7.2 Database

Use PostgreSQL with pgvector.

Local Docker image:

```text
pgvector/pgvector:pg16
```

Required extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### 7.3 File storage

Use local filesystem for the prototype.

Suggested layout:

```text
data/
  documents/
    raw/
      {company_id}/
        annual_reports/
        half_year_reports/
        interims/
        rns/
        presentations/
    text/
      {company_id}/
    parsed/
      {company_id}/
  prompts/
    {framework_code}/
  packets/
    {company_id}/
  results/
    {company_id}/
  logs/
```

The database stores file paths and hashes.

Do not store large PDFs directly in Postgres initially.

---

## 8. Recommended Repository Structure

```text
company-intelligence-platform/
  README.md
  pyproject.toml
  .env.example
  docker-compose.yml
  alembic.ini

  src/
    research_platform/
      __init__.py

      core/
        config.py
        ids.py
        dates.py
        logging.py
        enums.py

      db/
        __init__.py
        models.py
        session.py
        repositories.py
        migrations/

      schemas/
        __init__.py
        company.py
        securities.py
        financials.py
        market.py
        documents.py
        events.py
        evidence.py
        data_products.py
        frameworks.py

      sources/
        __init__.py
        fmp.py
        openfigi.py
        nsm.py
        rns.py
        ir_site.py
        companies_house.py
        fred.py
        oecd.py
        eia.py
        edgar.py

      documents/
        __init__.py
        storage.py
        parsing.py
        pdf.py
        html.py
        sections.py
        chunking.py
        embeddings.py

      intelligence/
        __init__.py
        financials.py
        metrics.py
        events.py
        evidence.py
        quality_flags.py
        source_conflicts.py

      retrieval/
        __init__.py
        keyword.py
        vector.py
        hybrid.py
        query_sets.py
        evidence_builder.py

      data_products/
        __init__.py
        registry.py
        builders.py
        company_profile.py
        financial_history.py
        capital_structure.py
        rns_summary.py
        evidence_sets.py
        macro_context.py

      frameworks/
        __init__.py
        registry.py
        runner.py

        ivf_pre_screen/
          __init__.py
          packet.py
          prompt.py
          schema.py

        ivf_full/
          __init__.py
          packet.py
          prompt.py
          schema.py

        ndf/
          __init__.py
          packet.py
          prompt.py
          schema.py

        energy_ai_filter/
          __init__.py
          packet.py
          prompt.py
          schema.py

      llm/
        __init__.py
        client.py
        json_runner.py
        validators.py

      cli.py

  tests/
    test_metrics.py
    test_chunking.py
    test_retrieval.py
    test_packet_builder.py
    test_framework_schemas.py
    golden/

  docs/
    architecture.md
    api_sources.md
    schema.md
    frameworks.md
    prompts.md

  data/
    .gitkeep
```

---

## 9. Data Sources

### 9.1 FMP

Use FMP as the primary structured data provider.

Pull:

```text
company profile
market cap
enterprise value
income statement
balance sheet
cash flow statement
ratios
key metrics
dividends
share count
peers, if available
insider transactions, if useful and available
```

FMP data populates the intelligence store and data products, but primary filings override it for load-bearing evidence where conflicts exist.

### 9.2 OpenFIGI

Use for identifier hygiene.

Purpose:

```text
ticker / exchange → FIGI / ISIN / security type / exchange / currency
```

This prevents screening the wrong security.

### 9.3 FCA National Storage Mechanism

Use for UK-listed annual and half-yearly financial reports.

Target document types:

```text
Annual Financial Report
Half-yearly Financial Report
ESEF/XHTML annual report
prospectus
circular
other regulated documents where relevant
```

### 9.4 RNS

Use for three years of UK regulated announcement history.

Important event types:

```text
trading updates
profit warnings
guidance changes
preliminary results
interim results
annual report publication
financing/refinancing
equity placings
buybacks
dividend changes
M&A
disposals
contract wins/losses
CEO/CFO changes
auditor changes
strategic reviews
litigation/regulatory matters
related-party transactions
director dealings
major shareholder notices
```

### 9.5 Company IR site

Use as fallback or preferred clean-PDF source where NSM is awkward.

### 9.6 Companies House

Use as supplementary legal/statutory context.

Useful for:

```text
registered company number
filing history
company status
officers
persons with significant control
statutory accounts
```

### 9.7 Macro and cycle data

Use conditionally:

```text
FRED
OECD
World Bank
EIA
```

Only call these when the company has material sensitivity to rates, housing, industrial production, energy, commodities, FX, or country macro.

### 9.8 EDGAR

Add later for US-listed companies.

Use for:

```text
10-K
10-Q
8-K
company facts XBRL
Forms 3/4/5
filing metadata
```

---

## 10. Database Schema

The database should store framework-neutral intelligence and framework-specific packets/results separately.

### 10.1 `companies`

```sql
CREATE TABLE companies (
  company_id uuid PRIMARY KEY,
  name text NOT NULL,
  legal_name text,
  ticker text,
  exchange text,
  isin text,
  figi text,
  lei text,
  country text,
  currency text,
  sector text,
  industry text,
  security_type text,
  website text,
  ir_url text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
```

### 10.2 `securities`

```sql
CREATE TABLE securities (
  security_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  ticker text NOT NULL,
  exchange text,
  isin text,
  figi text,
  currency text,
  primary_listing boolean DEFAULT false,
  active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
```

### 10.3 `raw_api_responses`

Store raw provider responses for reproducibility.

```sql
CREATE TABLE raw_api_responses (
  response_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  provider text NOT NULL,
  endpoint text NOT NULL,
  request_params jsonb,
  response_json jsonb,
  response_text text,
  status_code int,
  retrieved_at timestamptz DEFAULT now()
);
```

### 10.4 `market_snapshots`

```sql
CREATE TABLE market_snapshots (
  snapshot_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  security_id uuid REFERENCES securities(security_id),
  as_of_date date NOT NULL,
  source text NOT NULL,

  share_price numeric,
  market_cap numeric,
  enterprise_value numeric,
  shares_outstanding numeric,
  diluted_shares_outstanding numeric,

  net_debt numeric,
  cash_and_equivalents numeric,
  total_debt numeric,

  pe_ratio numeric,
  ev_to_ebitda numeric,
  ev_to_sales numeric,
  price_to_book numeric,
  price_to_tangible_book numeric,
  fcf_yield numeric,
  dividend_yield numeric,
  buyback_yield numeric,

  raw_json jsonb,
  created_at timestamptz DEFAULT now()
);
```

### 10.5 `financials_annual`

```sql
CREATE TABLE financials_annual (
  financial_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  fiscal_year int NOT NULL,
  period_end date,
  publication_date date,
  source text NOT NULL,

  revenue numeric,
  gross_profit numeric,
  operating_income numeric,
  ebit numeric,
  ebitda numeric,
  adjusted_ebitda numeric,
  net_income numeric,

  operating_cash_flow numeric,
  capex numeric,
  free_cash_flow numeric,

  cash numeric,
  total_debt numeric,
  net_debt numeric,
  lease_liabilities numeric,
  pension_deficit numeric,
  total_assets numeric,
  total_equity numeric,
  tangible_book_value numeric,
  goodwill numeric,
  intangible_assets numeric,
  goodwill_and_intangibles numeric,

  shares_outstanding numeric,
  diluted_shares_outstanding numeric,
  dividends_paid numeric,
  buybacks numeric,
  equity_issued numeric,

  raw_json jsonb,
  created_at timestamptz DEFAULT now(),

  UNIQUE(company_id, fiscal_year, source)
);
```

### 10.6 `financials_interim`

```sql
CREATE TABLE financials_interim (
  interim_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  fiscal_year int,
  period text,
  period_end date,
  publication_date date,
  source text NOT NULL,

  revenue numeric,
  gross_profit numeric,
  operating_income numeric,
  ebit numeric,
  ebitda numeric,
  adjusted_ebitda numeric,
  net_income numeric,

  operating_cash_flow numeric,
  capex numeric,
  free_cash_flow numeric,

  cash numeric,
  total_debt numeric,
  net_debt numeric,
  liquidity_available numeric,
  undrawn_facilities numeric,

  shares_outstanding numeric,
  diluted_shares_outstanding numeric,

  raw_json jsonb,
  created_at timestamptz DEFAULT now()
);
```

### 10.7 `derived_metrics`

```sql
CREATE TABLE derived_metrics (
  metrics_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  as_of_date date NOT NULL,

  revenue_cagr_3y numeric,
  revenue_cagr_5y numeric,

  avg_gross_margin_5y numeric,
  avg_operating_margin_5y numeric,
  avg_ebitda_margin_5y numeric,
  avg_fcf_margin_5y numeric,

  latest_gross_margin numeric,
  latest_operating_margin numeric,
  latest_ebitda_margin numeric,
  latest_fcf_margin numeric,

  latest_margin_vs_5y_avg numeric,
  latest_margin_vs_5y_max numeric,

  fcf_positive_years_5y int,
  cumulative_fcf_5y numeric,

  share_count_cagr_5y numeric,
  net_debt_to_ebitda numeric,
  total_debt_to_ebitda numeric,
  interest_coverage numeric,

  tangible_book_to_market_cap numeric,
  goodwill_intangibles_to_equity numeric,
  ev_to_5y_avg_ebitda numeric,

  dividend_yield numeric,
  buyback_yield numeric,
  shareholder_yield numeric,

  flags jsonb,
  created_at timestamptz DEFAULT now()
);
```

### 10.8 `documents`

```sql
CREATE TABLE documents (
  document_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),

  document_type text NOT NULL,
  source text NOT NULL,
  source_url text,
  title text,

  period_end date,
  publication_date date,

  local_path text,
  file_hash text,

  mime_type text,
  page_count int,

  text_extracted boolean DEFAULT false,
  indexed boolean DEFAULT false,
  embedding_model text,
  chunker_version text,

  metadata jsonb,

  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),

  UNIQUE(company_id, document_type, publication_date, file_hash)
);
```

Recommended `document_type` values:

```text
ANNUAL_REPORT
HALF_YEAR_REPORT
INTERIM_RESULTS
TRADING_UPDATE
PRELIMINARY_RESULTS
RNS
PRESENTATION
PROSPECTUS
CIRCULAR
COMPANIES_HOUSE_FILING
EDGAR_10K
EDGAR_10Q
EDGAR_8K
OTHER
```

Recommended `source` values:

```text
FCA_NSM
RNS
IR_SITE
COMPANIES_HOUSE
EDGAR
FMP
OTHER
```

### 10.9 `document_chunks`

Adjust the vector dimension to match the embedding model.

```sql
CREATE TABLE document_chunks (
  chunk_id uuid PRIMARY KEY,
  document_id uuid REFERENCES documents(document_id),
  company_id uuid REFERENCES companies(company_id),

  document_type text NOT NULL,
  source text,
  publication_date date,

  page_start int,
  page_end int,
  section text,
  chunk_index int,

  chunk_text text NOT NULL,
  token_count int,
  chunk_hash text,

  topic_tags text[],

  embedding vector(1536),

  metadata jsonb,

  created_at timestamptz DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX document_chunks_company_idx
ON document_chunks(company_id);

CREATE INDEX document_chunks_doc_type_idx
ON document_chunks(document_type);

CREATE INDEX document_chunks_publication_date_idx
ON document_chunks(publication_date);

CREATE INDEX document_chunks_topic_tags_idx
ON document_chunks
USING gin(topic_tags);

CREATE INDEX document_chunks_text_fts_idx
ON document_chunks
USING gin (to_tsvector('english', chunk_text));

CREATE INDEX document_chunks_embedding_hnsw_idx
ON document_chunks
USING hnsw (embedding vector_cosine_ops);
```

### 10.10 `company_events`

Framework-neutral event table.

```sql
CREATE TABLE company_events (
  event_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  document_id uuid REFERENCES documents(document_id),

  event_date date NOT NULL,
  title text NOT NULL,

  event_type text NOT NULL,
  materiality text,
  topic_tags text[],

  summary text,
  extracted_facts jsonb,
  red_flags jsonb,

  source text,
  source_url text,

  classifier_model text,
  classifier_prompt_version text,

  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
```

Recommended `event_type` values:

```text
RESULTS
TRADING_UPDATE
PROFIT_WARNING
GUIDANCE_CHANGE
ANNUAL_REPORT_PUBLICATION
INTERIM_REPORT_PUBLICATION
FINANCING_OR_REFINANCING
EQUITY_ISSUANCE
BUYBACK
DIVIDEND_CHANGE
M_AND_A
DISPOSAL
CONTRACT_WIN
CONTRACT_LOSS
CUSTOMER_CONCENTRATION
BOARD_CHANGE
AUDITOR_CHANGE
STRATEGIC_REVIEW
LITIGATION_OR_REGULATORY
RELATED_PARTY
DIRECTOR_DEALING
MAJOR_SHAREHOLDER
AGM_STATEMENT
OTHER
```

### 10.11 `evidence_items`

Framework-neutral extracted evidence.

```sql
CREATE TABLE evidence_items (
  evidence_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  document_id uuid REFERENCES documents(document_id),
  chunk_id uuid REFERENCES document_chunks(chunk_id),

  evidence_type text NOT NULL,
  topic_tags text[],

  source_type text,
  source_title text,
  source_date date,
  page_start int,
  page_end int,
  section text,

  fact text NOT NULL,
  quote text,
  confidence text,

  extraction_method text,
  extraction_model text,

  created_at timestamptz DEFAULT now()
);
```

Recommended `evidence_type` values:

```text
BUSINESS_MODEL
REVENUE_MODEL
CUSTOMER_CONCENTRATION
PRODUCT_CONCENTRATION
REGULATORY_DEPENDENCY
AUDIT_OPINION
GOING_CONCERN
RESTATEMENT
DEBT_MATURITY
COVENANT
LIQUIDITY
REFINANCING
EQUITY_ISSUANCE
SEGMENT_PERFORMANCE
MARGIN_TREND
PRICING_POWER
ORDER_BOOK
BACKLOG
CAPEX
CASH_CONVERSION
TANGIBLE_ASSET_VALUE
NAV
REPLACEMENT_COST
GOODWILL
IMPAIRMENT
BUYBACK
DIVIDEND
M_AND_A
RELATED_PARTY
STRUCTURAL_DECLINE
OBSOLESCENCE
DISLOCATION
FORCED_SELLING
PROFIT_WARNING
STRATEGIC_REVIEW
MAJOR_SHAREHOLDER
ENERGY_SENSITIVITY
AI_EXPOSURE
REGULATORY_RISK
LITIGATION
```

### 10.12 `data_products`

Optional table for stored data product outputs.

```sql
CREATE TABLE data_products (
  data_product_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),

  product_code text NOT NULL,
  product_version text NOT NULL,
  as_of_date date NOT NULL,

  product_json jsonb NOT NULL,

  source_document_ids uuid[],
  evidence_item_ids uuid[],
  input_hash text,

  created_at timestamptz DEFAULT now()
);
```

### 10.13 `framework_packets`

```sql
CREATE TABLE framework_packets (
  packet_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),

  framework_code text NOT NULL,
  framework_version text NOT NULL,
  packet_version text NOT NULL,

  as_of_date date NOT NULL,

  packet_json jsonb NOT NULL,

  source_document_ids uuid[],
  evidence_item_ids uuid[],
  data_product_ids uuid[],
  data_snapshot jsonb,

  created_at timestamptz DEFAULT now()
);
```

### 10.14 `framework_runs`

```sql
CREATE TABLE framework_runs (
  run_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),
  packet_id uuid REFERENCES framework_packets(packet_id),

  framework_code text NOT NULL,
  framework_version text NOT NULL,
  prompt_version text NOT NULL,

  run_date timestamptz DEFAULT now(),

  model text,
  result_json jsonb NOT NULL,

  status text,
  decision text,

  token_usage jsonb,
  latency_ms int,

  raw_prompt_path text,
  raw_response text,

  created_at timestamptz DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX framework_runs_company_idx
ON framework_runs(company_id);

CREATE INDEX framework_runs_framework_idx
ON framework_runs(framework_code);

CREATE INDEX framework_runs_status_idx
ON framework_runs(status);
```

### 10.15 `quality_flags`

Optional separate table if not stored inside data products.

```sql
CREATE TABLE quality_flags (
  flag_id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies(company_id),

  as_of_date date NOT NULL,
  flag_code text NOT NULL,
  flag_value boolean,
  flag_state text,
  severity text,
  description text,
  evidence_item_ids uuid[],

  created_at timestamptz DEFAULT now()
);
```

Use `flag_state` to distinguish:

```text
TRUE
FALSE
UNKNOWN
NOT_APPLICABLE
```

Do not use `false` to mean unknown.

---

## 11. Pydantic Schemas

All packet and result objects should be validated with Pydantic.

### 11.1 `CompanyFacts`

```python
class CompanyFacts(BaseModel):
    company_id: str
    name: str
    legal_name: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    isin: str | None = None
    figi: str | None = None
    lei: str | None = None
    country: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    security_type: str | None = None
    business_description: str | None = None
```

### 11.2 `EvidenceItem`

```python
class EvidenceItem(BaseModel):
    evidence_type: str
    topic_tags: list[str] = []
    source_type: str
    source_title: str | None = None
    source_date: date | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    fact: str
    quote: str | None = None
    confidence: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
```

### 11.3 `CompanyEvent`

```python
class CompanyEvent(BaseModel):
    event_date: date
    title: str
    event_type: str
    materiality: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    topic_tags: list[str] = []
    summary: str
    extracted_facts: list[str] = []
    red_flags: list[str] = []
```

### 11.4 `FrameworkPacket`

```python
class FrameworkPacket(BaseModel):
    framework_code: str
    framework_version: str
    packet_version: str
    company_id: str
    as_of_date: date
    packet: dict
    source_document_ids: list[str] = []
    evidence_item_ids: list[str] = []
    data_product_ids: list[str] = []
    evidence_gaps: list[str] = []
    source_conflicts: list[dict] = []
```

### 11.5 `FrameworkRunResult`

```python
class FrameworkRunResult(BaseModel):
    framework_code: str
    framework_version: str
    prompt_version: str
    company_id: str
    status: str | None = None
    decision: str | None = None
    result_json: dict
```

Framework-specific result schemas extend or specialise this.

---

## 12. Ingestion Layer

The ingestion layer should populate/update the Company Intelligence Store.

### 12.1 Ingestion modes

Support:

```text
full
light
documents-only
market-only
events-only
```

#### `full`

Runs:

```text
identifier resolution
FMP structured data pull
market snapshot
financial statement ingestion
primary document discovery
annual/interim document ingestion
3-year RNS ingestion
document parsing
chunking
embedding generation
event classification
evidence extraction
derived metric calculation
data quality flags
source conflict detection
```

#### `light`

Runs:

```text
market snapshot refresh
new RNS check
new document check
derived metric refresh if data changed
event classification for new events
```

#### `documents-only`

Runs:

```text
document discovery
download
file hashing
text extraction
chunking
embedding
```

#### `market-only`

Runs:

```text
FMP price/market snapshot
valuation snapshot
```

#### `events-only`

Runs:

```text
RNS refresh
event classification
event summary updates
```

### 12.2 Refresh triggers

Full refresh:

```text
first ingestion
new annual report
new half-year report
preliminary results
major acquisition/disposal
refinancing/equity issuance
ticker/identifier change
```

Light refresh:

```text
new trading update
new director dealing
new holding announcement
share price / market cap material move
new FMP update
```

### 12.3 Idempotency

All ingestion should be idempotent.

Use:

```text
company_id
source_url
provider ID
publication date
document type
file hash
event date/title hash
```

Repeated ingestion should not duplicate data.

---

## 13. Document Processing

### 13.1 Discovery

For UK companies:

1. Resolve company identity and listing.
2. Search FCA NSM for annual reports and half-year reports.
3. Search RNS for recent announcements.
4. Search company IR site if needed.
5. Store metadata.
6. Download document.
7. Hash file.
8. Skip reprocessing if hash already exists.

### 13.2 Parsing

For PDFs:

```text
Use PyMuPDF as primary parser.
Extract text by page.
Preserve page numbers.
Attempt heading/section detection.
```

For XHTML/ESEF:

```text
Use BeautifulSoup/lxml.
Strip scripts/styles/navigation.
Preserve headings.
Extract section-level text where possible.
```

### 13.3 Chunking

Preferred:

```text
section-aware chunking
page-aware fallback
token-aware max size
```

Defaults:

```text
chunk size: 500–900 tokens
overlap: 75–150 tokens
```

Each chunk should have:

```text
company_id
document_id
document_type
source
publication_date
page_start
page_end
section
chunk_index
chunk_hash
token_count
topic_tags
chunk_text
```

### 13.4 Topic tagging

Initial topic tagging can be deterministic using keywords plus optional LLM refinement.

Examples:

```text
"going concern" → going_concern, audit_quality
"covenant" → debt, covenants, liquidity
"maturity" + "facility" → debt_maturity, refinancing
"related party" → related_party
"impairment" → impairment, goodwill, asset_value
```

---

## 14. Embeddings and Retrieval

### 14.1 Embeddings

Generate embeddings for chunks.

Store:

```text
embedding vector
embedding_model
embedding_created_at
chunker_version
```

If embedding model or chunker version changes, re-index.

### 14.2 Hybrid retrieval

Use:

```text
Postgres full-text search
pgvector semantic search
metadata filters
topic tags
```

Do not rely solely on vector search.

### 14.3 Reusable retrieval queries

Retrieval should be topic-oriented, not framework-hardcoded.

Example topic query sets:

```yaml
debt_and_liquidity:
  keywords:
    - borrowings
    - debt maturity
    - covenant
    - liquidity
    - undrawn facility
    - refinancing
    - revolving credit facility
    - going concern
    - viability
  semantic_queries:
    - "debt maturity schedule and refinancing risks"
    - "liquidity position, covenant headroom, available facilities"

audit_quality:
  keywords:
    - auditor report
    - going concern
    - material uncertainty
    - qualified opinion
    - restatement
  semantic_queries:
    - "audit opinion, going concern warnings, restatements"

floor_anchors:
  keywords:
    - tangible assets
    - net assets
    - property plant equipment
    - impairment
    - goodwill
    - replacement cost
    - net cash
  semantic_queries:
    - "asset value, tangible book, replacement cost, impairment risk"

dislocation:
  keywords:
    - profit warning
    - strategic review
    - placing
    - litigation
    - regulatory investigation
    - major shareholder
    - guidance
  semantic_queries:
    - "events that explain why the market may be mispricing the company"
```

Framework packet builders can call these reusable query sets.

---

## 15. Events and RNS Classification

### 15.1 Normalise RNS into company events

RNS should be stored both as raw documents and as normalised events.

Example event:

```json
{
  "event_type": "PROFIT_WARNING",
  "materiality": "HIGH",
  "topic_tags": ["profit_warning", "guidance_change", "dislocation"],
  "summary": "Company reduced FY EBITDA guidance due to weaker demand in its core segment.",
  "extracted_facts": [
    "FY EBITDA guidance reduced from £30m to £22m.",
    "Management cited weaker demand in Q3."
  ],
  "red_flags": [
    "Guidance downgrade",
    "Demand deterioration"
  ]
}
```

### 15.2 RNS classifier output schema

```json
{
  "event_type": "RESULTS | TRADING_UPDATE | PROFIT_WARNING | GUIDANCE_CHANGE | ANNUAL_REPORT_PUBLICATION | INTERIM_REPORT_PUBLICATION | FINANCING_OR_REFINANCING | EQUITY_ISSUANCE | BUYBACK | DIVIDEND_CHANGE | M_AND_A | DISPOSAL | CONTRACT_WIN | CONTRACT_LOSS | CUSTOMER_CONCENTRATION | BOARD_CHANGE | AUDITOR_CHANGE | STRATEGIC_REVIEW | LITIGATION_OR_REGULATORY | RELATED_PARTY | DIRECTOR_DEALING | MAJOR_SHAREHOLDER | AGM_STATEMENT | OTHER",
  "materiality": "LOW | MEDIUM | HIGH | UNKNOWN",
  "topic_tags": ["profit_warning", "guidance_change"],
  "summary": "One concise sentence.",
  "extracted_facts": [
    "Short fact 1",
    "Short fact 2"
  ],
  "red_flags": [
    "Short red flag 1"
  ]
}
```

### 15.3 RNS event summary data product

`RNS_EVENT_SUMMARY_3Y` should include:

```json
{
  "period": "last_3_years",
  "high_materiality_events": [],
  "profit_warnings": [],
  "guidance_changes": [],
  "financing_events": [],
  "equity_issuance": [],
  "m_and_a": [],
  "disposals": [],
  "board_or_auditor_changes": [],
  "litigation_or_regulatory": [],
  "major_shareholder_changes": [],
  "latest_trading_updates": [],
  "dislocation_evidence": []
}
```

---

## 16. Derived Metrics

Derived metrics are framework-neutral but useful to many frameworks.

Calculate:

### 16.1 Growth and margins

```text
3-year revenue CAGR
5-year revenue CAGR
latest gross margin
latest operating margin
latest EBITDA margin
latest FCF margin
5-year average gross margin
5-year average operating margin
5-year average EBITDA margin
latest EBITDA margin vs 5-year average
latest EBITDA margin vs 5-year maximum
```

### 16.2 Cash generation

```text
FCF-positive years out of 5
cumulative 5-year FCF
average FCF margin
FCF / net income
OCF / net income
capex / revenue
capex / depreciation where available
```

### 16.3 Capital structure

```text
net debt / EBITDA
total debt / EBITDA
interest coverage
cash / market cap
largest maturity within 24 months, if available
undrawn facility coverage, if available
```

### 16.4 Balance sheet floor

```text
tangible book value
tangible book / market cap
net cash / market cap
goodwill + intangibles / equity
goodwill + intangibles / assets
price / tangible book
```

### 16.5 Dilution and capital allocation

```text
share count CAGR over 5 years
total dividends paid over 5 years
total buybacks over 5 years
total equity issued over 5 years
dividend yield
buyback yield
shareholder yield
```

### 16.6 Valuation context

```text
current EV / latest EBITDA
current EV / 5-year average EBITDA
current market cap / tangible book
current FCF yield
current earnings yield
drawdown from 3-year high, if available
```

---

## 17. Data Quality Flags

Use explicit flags to inform framework packets.

Example:

```json
{
  "missing_annual_report": false,
  "missing_latest_interim_or_update": false,
  "missing_debt_maturity_data": true,
  "missing_covenant_data": true,
  "fmp_filings_conflict": false,
  "negative_fcf_latest_year": false,
  "negative_fcf_multiple_years": false,
  "share_count_up_gt_10pct_5y": false,
  "goodwill_intangibles_gt_equity": true,
  "net_debt_to_ebitda_gt_3x": false,
  "latest_margin_near_5y_high": true,
  "fcf_positive_years_less_than_3_of_5": false,
  "material_equity_issuance_last_5y": false,
  "high_customer_concentration_found": null,
  "going_concern_warning_found": null,
  "restatement_found": null,
  "auditor_change_found": null
}
```

Use `null` for unknown. Do not use `false` for unknown.

---

## 18. Source Conflicts

Track conflicts between data sources.

Example:

```json
{
  "field": "net_debt",
  "fmp_value": 120000000,
  "filing_value": 95000000,
  "period": "FY2025",
  "resolution": "prefer_filing",
  "notes": "FMP appears to include lease liabilities; filing net debt excludes leases."
}
```

Rules:

```text
Prefer filings for reported financials.
Prefer market data providers for current share price and market cap.
Flag definitional differences.
Store both values where definitions differ.
Do not silently overwrite primary-source data.
```

---

## 19. Framework: IVF Pre-Screen

The IVF Pre-Screen is the first framework implementation.

However, it should not be the first judgement applied to every issuer. A prior routing step should decide whether the issuer is structurally suitable for an operating-company framework at all.

### 19.1 Purpose

Answer:

```text
Does this company justify a full IVF v2.7 run?
```

It does not produce:

```text
full thesis
valuation
scenario model
buy/sell decision
position size
```

### 19.2 Required packet components

`IVF_PRE_SCREEN` should use:

```text
ISSUER_ROUTING_PROFILE
COMPANY_BASE_PROFILE
MARKET_SNAPSHOT
FINANCIAL_HISTORY_5Y
DERIVED_SCREENING_METRICS
RNS_EVENT_SUMMARY_3Y
GOING_CONCERN_AND_AUDIT_EVIDENCE
DEBT_AND_LIQUIDITY_EVIDENCE
FLOOR_ANCHOR_EVIDENCE
DISLOCATION_EVIDENCE
DATA_QUALITY_FLAGS
SOURCE_CONFLICTS
```

### 19.3 IVF Pre-Screen packet shape

```json
{
  "issuer_routing_profile": {},
  "company_facts": {},
  "market_data": {},
  "historical_financial_summary": [],
  "derived_screening_metrics": {},
  "primary_document_summary": {},
  "rns_event_summary_3y": {},
  "evidence_pack": {
    "gate_0_eligibility": [],
    "gate_1_data_sufficiency": [],
    "gate_2_cycle_and_earnings_quality": [],
    "gate_3_survivability": [],
    "gate_4_downside_floor": [],
    "gate_5_time_direction": [],
    "gate_6_dislocation_source": []
  },
  "evidence_gaps": [],
  "source_conflicts": [],
  "data_quality_flags": {}
}
```

`gate_0_eligibility` should consume both:

- the routing outcome, including issuer archetype and framework suitability
- the framework-specific evidence required to decide whether IVF is the right next step

Example routing outcomes:

```text
OPERATING_COMPANY + IVF_ELIGIBLE
CLOSED_END_FUND + IVF_INELIGIBLE
INVESTMENT_TRUST + IVF_INELIGIBLE
FINANCIAL_INSTITUTION + IVF_INELIGIBLE
HOLDCO_OR_COMPLEX_STRUCTURE + MANUAL_REVIEW
```

### 19.4 IVF Pre-Screen prompt

Initial prompt:

```text
You are running the Intrinsic Value Framework Pre-Screen.

This is a fast triage filter, not a thesis and not a full valuation. Default to rejection. A PASS only means the company deserves a full Intrinsic Value Framework v2.7 run.

You will receive a structured screening packet generated from:
1. latest audited annual report or annual financial report;
2. latest half-year/interim/trading update, where available;
3. three years of RNS or regulated announcements;
4. structured financial and market data, usually from FMP;
5. retrieved evidence snippets from primary documents.

Use only the supplied packet and evidence. Prefer primary documents for load-bearing evidence. Use structured financial data for screening context, but do not treat it as superior to primary filings. Do not infer missing facts. If a required fact is absent or unclear, mark it as UNKNOWN. UNKNOWN evidence must not support a clean PASS.

Your task:
Decide whether the company should proceed to a full IVF v2.7 analysis.

If the packet indicates that the issuer is structurally unsuitable for IVF, do not force an IVF-style judgement. Return a reroute or reject outcome with explicit reasons.

Immediate rejection triggers:
- Pre-revenue, negligible operations, or no demonstrated earning power.
- Cash-burning growth story with no demonstrated unit economics.
- Going-concern warning or unresolved material restatement.
- Capital markets dependency: negative FCF plus near-term refinancing or equity funding need.
- Levered cyclical where the valuation case relies on peak earnings.
- No quantifiable downside floor.
- Observable structural obsolescence already impairing revenue, margins, returns, or demand.
- Single customer, product, contract, or regulatory dependency greater than 40% of revenue or earnings.
- Roll-up funded by repeated equity issuance or debt with material goodwill or integration risk.
- Material debt maturity, covenant, or liquidity risk that cannot be resolved from supplied evidence.

Gate 0 — Eligibility:
PASS only if this is an operating business with revenue, costs, customers, and at least 3 years of operating history.
REROUTE asset-backed vehicles, REITs, investment trusts, listed holding companies, or NAV-discount situations to NDF.
Special situations may pass only if clean pro-forma financials, identifiable dislocation, and a plausible resolution timeline exist. Otherwise reject.

Gate 1 — Data sufficiency:
PASS only if the supplied evidence contains enough information to assess revenue, profitability, cash flow, debt, liquidity, share count, and recent trading.
If key evidence is missing, return INSUFFICIENT_DATA or PASS_WITH_FLAGS, not PASS.
Use annual/interim reports as the primary source for historical financial evidence.
Use RNS/trading updates to flag changes since the latest report.

Gate 2 — Cycle and earnings quality:
Classify current earnings as PEAK, ABOVE_TREND, MID_CYCLE, BELOW_TREND, TROUGH, or UNKNOWN.
Reject if apparent cheapness depends on peak or above-cycle earnings.
Trough or below-trend earnings may pass only if survivability and industry structure appear intact.
If margins, utilisation, commodity prices, or demand appear unusually favourable versus history, flag peak-risk.

Gate 3 — Survivability:
Reject if the business cannot plausibly survive 2–3 years of adverse conditions without dilution, covenant breach, distressed asset sales, or refinancing dependence.
Assess cash, undrawn facilities, debt maturities, covenants, FCF, recent equity issuance, and post-period liquidity updates.
If debt maturity profile, covenant headroom, or liquidity runway is unclear, do not return clean PASS.

Gate 4 — Downside floor:
Identify credible floor anchors from the supplied evidence.
Possible anchors: recurring FCF yield, net cash, tangible book value, replacement cost, transaction evidence, trough multiple on demonstrated trough earnings, or debt-adjusted stressed FCF yield.
PASS if at least two credible independent floor anchors exist.
PASS_WITH_FLAGS if one strong operating floor exists and survivability is very strong.
Reject if no floor can be quantified or if the floor depends on heroic going-concern assumptions.

Gate 5 — Time direction:
Classify time direction as POSITIVE, NEUTRAL, NEGATIVE, or UNKNOWN.
POSITIVE means cash generation, reinvestment, dividends, buybacks, debt reduction, or intrinsic value growth accrue while waiting.
NEUTRAL means cyclical recovery or special situation where time does not materially erode value and the floor is intact.
NEGATIVE means intrinsic value erodes while waiting.
Reject time-negative situations unless there is a strong asset floor and a clear harvest or resolution path.

Gate 6 — Dislocation source:
Identify why the market may be mispricing the company.
Allowed categories:
- FORCED_SELLING
- SECTOR_CONTAGION
- EARNINGS_OVERREACTION
- CYCLICAL_TROUGH
- STRUCTURAL_MISUNDERSTANDING
- COMPLEXITY_OR_OPACITY
- QUIET_NEGLECT
- SPECIAL_SITUATION_OVERHANG
- UNKNOWN

UNKNOWN dislocation cannot receive clean PASS.

Likely IVF type:
Use only these categories:
- A_STRUCTURAL_FLOOR
- B_CYCLICAL_RECOVERY
- C_CAPITAL_ALLOCATION_ARBITRAGE
- D_SPECIAL_SITUATION
- UNKNOWN

Return strict JSON only. Do not include markdown, comments, explanation, or text outside the JSON.

Keep all rationale fields to a maximum of 35 words each.
Keep arrays short: maximum 5 items per array.
Prefer concise evidence labels over prose.

JSON schema:

{
  "name": string,
  "ticker": string | null,
  "sector": string | null,
  "status": "REJECT" | "REROUTE" | "INSUFFICIENT_DATA" | "PASS_WITH_FLAGS" | "PASS",
  "confidence": "LOW" | "MEDIUM" | "HIGH",
  "target_framework": "IVF" | "NDF" | "OTHER" | null,
  "killed_at_gate": "IMMEDIATE_REJECTION" | "GATE_0" | "GATE_1" | "GATE_2" | "GATE_3" | "GATE_4" | "GATE_5" | "GATE_6" | null,
  "primary_decision_rationale": string,
  "gate_results": {
    "gate_0_eligibility": {
      "result": "PASS" | "REJECT" | "REROUTE" | "UNKNOWN",
      "rationale": string
    },
    "gate_1_data_sufficiency": {
      "result": "PASS" | "FAIL" | "PARTIAL" | "UNKNOWN",
      "rationale": string,
      "missing_evidence": string[]
    },
    "gate_2_cycle_and_earnings_quality": {
      "result": "PASS" | "REJECT" | "FLAG" | "UNKNOWN",
      "cycle_position": "PEAK" | "ABOVE_TREND" | "MID_CYCLE" | "BELOW_TREND" | "TROUGH" | "UNKNOWN",
      "rationale": string
    },
    "gate_3_survivability": {
      "result": "PASS" | "REJECT" | "FLAG" | "UNKNOWN",
      "rationale": string,
      "key_risks": string[]
    },
    "gate_4_downside_floor": {
      "result": "PASS" | "REJECT" | "FLAG" | "UNKNOWN",
      "floor_anchors": string[],
      "rationale": string
    },
    "gate_5_time_direction": {
      "result": "PASS" | "REJECT" | "FLAG" | "UNKNOWN",
      "time_direction": "POSITIVE" | "NEUTRAL" | "NEGATIVE" | "UNKNOWN",
      "rationale": string
    },
    "gate_6_dislocation_source": {
      "result": "PASS" | "FLAG" | "UNKNOWN",
      "dislocation_source": "FORCED_SELLING" | "SECTOR_CONTAGION" | "EARNINGS_OVERREACTION" | "CYCLICAL_TROUGH" | "STRUCTURAL_MISUNDERSTANDING" | "COMPLEXITY_OR_OPACITY" | "QUIET_NEGLECT" | "SPECIAL_SITUATION_OVERHANG" | "UNKNOWN",
      "rationale": string
    }
  },
  "immediate_rejection_triggers_found": string[],
  "flags": string[],
  "evidence_gaps": string[],
  "likely_ivf_type": "A_STRUCTURAL_FLOOR" | "B_CYCLICAL_RECOVERY" | "C_CAPITAL_ALLOCATION_ARBITRAGE" | "D_SPECIAL_SITUATION" | "UNKNOWN",
  "recommended_next_step": "FULL_IVF_RUN" | "REROUTE_TO_NDF" | "REJECT_NO_FURTHER_WORK" | "REQUEST_MORE_EVIDENCE" | "WATCHLIST_ONLY",
  "one_sentence_summary": string
}
```

---

## 20. Framework: Full IVF

The full IVF should consume a deeper packet from the same store.

It should not depend on the IVF Pre-Screen packet, though it may include the pre-screen result as prior context.

### 20.1 Full IVF packet components

Potential data products:

```text
COMPANY_BASE_PROFILE
MARKET_SNAPSHOT
FINANCIAL_HISTORY_10Y
SEGMENT_HISTORY
CASH_CONVERSION_ANALYSIS
CAPEX_AND_WORKING_CAPITAL_EVIDENCE
CAPITAL_STRUCTURE_SUMMARY
DEBT_AND_LIQUIDITY_EVIDENCE
CAPITAL_ALLOCATION_HISTORY
INDUSTRY_STRUCTURE_EVIDENCE
MACRO_SENSITIVITY_CONTEXT
VALUATION_ANCHORS
RNS_EVENT_SUMMARY_3Y
PRIOR_FRAMEWORK_RESULTS
```

### 20.2 Prior pre-screen result

Full IVF may include:

```json
{
  "prior_framework_results": {
    "IVF_PRE_SCREEN": {
      "status": "PASS_WITH_FLAGS",
      "flags": ["covenant headroom unknown"],
      "likely_ivf_type": "A_STRUCTURAL_FLOOR",
      "evidence_gaps": ["customer concentration"]
    }
  }
}
```

But the full IVF must still pull independently from the intelligence store.

---

## 21. Framework: NDF

The Narrative Decay Framework will consume different data.

Possible NDF data products:

```text
COMPANY_BASE_PROFILE
MARKET_SNAPSHOT
NAV_HISTORY
ASSET_COMPOSITION
DISCOUNT_HISTORY
MAJOR_SHAREHOLDER_EVENTS
BUYBACK_ISSUANCE_HISTORY
NARRATIVE_OVERHANG_EVENTS
RNS_EVENT_SUMMARY_3Y
DEBT_AND_LIQUIDITY_EVIDENCE
TRANSACTION_EVIDENCE
```

NDF should be used for:

```text
investment trusts
REITs
listed holding companies
NAV-discount situations
asset-backed vehicles
```

The IVF Pre-Screen may reroute these to NDF.

---

## 22. LLM JSON Validation

Every framework output must be validated.

Validation steps:

1. Ensure response is valid JSON.
2. Validate against the framework-specific Pydantic schema.
3. Reject if required fields are missing.
4. Reject if enum values are invalid.
5. Reject if output contains markdown or text outside JSON.
6. Retry once with a repair prompt if appropriate.
7. Store raw response even if invalid.

### 22.1 Repair prompt

```text
The previous response was not valid according to the required JSON schema.
Return only corrected strict JSON.
Do not add markdown or explanation.
Use the original decision content where possible.
```

Limit retries.

---

## 23. CLI

Use Typer.

### 23.1 Ingestion commands

```bash
research init-db
research ingest-company LSE:XYZ
research refresh-intelligence LSE:XYZ --mode full
research refresh-intelligence LSE:XYZ --mode light
research ingest-fmp LSE:XYZ
research ingest-nsm LSE:XYZ
research ingest-rns LSE:XYZ --years 3
research parse-documents LSE:XYZ
research embed-documents LSE:XYZ
research classify-events LSE:XYZ
research calculate-metrics LSE:XYZ
research extract-evidence LSE:XYZ
```

### 23.2 Data product commands

```bash
research build-data-product LSE:XYZ --product COMPANY_BASE_PROFILE
research build-data-product LSE:XYZ --product RNS_EVENT_SUMMARY_3Y
research build-data-products LSE:XYZ --framework IVF_PRE_SCREEN
```

### 23.3 Framework commands

```bash
research list-frameworks
research build-packet LSE:XYZ --framework IVF_PRE_SCREEN
research run-framework LSE:XYZ --framework IVF_PRE_SCREEN
research run-framework LSE:XYZ --framework IVF_FULL
research run-framework LSE:XYZ --framework NDF_FULL
research show-result LSE:XYZ --framework IVF_PRE_SCREEN --latest
research export-packet LSE:XYZ --framework IVF_PRE_SCREEN --out packet.json
research export-result LSE:XYZ --framework IVF_PRE_SCREEN --out result.json
```

---

## 24. Example End-to-End Flow

```bash
# Start local PostgreSQL/pgvector
docker compose up -d postgres

# Initialise database
research init-db

# Ingest and update company intelligence
research ingest-company LSE:XYZ
research refresh-intelligence LSE:XYZ --mode full

# Route issuer to the right framework family
research route-issuer LSE:XYZ

# Build IVF Pre-Screen packet
research build-packet LSE:XYZ --framework IVF_PRE_SCREEN

# Run IVF Pre-Screen
research run-framework LSE:XYZ --framework IVF_PRE_SCREEN

# Inspect latest result
research show-result LSE:XYZ --framework IVF_PRE_SCREEN --latest

# If passed, build full IVF packet
research build-packet LSE:XYZ --framework IVF_FULL

# Run full IVF later
research run-framework LSE:XYZ --framework IVF_FULL
```

---

## 25. Error Handling

### 25.1 API failures

Use retries with exponential backoff.

Classify:

```text
temporary_network_error
rate_limit
not_found
parse_error
schema_error
provider_error
```

Do not silently skip missing data.

### 25.2 Document parsing failures

If parsing fails:

```text
store document metadata
store failure reason
set text_extracted = false
add evidence gap
```

### 25.3 Embedding failures

If embedding fails:

```text
store chunk text
mark embedding missing
allow keyword retrieval to continue
add warning
```

### 25.4 LLM failures

If framework output is invalid:

```text
retry once with repair prompt
store raw response
mark run failed if still invalid
do not fabricate result
```

---

## 26. Testing Strategy

### 26.1 Unit tests

Test:

```text
identifier normalisation
financial metric calculations
file hashing
document chunking
topic tagging
event classification schema
retrieval query generation
evidence item construction
data product construction
framework packet construction
LLM JSON validation
```

### 26.2 Integration tests

Use known examples:

```text
one obvious IVF reject
one NDF reroute candidate
one plausible IVF pass/pass-with-flags
one company with profit warning RNS
one company with refinancing/equity issuance event
```

### 26.3 Golden files

Store expected outputs:

```text
tests/golden/
  xyz_company_profile.json
  xyz_ivf_pre_screen_packet.json
  xyz_ivf_pre_screen_result.json
```

Use these to detect schema and prompt drift.

---

## 27. Logging and Audit Trail

Log every major step with:

```text
run_id
company_id
ticker
stage
status
duration_ms
source
document_id
error
```

Store:

```text
raw API responses
raw LLM prompts or file paths
raw LLM responses
validated JSON results
token usage
prompt version
model name
packet ID
data product IDs
evidence item IDs
```

Framework results should be reproducible from a stored packet and prompt version.

---

## 28. Implementation Phases

### Phase 1 — Platform foundation

Goal: local Postgres/pgvector + core schema.

Tasks:

```text
Docker Compose for Postgres/pgvector
SQLAlchemy models
Alembic migrations
Pydantic schemas
Typer CLI skeleton
framework registry skeleton
```

Success criteria:

```text
database initialises
frameworks can be listed
company can be created
```

### Phase 2 — Structured data ingestion

Goal: FMP and identifiers.

Tasks:

```text
OpenFIGI integration
FMP profile ingestion
FMP market snapshot ingestion
FMP annual/interim financials
raw API response storage
derived metric calculation
quality flags
```

Success criteria:

```text
company has profile
market data stored
5–10 years financials stored
derived metrics generated
```

### Phase 3 — Document ingestion

Goal: NSM/RNS/IR documents stored and parsed.

Tasks:

```text
NSM discovery
IR fallback
RNS download
local file storage
file hashing
PDF/XHTML extraction
document chunks
topic tags
```

Success criteria:

```text
latest annual report retrieved
latest half-year/interim retrieved where available
RNS stored
chunks searchable by keyword
```

### Phase 4 — Embeddings and hybrid retrieval

Goal: evidence retrieval.

Tasks:

```text
embedding generation
pgvector index
keyword search
vector search
hybrid search
topic query sets
evidence item extraction
```

Success criteria:

```text
debt/liquidity search finds borrowings sections
audit search finds audit/going concern section
dislocation search finds profit warning/strategic review events
```

### Phase 5 — Events and RNS classification

Goal: normalised company events.

Tasks:

```text
RNS classifier prompt
event schema validation
company_events storage
3-year RNS event summary data product
```

Success criteria:

```text
profit warnings classified correctly
equity placings classified correctly
board changes classified correctly
high materiality events summarised
```

### Phase 6 — Data products

Goal: reusable bundles.

Tasks:

```text
COMPANY_BASE_PROFILE
MARKET_SNAPSHOT
FINANCIAL_HISTORY_5Y
DERIVED_SCREENING_METRICS
RNS_EVENT_SUMMARY_3Y
GOING_CONCERN_AND_AUDIT_EVIDENCE
DEBT_AND_LIQUIDITY_EVIDENCE
FLOOR_ANCHOR_EVIDENCE
DISLOCATION_EVIDENCE
```

Success criteria:

```text
data products build independently
data products store source/evidence IDs
```

### Phase 7 — IVF Pre-Screen framework

Goal: first framework runner.

Tasks:

```text
IVF_PRE_SCREEN packet builder
IVF_PRE_SCREEN prompt
IVF_PRE_SCREEN result schema
JSON runner
result storage
CLI run-framework command
```

Success criteria:

```text
full local flow produces valid strict JSON pre-screen result
status stored in framework_runs
evidence gaps explicit
```

### Phase 8 — Framework expansion

Goal: add full IVF and NDF.

Tasks:

```text
IVF_FULL packet builder
NDF packet builder
prior framework result inclusion
framework-specific schemas
```

Success criteria:

```text
same company intelligence store can feed multiple framework packets
IVF pre-screen result can be included as prior context but not source truth
```

---

## 29. MVP Scope

### Include

```text
local Postgres/pgvector
FMP ingestion
UK NSM/RNS ingestion
local document storage
document parsing/chunking
embeddings
hybrid retrieval
company events
evidence items
data products
IVF Pre-Screen framework
strict JSON results
```

### Exclude initially

```text
full IVF valuation
portfolio management
position sizing
real-time market data
frontend
multi-user auth
cloud deployment
Airflow/Kafka
separate vector database
automated investment decisions
```

---

## 30. Acceptance Criteria for First Working Prototype

The prototype is complete when this works:

```bash
research refresh-intelligence LSE:XYZ --mode full
research build-packet LSE:XYZ --framework IVF_PRE_SCREEN
research run-framework LSE:XYZ --framework IVF_PRE_SCREEN
research show-result LSE:XYZ --framework IVF_PRE_SCREEN --latest
```

And produces:

```text
company record
security identifiers
FMP market/fundamental data
latest annual report metadata and local file
latest half-year/interim/trading update where available
3-year RNS archive
document chunks
embeddings
company events
derived metrics
data quality flags
evidence items
framework packet
valid strict JSON framework result
stored audit trail
```

The result must include:

```text
status
confidence
target framework
gate results
flags
evidence gaps
recommended next step
```

---

## 31. Non-Goals

This system should not:

```text
replace full investment analysis
guarantee data correctness
make final buy/sell decisions
automate trading
hide missing evidence
treat aggregator data as primary evidence
force all frameworks into IVF terminology
```

Its job is:

```text
Create a reusable, evidence-grounded company intelligence layer.
Feed investment frameworks with compact, source-aware packets.
Store framework outputs in a consistent, auditable way.
```

---

## 32. Summary

The target architecture is:

```text
Company Intelligence Store
   - companies
   - securities
   - market data
   - financials
   - documents
   - chunks
   - embeddings
   - events
   - evidence
   - derived metrics
   - data quality flags

Framework Layer
   - data product selection
   - packet builders
   - prompts
   - result schemas
   - framework runs
```

The first framework is IVF Pre-Screen, but the platform must be reusable.

The enduring rule:

```text
The intelligence store captures reusable facts and evidence.
Frameworks consume those facts and produce framework-specific judgements.
```
