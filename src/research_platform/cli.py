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
from research_platform.documents.ixbrl_summary import IXBRLSummarizer
from research_platform.documents.xhtml_markdown import XHTMLMarkdownRenderer
from research_platform.documents.xhtml_parser import (
    XHTMLReportParseError,
    XHTMLReportParser,
)
from research_platform.frameworks.ivf_pre_screen import IVFPreScreenRunner
from research_platform.frameworks.registry import load_framework_registry
from research_platform.llm import create_llm_client
from research_platform.routing.issuer_router import IssuerRouter
from research_platform.sources.nsm import (
    NSMDownloadRequest,
    NSMDownloadService,
    NSMSearchError,
)

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
        help="Document type hint for later result filtering.",
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
    """Run the Playwright-based NSM downloader scaffold."""
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
        help="Optional path for writing the iXBRL summary as JSON.",
    ),
) -> None:
    """Summarize key metrics and tagged disclosures from an iXBRL XHTML report."""
    extractor = IXBRLExtractor()
    summarizer = IXBRLSummarizer()

    try:
        extraction = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    summary = summarizer.summarize(extraction)
    payload = summary.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote iXBRL summary to {out}")


@app.command("route-issuer")
def route_issuer(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the issuer routing profile as JSON.",
    ),
) -> None:
    """Determine issuer archetype and IVF eligibility from an iXBRL XHTML report."""
    extractor = IXBRLExtractor()
    summarizer = IXBRLSummarizer()
    router = IssuerRouter()

    try:
        extraction = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    summary = summarizer.summarize(extraction)
    routing_profile = router.route(summary=summary, extraction=extraction)
    payload = routing_profile.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote issuer routing profile to {out}")


@app.command("build-ivf-packet-from-ixbrl")
def build_ivf_packet_from_ixbrl(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the IVF packet as JSON.",
    ),
) -> None:
    """Build a broad first-pass IVF packet directly from iXBRL extraction."""
    extractor = IXBRLExtractor()
    summarizer = IXBRLSummarizer()
    router = IssuerRouter()
    packet_builder = IVFFIXBRLPacketBuilder()

    try:
        extraction = extractor.extract(file)
    except IXBRLExtractionError as exc:
        logger.error("iXBRL extraction failed: %s", exc)
        raise typer.Exit(code=1) from exc

    summary = summarizer.summarize(extraction)
    routing_profile = router.route(summary=summary, extraction=extraction)
    packet = packet_builder.build(
        summary=summary,
        extraction=extraction,
        routing_profile=routing_profile,
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


if __name__ == "__main__":
    app()
