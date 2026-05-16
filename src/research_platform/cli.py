from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.documents.ixbrl_extractor import (
    IXBRLExtractionError,
    IXBRLExtractor,
)
from research_platform.documents.ivf_ixbrl_packet import IVFFIXBRLPacketBuilder
from research_platform.documents.text_extractor import TextExtractionError, extract_text
from research_platform.documents.ixbrl_summary import IXBRLFactSetBuilder
from research_platform.documents.xhtml_markdown import XHTMLMarkdownRenderer
from research_platform.documents.xhtml_parser import (
    XHTMLReportParseError,
    XHTMLReportParser,
)
from research_platform.frameworks.ivf_pre_screen import IVFPreScreenRunner
from research_platform.frameworks.registry import load_framework_registry
from research_platform.llm import create_llm_client
from research_platform.sources.nsm import (
    NSMDownloadRequest,
    NSMDownloadService,
    NSMSearchError,
)
from research_platform.sources.market import MarketDataError, YFinanceClient
from research_platform.sources.openfigi import OpenFIGIClient, OpenFIGIError, to_yahoo_ticker

app = typer.Typer(help="Company intelligence platform CLI.")
logger = get_logger(__name__)


@app.callback()
def main() -> None:
    """Initialize app settings and logging."""
    configure_logging()


@app.command("list-frameworks")
def list_frameworks() -> None:
    """List registered frameworks."""
    registry = load_framework_registry()
    typer.echo(json.dumps(registry, indent=2))


@app.command("lookup-isin")
def lookup_isin(
    isin: str = typer.Argument(..., help="ISIN to look up, e.g. GB0008847096"),
    out: Optional[Path] = typer.Option(None, help="Optional path to write the result as JSON."),
) -> None:
    """Resolve an ISIN via OpenFIGI and show the company name, exchange, and Yahoo ticker."""
    settings = get_settings()
    client = OpenFIGIClient(api_key=settings.openfigi_api_key)
    try:
        result = client.lookup_isin(isin)
    except OpenFIGIError as exc:
        logger.error("OpenFIGI lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote OpenFIGI result to {out}")


@app.command("extract-text")
def extract_text_command(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False,
                              help="PDF or HTML file to extract text from."),
    max_chars: Optional[int] = typer.Option(None, help="Truncate output to this many characters."),
    out: Optional[Path] = typer.Option(None, help="Optional path to write extracted text."),
) -> None:
    """Extract readable text from a PDF or HTML document (Docling for PDF, html.parser for HTML)."""
    try:
        text = extract_text(file, max_chars=max_chars)
    except TextExtractionError as exc:
        logger.error("Text extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Wrote extracted text to {out} ({len(text):,} chars)")
    else:
        typer.echo(text)


@app.command("fetch-market-data")
def fetch_market_data(
    ticker: str = typer.Argument(..., help="Yahoo Finance ticker, e.g. TSCO.L"),
    out: Optional[Path] = typer.Option(None, help="Optional path to write the result as JSON."),
) -> None:
    """Fetch current market snapshot and 4-year financial history from Yahoo Finance."""
    client = YFinanceClient()
    try:
        snapshot, history = client.get_snapshot(ticker)
    except MarketDataError as exc:
        logger.error("Market data fetch failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = {
        "snapshot": snapshot.model_dump(mode="json"),
        "history": history.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote market data to {out}")


@app.command("init-db")
def init_db() -> None:
    """Show the configured database target for initial setup work."""
    settings = get_settings()
    typer.echo(
        "Database initialization scaffold is ready.\n"
        f"DATABASE_URL={settings.database_url}"
    )


@app.command("ingest-nsm-report")
def ingest_nsm_report(
    query: str = typer.Option(..., help="Company name or search string."),
    document_type: str = typer.Option(
        "annual-report",
        help="Document type to fetch: annual-report or interim-report.",
    ),
    headed: bool = typer.Option(
        False,
        help="Launch a visible browser window for debugging the site flow.",
    ),
    browser_channel: Optional[str] = typer.Option(
        None,
        help="Optional browser channel, for example 'chrome' or 'msedge'.",
    ),
    max_results: int = typer.Option(
        10,
        min=1,
        max=50,
        help="Maximum candidate rows to inspect in the search results view.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing run metadata as JSON.",
    ),
) -> None:
    """Download an NSM filing for a company. Run once per document type."""
    settings = get_settings()
    service = NSMDownloadService(settings=settings)
    request = NSMDownloadRequest(
        query=query,
        document_type=document_type,
        headed=headed,
        browser_channel=browser_channel,
        max_results=max_results,
    )

    try:
        result = service.run(request)
    except NSMSearchError as exc:
        logger.error("NSM acquisition failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote NSM acquisition metadata to {out}")


@app.command("parse-xhtml-report")
def parse_xhtml_report(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing parser output as JSON.",
    ),
) -> None:
    """Parse an XHTML annual report into page text, TOC entries, and inferred sections."""
    parser = XHTMLReportParser()

    try:
        result = parser.parse(file)
    except XHTMLReportParseError as exc:
        logger.error("XHTML parsing failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote XHTML parser output to {out}")


@app.command("render-xhtml-markdown")
def render_xhtml_markdown(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Path = typer.Option(
        ...,
        help="Path for writing the rendered markdown output.",
    ),
) -> None:
    """Render an XHTML annual report into a human-readable markdown file."""
    parser = XHTMLReportParser()
    renderer = XHTMLMarkdownRenderer()

    try:
        result = parser.parse(file)
    except XHTMLReportParseError as exc:
        logger.error("XHTML parsing failed: %s", exc)
        raise typer.Exit(code=1) from exc

    markdown = renderer.render(result)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    typer.echo(f"Wrote XHTML markdown to {out}")


@app.command("extract-ixbrl-facts")
def extract_ixbrl_facts(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing extracted facts as JSON.",
    ),
    jsonl_out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing extracted facts as JSONL.",
    ),
) -> None:
    """Extract structured iXBRL facts from an XHTML annual report."""
    extractor = IXBRLExtractor()

    try:
        result = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = result.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote iXBRL facts JSON to {out}")

    if jsonl_out is not None:
        jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        jsonl_out.write_text(result.to_jsonl(), encoding="utf-8")
        typer.echo(f"Wrote iXBRL facts JSONL to {jsonl_out}")


@app.command("summarize-ixbrl")
def summarize_ixbrl(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the iXBRL fact set as JSON.",
    ),
) -> None:
    """Extract and deduplicate all iXBRL facts from an XHTML annual report."""
    extractor = IXBRLExtractor()
    builder = IXBRLFactSetBuilder()

    try:
        extraction = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    fact_set = builder.build(extraction)
    payload = fact_set.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote iXBRL fact set to {out}")



@app.command("build-ivf-packet-from-ixbrl")
def build_ivf_packet_from_ixbrl(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    post_period_file: Optional[Path] = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional post-period file: XHTML for iXBRL, HTML/PDF for text extraction.",
    ),
    post_period_type: str = typer.Option(
        "INTERIM_OR_UPDATE",
        help="Label for the post-period file, e.g. HALF_YEAR_REPORT or TRADING_UPDATE.",
    ),
    market_data_file: Optional[Path] = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional JSON output from 'fetch-market-data --out' to include market context.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the IVF packet as JSON.",
    ),
) -> None:
    """Build a first-pass IVF packet from an annual report XHTML, with optional post-period and market data."""
    from research_platform.documents.text_extractor import TextExtractionError, extract_text
    from research_platform.sources.market import FinancialHistory, MarketSnapshot

    extractor = IXBRLExtractor()
    fact_set_builder = IXBRLFactSetBuilder()
    packet_builder = IVFFIXBRLPacketBuilder()

    try:
        extraction = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    fact_set = fact_set_builder.build(extraction)

    # Post-period: XHTML → iXBRL facts; HTML/PDF → text extraction
    post_period_fact_set = None
    post_period_narrative = None
    if post_period_file is not None:
        if post_period_file.suffix.lower() == ".xhtml":
            try:
                post_extraction = extractor.extract(post_period_file)
                post_period_fact_set = fact_set_builder.build(post_extraction)
            except IXBRLExtractionError as exc:
                logger.error("Post-period iXBRL extraction failed: %s", exc)
                raise typer.Exit(code=1) from exc
        else:
            try:
                post_period_narrative = extract_text(post_period_file)
                logger.info("Post-period text extracted: %d chars", len(post_period_narrative))
            except TextExtractionError as exc:
                logger.error("Post-period text extraction failed: %s", exc)
                raise typer.Exit(code=1) from exc

    # Market data: load from JSON file produced by fetch-market-data
    market_snapshot = None
    market_history = None
    if market_data_file is not None:
        try:
            md = json.loads(market_data_file.read_text(encoding="utf-8"))
            market_snapshot = MarketSnapshot(**md["snapshot"])
            market_history = FinancialHistory(**md["history"])
        except Exception as exc:
            logger.error("Failed to load market data: %s", exc)
            raise typer.Exit(code=1) from exc

    packet = packet_builder.build(
        fact_set=fact_set,
        post_period_fact_set=post_period_fact_set,
        post_period_type=post_period_type,
        post_period_narrative=post_period_narrative,
        market_snapshot=market_snapshot,
        market_history=market_history,
    )
    payload = packet.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote IVF packet to {out}")


@app.command("run-ivf-pre-screen")
def run_ivf_pre_screen(
    packet_file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the validated IVF pre-screen result as JSON.",
    ),
    prompt_out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the raw prompt snapshot.",
    ),
    raw_response_out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the raw model response.",
    ),
) -> None:
    """Run the IVF pre-screen LLM step against a broad packet."""
    settings = get_settings()
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    llm_client = create_llm_client(settings)
    runner = IVFPreScreenRunner(
        llm_client=llm_client,
        model=settings.llm_model,
        temperature=settings.ivf_pre_screen_temperature,
        max_repair_attempts=settings.ivf_pre_screen_max_repair_attempts,
    )
    result = runner.run(
        packet=packet,
        prompt_out=prompt_out,
        raw_response_out=raw_response_out,
    )
    payload = runner.build_run_payload(
        packet=packet,
        result=result,
        provider=llm_client.provider_name,
        model=settings.llm_model,
    )
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote IVF pre-screen result to {out}")


@app.command("run-ivf-screen")
def run_ivf_screen(
    isin: str = typer.Option(..., help="ISIN of the company to screen, e.g. GB0008847096."),
    headed: bool = typer.Option(False, help="Run NSM browser in headed mode for debugging."),
    out_dir: Path = typer.Option(Path("data/results"), help="Base directory for run outputs."),
) -> None:
    """End-to-end IVF pre-screen: resolve ISIN → download NSM reports → fetch market data → run pre-screen."""
    from datetime import date as _date

    from research_platform.documents.text_extractor import TextExtractionError, extract_text
    from research_platform.sources.market import (
        FinancialHistory,
        MarketDataError,
        MarketSnapshot,
        YFinanceClient,
    )

    settings = get_settings()
    run_date = _date.today().isoformat()

    # ── 1. Resolve ISIN ──────────────────────────────────────────────────────
    typer.echo(f"[1/7] Resolving {isin} via OpenFIGI...")
    try:
        figi = OpenFIGIClient(api_key=settings.openfigi_api_key).lookup_isin(isin)
    except OpenFIGIError as exc:
        logger.error("OpenFIGI lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    company_name = figi.name
    yahoo_ticker = to_yahoo_ticker(figi.ticker, figi.exch_code)
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in company_name.lower())
    slug = "-".join(p for p in slug.split("-") if p)[:40]

    run_dir = out_dir / isin / run_date
    run_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"    {company_name} ({yahoo_ticker}) → {run_dir}")

    # ── 2 & 3. NSM document acquisition (manifest-driven, single session) ────
    typer.echo("[2/7] Acquiring documents from NSM...")
    from research_platform.sources.nsm_manifest import AcquiredDocumentSet
    nsm_service = NSMDownloadService(settings=settings)
    try:
        doc_set = nsm_service.acquire_document_set(
            query=company_name, headed=headed, max_candidates=50,
        )
    except NSMSearchError as exc:
        logger.error("NSM acquisition failed: %s", exc)
        raise typer.Exit(code=1) from exc

    (run_dir / "nsm_document_set.json").write_text(
        doc_set.model_dump_json(indent=2), encoding="utf-8"
    )

    annual_doc = doc_set.get("annual")
    if not annual_doc or not annual_doc.primary_report_file:
        logger.error("No annual report acquired from NSM.")
        raise typer.Exit(code=1)
    annual_xhtml = Path(annual_doc.primary_report_file)
    typer.echo(f"    Annual  ({annual_doc.category}): {annual_xhtml.name}")

    post_doc = doc_set.get_post_period()
    interim_file: Optional[Path] = None
    if post_doc and post_doc.primary_report_file:
        interim_file = Path(post_doc.primary_report_file)
        typer.echo(f"    {post_doc.role.replace('_', ' ').title()} ({post_doc.category}): {interim_file.name}")
    else:
        typer.echo("    No post-period document found.")
    typer.echo(f"    Notes: {'; '.join(doc_set.notes[:3])}")

    # ── 4. Annual report content ──────────────────────────────────────────────
    typer.echo("[4/7] Extracting annual report content...")
    extractor = IXBRLExtractor()
    fsb = IXBRLFactSetBuilder()
    annual_narrative: Optional[str] = None

    if annual_xhtml.suffix.lower() == ".xhtml":
        try:
            fact_set = fsb.build(extractor.extract(annual_xhtml))
            typer.echo(f"    iXBRL: {len(fact_set.numeric_facts)} numeric, {len(fact_set.narrative_facts)} narrative facts")
        except IXBRLExtractionError as exc:
            logger.error("iXBRL extraction failed: %s", exc)
            raise typer.Exit(code=1) from exc
    else:
        # PDF or HTML annual — text extraction, no structured iXBRL
        from research_platform.documents.ixbrl_summary import IXBRLFactSet as _EmptyFactSet
        try:
            annual_narrative = extract_text(annual_xhtml)
            typer.echo(f"    PDF/HTML text: {len(annual_narrative):,} chars (no iXBRL structure)")
        except TextExtractionError as exc:
            logger.warning("Annual text extraction failed (continuing): %s", exc)
        fact_set = _EmptyFactSet(file_path=str(annual_xhtml))

    # ── 5. Post-period ───────────────────────────────────────────────────────
    typer.echo("[5/7] Processing post-period update...")
    post_period_fact_set = None
    post_period_narrative = None

    if interim_file and interim_file.exists():
        if interim_file.suffix.lower() == ".xhtml":
            try:
                post_period_fact_set = fsb.build(extractor.extract(interim_file))
                typer.echo(f"    iXBRL: {len(post_period_fact_set.numeric_facts)} facts")
            except IXBRLExtractionError as exc:
                logger.warning("Post-period iXBRL extraction failed: %s", exc)
        else:
            try:
                post_period_narrative = extract_text(interim_file)
                typer.echo(f"    Narrative: {len(post_period_narrative):,} chars")
            except TextExtractionError as exc:
                logger.warning("Post-period text extraction failed: %s", exc)
    else:
        typer.echo("    No post-period file — staleness flag applied if annual > 9 months.")

    # ── 6. Market data ───────────────────────────────────────────────────────
    typer.echo(f"[6/7] Fetching market data ({yahoo_ticker})...")
    market_snapshot: Optional[MarketSnapshot] = None
    market_history: Optional[FinancialHistory] = None
    try:
        market_snapshot, market_history = YFinanceClient().get_snapshot(yahoo_ticker)
        (run_dir / "market_data.json").write_text(
            json.dumps({
                "snapshot": market_snapshot.model_dump(mode="json"),
                "history": market_history.model_dump(mode="json"),
            }, indent=2),
            encoding="utf-8",
        )
        typer.echo(
            f"    {market_snapshot.currency} {market_snapshot.price} | "
            f"cap {market_snapshot.market_cap:,.0f} | "
            f"{len(market_history.years)} years history"
        )
    except MarketDataError as exc:
        logger.warning("Market data unavailable (continuing): %s", exc)

    # ── 7. Build packet + run pre-screen ─────────────────────────────────────
    typer.echo("[7/7] Building packet and running IVF pre-screen...")
    packet = IVFFIXBRLPacketBuilder().build(
        fact_set=fact_set,
        post_period_fact_set=post_period_fact_set,
        post_period_type="INTERIM_OR_UPDATE",
        post_period_narrative=post_period_narrative,
        market_snapshot=market_snapshot,
        market_history=market_history,
        company_name=company_name,
        ticker=yahoo_ticker,
        isin=isin,
        annual_narrative=annual_narrative,
    )
    packet_dict = packet.model_dump(mode="json")
    (run_dir / "ivf_packet.json").write_text(json.dumps(packet_dict, indent=2), encoding="utf-8")

    llm_client = create_llm_client(settings)
    runner = IVFPreScreenRunner(
        llm_client=llm_client,
        model=settings.llm_model,
        temperature=settings.ivf_pre_screen_temperature,
        max_repair_attempts=settings.ivf_pre_screen_max_repair_attempts,
    )
    result = runner.run(
        packet=packet_dict,
        prompt_out=run_dir / "prompt.txt",
        raw_response_out=run_dir / "raw_response.json",
    )
    run_payload = runner.build_run_payload(
        packet=packet_dict,
        result=result,
        provider=llm_client.provider_name,
        model=settings.llm_model,
    )
    (run_dir / "ivf_result.json").write_text(
        json.dumps(run_payload, indent=2), encoding="utf-8"
    )

    typer.echo(f"\n{'═' * 60}")
    typer.echo(f"  {result.name}  —  {result.status} / {result.confidence} confidence")
    typer.echo(f"  {result.one_sentence_summary}")
    typer.echo(f"  Next: {result.recommended_next_step}")
    typer.echo(f"  Outputs: {run_dir}")
    typer.echo(f"{'═' * 60}")


@app.command("clean")
def clean_data(
    isin: Optional[str] = typer.Option(
        None,
        help="Limit to a specific ISIN — cleans data/results/<isin>/.",
    ),
    slug: Optional[str] = typer.Option(
        None,
        help="Limit to a company slug — cleans data/artifacts/nsm/<slug>/ and data/downloads/nsm/<slug>/. "
             "Use for directories created by the individual pipeline commands (e.g. tesco-plc).",
    ),
    include_downloads: bool = typer.Option(
        True,
        help="Include downloaded NSM files. Applies to global and --slug cleanup.",
    ),
    dry_run: bool = typer.Option(
        False,
        help="Show what would be deleted without deleting anything.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Remove run results, artifacts, and optionally downloaded NSM files."""
    import shutil

    settings = get_settings()

    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n //= 1024
        return f"{n:.0f} TB"

    def _count_files(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for f in path.rglob("*") if f.is_file())

    targets: list[tuple[str, Path]] = []

    data = Path("data")

    if isin and slug:
        typer.echo("Specify either --isin or --slug, not both.")
        raise typer.Exit(code=1)

    if isin:
        path = data / "results" / isin
        if path.exists():
            targets.append(("Results", path))
        else:
            typer.echo(f"No results found for ISIN {isin} ({path}).")
            return

    elif slug:
        for label, rel in [
            ("Artifacts", data / "artifacts" / "nsm" / slug),
            ("Downloads", data / "downloads" / "nsm" / slug),
        ]:
            if rel.exists() and (label != "Downloads" or include_downloads):
                targets.append((label, rel))
        if not targets:
            typer.echo(f"No data found for slug '{slug}'.")
            return

    else:
        for label, rel in [
            ("Results", data / "results"),
            ("Artifacts", data / "artifacts"),
        ]:
            if rel.exists():
                targets.append((label, rel))
        if include_downloads and (data / "downloads").exists():
            targets.append(("Downloads", data / "downloads"))

    if not targets:
        typer.echo("Nothing to clean.")
        return

    typer.echo("The following will be removed:\n")
    total_bytes = 0
    for label, path in targets:
        size = _dir_size(path)
        count = _count_files(path)
        typer.echo(f"  {label:<12} {path}  ({count} files, {_fmt_size(size)})")
        total_bytes += size
    typer.echo(f"\n  Total: {_fmt_size(total_bytes)}")

    if dry_run:
        typer.echo("\n(Dry run — nothing deleted.)")
        return

    if not yes:
        typer.confirm("\nDelete?", abort=True)

    for _, path in targets:
        shutil.rmtree(path, ignore_errors=True)
        typer.echo(f"  Removed {path}")

    typer.echo("Done.")


if __name__ == "__main__":
    app()
