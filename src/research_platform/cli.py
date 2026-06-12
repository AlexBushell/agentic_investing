from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy.engine.url import make_url

from research_platform.backup import (
    BackupError,
    CheckStatus,
    RestoreMode,
    run_backup,
    run_restore_preflight,
    run_restore,
)
from research_platform.access.queries import SQLCompanyContextStore
from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.documents.ixbrl_extractor import (
    IXBRLExtractionError,
    IXBRLExtractor,
)
from research_platform.documents.text_extractor import TextExtractionError, extract_text
from research_platform.documents.ixbrl_summary import IXBRLFactSetBuilder
from research_platform.documents.xhtml_markdown import XHTMLMarkdownRenderer
from research_platform.documents.xhtml_parser import (
    XHTMLReportParseError,
    XHTMLReportParser,
)
from research_platform.sources.nsm import (
    NSMDownloadRequest,
    NSMDownloadService,
    NSMSearchError,
)
from research_platform.sources.edgar import EdgarClient, EdgarError
from research_platform.sources.gleif import GLEIFClient, GLEIFError
from research_platform.sources.sec_tickers import SECTickerClient, SECTickerError
from research_platform.store.models import STORE_TABLES
from research_platform.store.services.company_identity import CompanyIdentityService
from research_platform.store.services.chunking import NarrativeChunkingService
from research_platform.store.services.document_pipeline import DocumentPipelineService
from research_platform.store.services.document_ingestion import DocumentIngestionService
from research_platform.store.services.edgar_ingestion import EdgarIngestionService
from research_platform.store.services.extraction_persistence import ExtractionPersistenceService
from research_platform.store.services.company_context_builder import (
    CompanyContextBuilderService,
    ResolvedCompanyCandidate,
)
from research_platform.store.session import run_migrations_to_head, session_scope

app = typer.Typer(help="Company intelligence platform CLI.")
logger = get_logger(__name__)


def _redact_database_url(database_url: str) -> str:
    """Return a safe-to-print database URL with any password masked."""
    url = make_url(database_url)
    return url.render_as_string(hide_password=True)


def _build_uk_candidate_list(results) -> list[dict[str, object]]:
    return [
        {
            "lei": result.lei,
            "legal_name": result.legal_name,
            "country": result.country,
            "jurisdiction": result.jurisdiction,
            "city": result.city,
            "registered_as": result.registered_as,
            "status": result.status,
            "registration_status": result.registration_status,
            "isins": result.isins,
            "other_names": result.other_names,
            "next_commands": _build_uk_next_commands(lei=result.lei),
        }
        for result in results
    ]


def _rank_uk_candidates(*, query: str, results):
    normalized_query = _normalize_name_for_ranking(query)

    def score(record) -> tuple[int, int, int, int, int, str]:
        normalized_name = _normalize_name_for_ranking(record.legal_name)
        exact_score = 2 if normalized_name == normalized_query else 0
        substring_score = 1 if normalized_query and normalized_query in normalized_name else 0
        isin_score = 4 if record.isins else 0
        plc_score = 1 if " plc" in f" {record.legal_name.lower()} " else 0
        penalty = 0
        lowered = record.legal_name.lower()
        if "plan" in lowered or "trust" in lowered or "incentive" in lowered:
            penalty -= 2
        if "limited" in lowered and "plc" not in lowered:
            penalty -= 1
        return (
            isin_score,
            exact_score,
            substring_score,
            plc_score + penalty,
            len(record.isins),
            record.legal_name,
        )

    return sorted(results, key=score, reverse=True)


def _normalize_name_for_ranking(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in value)
    stop_words = {"the", "plc", "limited", "ltd", "corp", "inc", "company", "co"}
    tokens = [token for token in cleaned.split() if token and token not in stop_words]
    return " ".join(tokens)


def _build_uk_next_commands(*, lei: str) -> list[dict[str, str]]:
    return [
        {
            "purpose": "Retrieve the latest annual report from FCA NSM",
            "command": f"research ingest-nsm-company --lei {lei} --document-type annual-report --no-persist",
        },
        {
            "purpose": "Retrieve the latest interim report from FCA NSM",
            "command": f"research ingest-nsm-company --lei {lei} --document-type interim-report --no-persist",
        },
    ]


def _build_us_next_commands(*, cik: str, forms: list[str]) -> list[dict[str, str]]:
    form_csv = ",".join(forms)
    return [
        {
            "purpose": "Retrieve recent EDGAR filings using the suggested form family",
            "command": (
                "research ingest-edgar-filings "
                f"--cik {cik} --forms {form_csv} --limit 10 --download --no-persist"
            ),
        }
    ]


def _build_us_candidate_list(results, *, profiles_by_cik: dict[str, dict[str, object]] | None = None) -> list[dict[str, object]]:
    profiles_by_cik = profiles_by_cik or {}
    candidates: list[dict[str, object]] = []
    for result in results:
        candidate = {
            "cik": result.cik,
            "ticker": result.ticker,
            "title": result.title,
        }
        profile = profiles_by_cik.get(result.cik)
        if profile:
            candidate["edgar_profile"] = profile
            candidate["next_commands"] = _build_us_next_commands(
                cik=result.cik,
                forms=profile["suggested_forms"],
            )
        else:
            candidate["next_commands"] = _build_us_next_commands(
                cik=result.cik,
                forms=["10-K", "10-Q", "8-K", "20-F", "6-K"],
            )
        candidates.append(candidate)
    return candidates


def _build_us_resolver_payload(
    *,
    query: str,
    results,
    profiles_by_cik: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "query": query,
        "candidate_count": len(results),
        "candidates": _build_us_candidate_list(results, profiles_by_cik=profiles_by_cik),
    }


def _build_us_profiles(settings, results) -> dict[str, dict[str, object]]:
    client = EdgarClient(settings)
    profiles: dict[str, dict[str, object]] = {}
    for result in results:
        try:
            profile = client.inspect_filer(cik=result.cik)
        except EdgarError:
            continue
        profiles[result.cik] = profile.model_dump(mode="json")
    return profiles


def _forms_match_filing_family(*, requested_forms: tuple[str, ...], suggested_forms: list[str]) -> bool:
    requested = {form.strip().upper() for form in requested_forms if form}
    suggested = {form.strip().upper() for form in suggested_forms if form}
    if not requested or not suggested:
        return True
    return requested.issubset(suggested)


def _build_edgar_form_guidance(*, cik: str, requested_forms: tuple[str, ...], filer_profile) -> dict[str, object]:
    requested = [form.strip().upper() for form in requested_forms if form.strip()]
    suggested = filer_profile.suggested_forms
    forms_match = _forms_match_filing_family(
        requested_forms=requested_forms,
        suggested_forms=suggested,
    )
    return {
        "cik": filer_profile.cik,
        "company_name": filer_profile.company_name,
        "entity_type": filer_profile.entity_type,
        "filing_family": filer_profile.filing_family,
        "requested_forms": requested,
        "suggested_forms": suggested,
        "forms_match": forms_match,
        "suggested_command": (
            "research ingest-edgar-filings "
            f"--cik {cik} --forms {','.join(suggested)} --limit 10 --download --no-persist"
        ),
    }


def _derive_document_context(
    *,
    session,
    document_id: str,
    max_chars: int = 1200,
    overlap_chars: int = 150,
) -> dict[str, object]:
    result = DocumentPipelineService(session).materialize_document(
        document_id=document_id,
        strategy="auto",
        chunk=True,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    return {
        "document_id": result.document_id,
        "artifact_id": result.artifact_id,
        "artifact_path": result.artifact_path,
        "strategy": result.strategy,
        "extraction_id": result.extraction_id,
        "fact_count": result.fact_count,
        "narrative_count": result.narrative_count,
        "chunk_count": result.chunk_count,
    }


def _normalize_document_roles(document_roles: list[str] | None) -> set[str]:
    if not document_roles:
        return set()
    return {
        item.strip().upper().replace("-", "_")
        for item in document_roles
        if item and item.strip()
    }


def _resolve_company_candidates(
    *,
    name: str,
    market: str,
    limit: int,
    settings,
) -> list[ResolvedCompanyCandidate]:
    candidates: list[ResolvedCompanyCandidate] = []
    selected = market.strip().lower()

    if selected in {"auto", "uk"}:
        uk_client = GLEIFClient()
        try:
            uk_results = uk_client.search_company(name, country="GB", limit=limit)
        except GLEIFError:
            uk_results = []
        else:
            for result in uk_results:
                result.isins = uk_client.get_isins(result.lei)
            uk_results = _rank_uk_candidates(query=name, results=uk_results)
        for result in uk_results:
            isins = ", ".join(result.isins) if result.isins else "none"
            candidates.append(
                ResolvedCompanyCandidate(
                    market="uk",
                    display_name=result.legal_name,
                    summary=f"UK | LEI {result.lei} | ISINs {isins}",
                    payload={
                        "lei": result.lei,
                        "legal_name": result.legal_name,
                        "country": result.country,
                        "isins": result.isins,
                    },
                )
            )

    if selected in {"auto", "us"}:
        us_client = SECTickerClient(settings)
        try:
            us_results = us_client.search_company(name, limit=limit)
        except SECTickerError:
            us_results = []
            us_profiles_by_cik = {}
        else:
            us_profiles_by_cik = _build_us_profiles(settings, us_results)
        for result in us_results:
            profile = us_profiles_by_cik.get(result.cik, {})
            filing_family = profile.get("filing_family", "unknown")
            suggested_forms = ",".join(profile.get("suggested_forms", [])) or "10-K,10-Q,8-K,20-F,6-K"
            candidates.append(
                ResolvedCompanyCandidate(
                    market="us",
                    display_name=result.title,
                    summary=(
                        f"US | CIK {result.cik} | ticker {result.ticker} | "
                        f"{filing_family} | forms {suggested_forms}"
                    ),
                    payload={
                        "cik": result.cik,
                        "ticker": result.ticker,
                        "title": result.title,
                        "edgar_profile": profile,
                    },
                )
            )

    return candidates


def _choose_company_candidate(
    *,
    candidates: list[ResolvedCompanyCandidate],
    selection: int | None = None,
) -> ResolvedCompanyCandidate:
    if not candidates:
        raise ValueError("No company candidates found.")
    if selection is not None:
        if selection < 1 or selection > len(candidates):
            raise ValueError(f"Selection must be between 1 and {len(candidates)}.")
        return candidates[selection - 1]
    if len(candidates) == 1:
        return candidates[0]

    typer.echo("Multiple company matches found:\n")
    for index, candidate in enumerate(candidates, start=1):
        typer.echo(f"{index}. {candidate.display_name}")
        typer.echo(f"   {candidate.summary}")

    chosen = typer.prompt(
        f"\nSelect a company [1-{len(candidates)}]",
        type=int,
    )
    if chosen < 1 or chosen > len(candidates):
        raise typer.BadParameter(f"Selection must be between 1 and {len(candidates)}.")
    return candidates[chosen - 1]


@app.callback()
def main() -> None:
    """Initialize app settings and logging."""
    configure_logging()


@app.command("find-uk-company")
def find_uk_company(
    name: str = typer.Argument(..., help="UK company name to search, e.g. The Gym Group"),
    limit: int = typer.Option(10, min=1, max=25, help="Maximum number of candidates to return."),
    persist: bool = typer.Option(
        False,
        "--persist/--no-persist",
        help="Persist the resolved LEI-based identity into the store.",
    ),
    out: Optional[Path] = typer.Option(None, help="Optional path to write the result as JSON."),
) -> None:
    """Search GLEIF for a UK legal entity and surface LEI plus related ISINs."""
    client = GLEIFClient()
    try:
        results = client.search_company(name, country="GB", limit=limit)
    except GLEIFError as exc:
        logger.error("GLEIF UK company search failed: %s", exc)
        raise typer.Exit(code=1) from exc

    for result in results:
        result.isins = client.get_isins(result.lei)
    results = _rank_uk_candidates(query=name, results=results)

    payload = {
        "query": name,
        "candidate_count": len(results),
        "candidates": [
            {
                "lei": result.lei,
                "legal_name": result.legal_name,
                "country": result.country,
                "jurisdiction": result.jurisdiction,
                "city": result.city,
                "registered_as": result.registered_as,
                "status": result.status,
                "registration_status": result.registration_status,
                "isins": result.isins,
                "other_names": result.other_names,
            }
            for result in results
        ],
    }
    typer.echo(json.dumps(payload, indent=2))

    if persist and results:
        settings = get_settings()
        try:
            with session_scope(settings) as session:
                persisted = CompanyIdentityService(session).upsert_from_gleif(record=results[0])
        except Exception as exc:
            logger.error("GLEIF identity persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc
        state = "created" if persisted.created_company else "updated"
        typer.echo(f"Stored company identity: {persisted.company_id} ({state})")

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote GLEIF UK company search result to {out}")


@app.command("find-us-company")
def find_us_company(
    name: str = typer.Argument(..., help="US company name to search, e.g. Alphabet"),
    limit: int = typer.Option(10, min=1, max=25, help="Maximum number of candidates to return."),
    out: Optional[Path] = typer.Option(None, help="Optional path to write the result as JSON."),
) -> None:
    """Search SEC company ticker data for a US filer candidate."""
    settings = get_settings()
    client = SECTickerClient(settings)
    try:
        results = client.search_company(name, limit=limit)
    except SECTickerError as exc:
        logger.error("SEC company search failed: %s", exc)
        raise typer.Exit(code=1) from exc

    profiles_by_cik = _build_us_profiles(settings, results)
    payload = _build_us_resolver_payload(
        query=name,
        results=results,
        profiles_by_cik=profiles_by_cik,
    )
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote SEC US company search result to {out}")


@app.command("resolve-company")
def resolve_company(
    name: str = typer.Argument(..., help="Company name to resolve."),
    market: Optional[str] = typer.Option(
        None,
        help="Optional market selector: uk, us, or auto. Omit to search both.",
    ),
    limit: int = typer.Option(10, min=1, max=25, help="Maximum candidates per market."),
    out: Optional[Path] = typer.Option(None, help="Optional path to write the result as JSON."),
) -> None:
    """Resolve a company name across UK and/or US identity sources without auto-picking a final identity."""
    selected = (market or "auto").strip().lower()
    if selected not in {"auto", "uk", "us"}:
        typer.echo("Market must be one of: uk, us, auto.")
        raise typer.Exit(code=1)

    payload: dict[str, object] = {
        "query": name,
        "markets_searched": ["uk", "us"] if selected == "auto" else [selected],
        "uk_candidates": [],
        "us_candidates": [],
    }

    if selected in {"auto", "uk"}:
        uk_client = GLEIFClient()
        try:
            uk_results = uk_client.search_company(name, country="GB", limit=limit)
        except GLEIFError:
            uk_results = []
        else:
            for result in uk_results:
                result.isins = uk_client.get_isins(result.lei)
            uk_results = _rank_uk_candidates(query=name, results=uk_results)
        payload["uk_candidates"] = _build_uk_candidate_list(uk_results)

    if selected in {"auto", "us"}:
        settings = get_settings()
        us_client = SECTickerClient(settings)
        try:
            us_results = us_client.search_company(name, limit=limit)
        except SECTickerError:
            us_results = []
            us_profiles_by_cik = {}
        else:
            us_profiles_by_cik = _build_us_profiles(settings, us_results)
        payload["us_candidates"] = _build_us_candidate_list(
            us_results,
            profiles_by_cik=us_profiles_by_cik,
        )

    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote company resolution result to {out}")


@app.command("extract-text")
def extract_text_command(
    file: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False,
                              help="PDF or HTML file to extract text from."),
    max_chars: Optional[int] = typer.Option(None, help="Truncate output to this many characters."),
    out: Optional[Path] = typer.Option(None, help="Optional path to write extracted text."),
    document_id: Optional[str] = typer.Option(
        None,
        help="Optional existing document ID when persisting extracted text.",
    ),
    persist: bool = typer.Option(
        False,
        "--persist/--no-persist",
        help="Persist the extracted narrative into the store.",
    ),
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

    if persist:
        settings = get_settings()
        try:
            with session_scope(settings) as session:
                persisted = ExtractionPersistenceService(session).persist_text_extraction(
                    file_path=file,
                    text=text,
                    document_id=document_id,
                )
        except Exception as exc:
            logger.error("Text extraction persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"Stored text extraction: {persisted.extraction_id} "
            f"for document {persisted.document_id}"
        )


@app.command("init-db")
def init_db() -> None:
    """Apply Alembic migrations for the core company store schema."""
    settings = get_settings()
    try:
        run_migrations_to_head(settings)
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo("Database initialized.")
    typer.echo(f"DATABASE_URL={_redact_database_url(settings.database_url)}")
    typer.echo("Migration target: head")
    typer.echo("Expected core tables:")
    for table_name in sorted(STORE_TABLES):
        typer.echo(f"  - {table_name}")


@app.command("backup")
def backup_command(
    target: Optional[Path] = typer.Option(
        None,
        help="Backup root directory. Defaults to BACKUP_TARGET_DIR from the environment.",
    ),
) -> None:
    """Create a local backup snapshot with a pg_dump SQL export and copied data directory."""
    settings = get_settings()
    try:
        result = run_backup(settings=settings, target_root=target, progress=typer.echo)
    except BackupError as exc:
        logger.error("Backup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Backup created: {result.backup_dir}")
    typer.echo(f"  Database dump: {result.db_dump_path}")
    typer.echo(f"  Data copy: {result.data_copy_path}")
    typer.echo(f"  Manifest: {result.manifest_path}")


@app.command("show-company")
def show_company(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
) -> None:
    """Show a stored company identity record with identifiers and primary listing."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            company_record = store.get_company(company)
            payload = {
                "company": asdict(company_record),
                "identifiers": [asdict(item) for item in store.get_identifiers(company_record.company_id)],
                "primary_listing": (
                    asdict(store.get_primary_listing(company_record.company_id))
                    if store.get_primary_listing(company_record.company_id) is not None
                    else None
                ),
            }
    except Exception as exc:
        logger.error("Company lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("ingest-edgar-filings")
def ingest_edgar_filings(
    cik: str = typer.Option(..., help="SEC CIK, with or without leading zeros."),
    forms: str = typer.Option(
        "10-K,10-Q,8-K,20-F,40-F,6-K",
        help="Comma-separated EDGAR forms to include.",
    ),
    limit: int = typer.Option(20, min=1, max=200, help="Maximum number of filings to return."),
    download: bool = typer.Option(
        False,
        "--download/--no-download",
        help="Download the discovered primary filing documents.",
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Persist the discovered company and filing records into the store.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing the discovery result as JSON.",
    ),
    materialize: bool = typer.Option(
        False,
        "--materialize/--no-materialize",
        help="After persistence, extract and chunk each stored filing where a source artifact is available.",
    ),
) -> None:
    """Discover EDGAR filings for a company by CIK using SEC public endpoints."""
    settings = get_settings()
    client = EdgarClient(settings)
    form_list = tuple(item.strip().upper() for item in forms.split(",") if item.strip())

    form_guidance = None
    try:
        filer_profile = client.inspect_filer(cik=cik)
    except EdgarError:
        filer_profile = None
    else:
        form_guidance = _build_edgar_form_guidance(
            cik=cik,
            requested_forms=form_list,
            filer_profile=filer_profile,
        )
        if not form_guidance["forms_match"]:
            typer.echo(
                json.dumps(
                    {
                        "edgar_form_guidance": form_guidance,
                        "warning": (
                            "Requested forms do not match the likely EDGAR filing family "
                            "for this issuer."
                        ),
                    },
                    indent=2,
                )
            )

    try:
        submissions = client.discover_filings(cik=cik, forms=form_list, limit=limit)
    except EdgarError as exc:
        logger.error("EDGAR discovery failed: %s", exc)
        raise typer.Exit(code=1) from exc

    downloaded_files: dict[str, Path] = {}
    if download:
        for filing in submissions.filings:
            try:
                downloaded_files[filing.accession_number] = client.download_filing(filing)
            except EdgarError as exc:
                logger.warning("EDGAR download failed for %s: %s", filing.accession_number, exc)

    payload = submissions.model_dump(mode="json")
    if form_guidance is not None:
        payload["edgar_form_guidance"] = form_guidance
    if downloaded_files:
        payload["downloaded_files"] = {
            accession: str(path) for accession, path in downloaded_files.items()
        }
    typer.echo(json.dumps(payload, indent=2))

    if persist:
        try:
            with session_scope(settings) as session:
                persisted = EdgarIngestionService(session).persist_submissions(
                    submissions=submissions,
                    downloaded_files=downloaded_files or None,
                )
                materialized: list[dict[str, object]] = []
                if materialize:
                    for stored_document_id in persisted.document_ids:
                        try:
                            materialized.append(
                                _derive_document_context(
                                    session=session,
                                    document_id=stored_document_id,
                                )
                            )
                        except Exception as exc:
                            logger.warning(
                                "Document materialization skipped for %s: %s",
                                stored_document_id,
                                exc,
                            )
        except Exception as exc:
            logger.error("EDGAR persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"Stored EDGAR company {persisted.company_id} "
            f"with {len(persisted.document_ids)} filings"
        )
        if materialize and materialized:
            typer.echo(json.dumps({"derived_document_contexts": materialized}, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote EDGAR discovery result to {out}")


@app.command("list-documents")
def list_documents(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
) -> None:
    """List stored documents for a company."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            company_record = store.get_company(company)
            documents = store.get_latest_documents(company_record.company_id)
            payload = {
                "company": asdict(company_record),
                "documents": [asdict(item) for item in documents],
            }
    except Exception as exc:
        logger.error("Document lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("list-artifacts")
def list_artifacts(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
) -> None:
    """List stored artifacts for a company with document provenance."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            company_record = store.get_company(company)
            artifacts = store.list_artifacts_for_company(company_record.company_id)
            payload = {
                "company": asdict(company_record),
                "artifacts": [
                    {
                        "artifact": asdict(item.artifact),
                        "document": asdict(item.document),
                    }
                    for item in artifacts
                ],
            }
    except Exception as exc:
        logger.error("Artifact lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("show-document")
def show_document(
    document_id: str = typer.Option(..., help="Stored document ID."),
) -> None:
    """Show a stored document and its associated artifacts."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            document = store.get_document(document_id)
            artifacts = store.get_document_artifacts(document_id)
            payload = {
                "document": asdict(document),
                "artifacts": [asdict(item) for item in artifacts],
            }
    except Exception as exc:
        logger.error("Document detail lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("show-artifact")
def show_artifact(
    artifact_id: str = typer.Option(..., help="Stored artifact ID."),
) -> None:
    """Show a stored artifact with its parent document and company provenance."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            result = store.get_artifact(artifact_id)
            payload = {
                "company": asdict(result.company),
                "document": asdict(result.document),
                "artifact": asdict(result.artifact),
            }
    except Exception as exc:
        logger.error("Artifact detail lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("show-facts")
def show_facts(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
    document_role: Optional[str] = typer.Option(
        None,
        help="Optional document role filter, e.g. ANNUAL_REPORT or INTERIM_REPORT.",
    ),
    limit: int = typer.Option(50, min=1, max=500, help="Maximum number of facts to show."),
) -> None:
    """Show stored structured facts for a company."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            company_record = store.get_company(company)
            fact_set = store.get_fact_set(company_record.company_id, document_role=document_role)
            payload = {
                "company": asdict(company_record),
                "document_role": document_role,
                "fact_count": len(fact_set.facts),
                "facts": fact_set.facts[:limit],
            }
    except Exception as exc:
        logger.error("Fact lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("chunk-document")
def chunk_document(
    document_id: str = typer.Option(..., help="Stored document ID to chunk from narrative extracts."),
    max_chars: int = typer.Option(1200, min=200, max=5000, help="Target chunk size in characters."),
    overlap_chars: int = typer.Option(150, min=0, max=1000, help="Character overlap between adjacent chunks."),
) -> None:
    """Chunk stored narrative extracts for a document into retrieval passages."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            result = NarrativeChunkingService(session).chunk_document(
                document_id=document_id,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
    except Exception as exc:
        logger.error("Document chunking failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Stored {result.chunk_count} chunks for document {result.document_id}"
    )


@app.command("derive-document-context")
def derive_document_context(
    document_id: str = typer.Option(..., help="Stored document ID to extract and chunk."),
    strategy: str = typer.Option(
        "auto",
        help="Extraction strategy: auto, ixbrl, or text.",
    ),
    chunk: bool = typer.Option(
        True,
        "--chunk/--no-chunk",
        help="Chunk stored narratives after extraction.",
    ),
    max_chars: int = typer.Option(1200, min=200, max=5000, help="Target chunk size in characters."),
    overlap_chars: int = typer.Option(150, min=0, max=1000, help="Character overlap between adjacent chunks."),
) -> None:
    """Derive stored facts, narratives, and retrieval chunks from a stored document."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            result = DocumentPipelineService(session).materialize_document(
                document_id=document_id,
                strategy=strategy,
                chunk=chunk,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
            payload = {
                "document_id": result.document_id,
                "artifact_id": result.artifact_id,
                "artifact_path": result.artifact_path,
                "strategy": result.strategy,
                "extraction_id": result.extraction_id,
                "fact_count": result.fact_count,
                "narrative_count": result.narrative_count,
                "chunk_count": result.chunk_count,
            }
    except Exception as exc:
        logger.error("Document materialization failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("derive-company-context")
def derive_company_context(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
    document_role: list[str] = typer.Option(
        None,
        "--document-role",
        help="Optional document role filter. Repeatable, e.g. --document-role ANNUAL_REPORT.",
    ),
    latest_only: bool = typer.Option(
        True,
        "--latest-only/--all-matching",
        help="Only derive the latest document per selected role.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        min=1,
        max=50,
        help="Optional cap on number of documents to derive after filtering.",
    ),
    strategy: str = typer.Option(
        "auto",
        help="Extraction strategy: auto, ixbrl, or text.",
    ),
    chunk: bool = typer.Option(
        True,
        "--chunk/--no-chunk",
        help="Chunk stored narratives after extraction.",
    ),
    max_chars: int = typer.Option(1200, min=200, max=5000, help="Target chunk size in characters."),
    overlap_chars: int = typer.Option(150, min=0, max=1000, help="Character overlap between adjacent chunks."),
) -> None:
    """Derive stored facts, narratives, and retrieval chunks for selected company documents."""
    settings = get_settings()
    normalized_roles = _normalize_document_roles(document_role)
    try:
        with session_scope(settings) as session:
            derivation = CompanyContextBuilderService(session, settings).derive(
                company_ref=company,
                document_roles=normalized_roles,
                latest_only=latest_only,
                limit=limit,
                strategy=strategy,
                chunk=chunk,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
            payload = asdict(derivation)
    except Exception as exc:
        logger.error("Company context derivation failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("build-company-context")
def build_company_context(
    company_name: str = typer.Argument(..., help="Company name to resolve, ingest, and derive."),
    market: str = typer.Option(
        "auto",
        help="Market selector: uk, us, or auto.",
    ),
    limit: int = typer.Option(10, min=1, max=25, help="Maximum resolution candidates per market."),
    selection: Optional[int] = typer.Option(
        None,
        help="Optional 1-based candidate selection to avoid the interactive prompt.",
    ),
    uk_document_type: str = typer.Option(
        "annual-report",
        help="UK NSM document type to ingest when a UK company is selected.",
    ),
    us_forms: Optional[str] = typer.Option(
        None,
        help="Optional comma-separated EDGAR forms override when a US company is selected.",
    ),
    us_limit: int = typer.Option(
        10,
        min=1,
        max=200,
        help="Maximum EDGAR filings to ingest when a US company is selected.",
    ),
    download: bool = typer.Option(
        True,
        "--download/--no-download",
        help="Download filing documents during ingestion where supported.",
    ),
    derive_latest_only: bool = typer.Option(
        True,
        "--latest-only/--all-derived",
        help="Derive only the latest stored document per role after ingestion.",
    ),
    derive_limit: Optional[int] = typer.Option(
        None,
        min=1,
        max=50,
        help="Optional cap on number of documents to derive after ingestion.",
    ),
    strategy: str = typer.Option(
        "auto",
        help="Derivation strategy: auto, ixbrl, or text.",
    ),
    chunk: bool = typer.Option(
        True,
        "--chunk/--no-chunk",
        help="Chunk stored narratives after derivation.",
    ),
    max_chars: int = typer.Option(1200, min=200, max=5000, help="Target chunk size in characters."),
    overlap_chars: int = typer.Option(150, min=0, max=1000, help="Character overlap between adjacent chunks."),
) -> None:
    """Resolve a company, ingest the relevant filings, and derive retrieval-ready company context."""
    selected_market = market.strip().lower()
    if selected_market not in {"auto", "uk", "us"}:
        typer.echo("Market must be one of: uk, us, auto.")
        raise typer.Exit(code=1)

    settings = get_settings()

    try:
        candidates = _resolve_company_candidates(
            name=company_name,
            market=selected_market,
            limit=limit,
            settings=settings,
        )
        chosen = _choose_company_candidate(candidates=candidates, selection=selection)
    except Exception as exc:
        logger.error("Company resolution failed: %s", exc)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope(settings) as session:
            build_result = CompanyContextBuilderService(session, settings).build(
                chosen=chosen,
                uk_document_type=uk_document_type,
                us_forms=us_forms,
                us_limit=us_limit,
                download=download,
                derive_latest_only=derive_latest_only,
                derive_limit=derive_limit,
                strategy=strategy,
                chunk=chunk,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
    except Exception as exc:
        logger.error("Company context build failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = {
        "requested_company_name": company_name,
        "resolution": {
            "market": chosen.market,
            "display_name": chosen.display_name,
            "summary": chosen.summary,
            "payload": chosen.payload,
        },
        "ingestion": build_result.ingestion_summary,
        "derived_company_context": asdict(build_result.derived_context),
    }
    typer.echo(json.dumps(payload, indent=2))


@app.command("search-passages")
def search_passages(
    company: str = typer.Option(..., help="Company ID, name, or known identifier value."),
    query: str = typer.Option(..., help="Case-insensitive substring to search for in stored chunks."),
    document_role: Optional[str] = typer.Option(
        None,
        help="Optional document role filter, e.g. ANNUAL_REPORT or INTERIM_REPORT.",
    ),
    limit: int = typer.Option(20, min=1, max=100, help="Maximum number of passages to return."),
) -> None:
    """Search chunked filing passages stored for a company."""
    settings = get_settings()
    try:
        with session_scope(settings) as session:
            store = SQLCompanyContextStore(session)
            company_record = store.get_company(company)
            passages = store.search_passages(
                company_record.company_id,
                query=query,
                document_role=document_role,
                limit=limit,
            )
            payload = {
                "company": asdict(company_record),
                "query": query,
                "document_role": document_role,
                "passage_count": len(passages),
                "passages": [asdict(item) for item in passages],
            }
    except Exception as exc:
        logger.error("Passage search failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, indent=2))


@app.command("restore")
def restore_command(
    backup_dir: Path = typer.Option(
        ...,
        "--from",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Path to a backup snapshot directory created by 'research backup'.",
    ),
    mode: RestoreMode = typer.Option(
        RestoreMode.FULL,
        help="Restore files only, database only, or both.",
    ),
    target_data_dir: Optional[Path] = typer.Option(
        None,
        help="Override DATA_DIR for the restore target.",
    ),
    target_db_url: Optional[str] = typer.Option(
        None,
        help="Override DATABASE_URL for the restore target.",
    ),
    apply: bool = typer.Option(
        False,
        help="Perform the restore. Without this flag the command runs as a dry-run plan only.",
    ),
    pre_backup: bool = typer.Option(
        True,
        "--pre-backup/--no-pre-backup",
        help="Create a fresh safety backup before restoring.",
    ),
) -> None:
    """Safely restore a backup snapshot with dry-run by default and typed confirmation on apply."""
    settings = get_settings()
    preflight = run_restore_preflight(
        settings=settings,
        backup_dir=backup_dir,
        mode=mode,
        target_data_dir=target_data_dir,
        target_database_url=target_db_url,
        create_pre_restore_backup=pre_backup,
    )
    plan = preflight.plan

    typer.echo("Restore plan:")
    typer.echo(f"  Backup: {plan.backup_dir if plan else backup_dir.resolve()}")
    typer.echo(f"  Mode: {mode.value}")
    typer.echo(f"  SQL dump: {plan.db_dump_path if plan and plan.db_dump_path else '(not used)'}")
    typer.echo(f"  Backup data: {plan.data_copy_path if plan and plan.data_copy_path else '(not used)'}")
    typer.echo(
        f"  Target DB: {plan.target_database_url_redacted if plan and plan.target_database_url_redacted else '(not used)'}"
    )
    typer.echo(f"  Target data dir: {plan.target_data_dir if plan and plan.target_data_dir else '(not used)'}")
    typer.echo(
        f"  Pre-restore backup root: {plan.pre_restore_backup_root if plan and pre_backup else '(disabled)'}"
    )

    typer.echo("\nPreflight checklist:")
    for check in preflight.checks:
        typer.echo(f"  [{check.status.value}] {check.name}: {check.details}")

    if not apply:
        if not preflight.ok:
            typer.echo("\nPreflight failed. Fix the failed items before running with --apply.")
            raise typer.Exit(code=1)
        typer.echo("\nDry run only. Re-run with --apply to execute the restore.")
        return

    if not preflight.ok:
        typer.echo("\nRestore blocked because preflight failed.")
        raise typer.Exit(code=1)

    confirmation = typer.prompt("\nType RESTORE to continue")
    if confirmation != "RESTORE":
        typer.echo("Restore cancelled.")
        raise typer.Exit(code=1)

    try:
        result = run_restore(
            settings=settings,
            backup_dir=backup_dir,
            mode=mode,
            target_data_dir=target_data_dir,
            target_database_url=target_db_url,
            create_pre_restore_backup=pre_backup,
            progress=typer.echo,
        )
    except BackupError as exc:
        logger.error("Restore failed: %s", exc)
        raise typer.Exit(code=1) from exc

    typer.echo("\nRestore complete:")
    typer.echo(f"  Mode: {result.mode.value}")
    typer.echo(f"  Source backup: {result.backup_dir}")
    typer.echo(
        f"  Pre-restore backup: {result.pre_restore_backup_dir if result.pre_restore_backup_dir else '(disabled)'}"
    )
    typer.echo(f"  Restored data dir: {result.target_data_dir if result.target_data_dir else '(not used)'}")
    typer.echo(
        f"  Previous data snapshot: {result.data_rollback_dir if result.data_rollback_dir else '(none)'}"
    )
    typer.echo(
        f"  Restored DB: {result.target_database_url_redacted if result.target_database_url_redacted else '(not used)'}"
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
    company_id: Optional[str] = typer.Option(
        None,
        help="Optional existing company ID to attach the stored document to.",
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Persist the ingested document and artifacts into the store.",
    ),
    materialize: bool = typer.Option(
        False,
        "--materialize/--no-materialize",
        help="After persistence, extract and chunk the stored document.",
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

    if persist:
        try:
            with session_scope(settings) as session:
                write_result = DocumentIngestionService(session).persist_nsm_download_result(
                    query=query,
                    result=result,
                    company_id=company_id,
                )
                materialized = None
                if materialize:
                    materialized = _derive_document_context(
                        session=session,
                        document_id=write_result.document_id,
                    )
        except Exception as exc:
            logger.error("NSM document persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"Stored NSM document: {write_result.document_id} "
            f"for company {write_result.company_id} "
            f"with {len(write_result.artifacts)} artifacts"
        )
        if materialize and materialized is not None:
            typer.echo(json.dumps({"derived_document_context": materialized}, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote NSM acquisition metadata to {out}")


@app.command("ingest-nsm-company")
def ingest_nsm_company(
    lei: str = typer.Option(..., help="GLEIF LEI for the UK company."),
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
    company_id: Optional[str] = typer.Option(
        None,
        help="Optional existing company ID to attach the stored document to.",
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Persist the ingested document and artifacts into the store.",
    ),
    materialize: bool = typer.Option(
        False,
        "--materialize/--no-materialize",
        help="After persistence, extract and chunk the stored document.",
    ),
) -> None:
    """Resolve a UK company by LEI, then retrieve and optionally store NSM filings."""
    try:
        gleif_record = GLEIFClient().get_record(lei)
        gleif_record.isins = GLEIFClient().get_isins(lei)
    except GLEIFError as exc:
        logger.error("GLEIF LEI lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    query = gleif_record.legal_name
    typer.echo(
        json.dumps(
            {
                "lei": gleif_record.lei,
                "legal_name": gleif_record.legal_name,
                "isins": gleif_record.isins,
                "document_type": document_type,
            },
            indent=2,
        )
    )

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

    payload = {
        "resolved_identity": {
            "lei": gleif_record.lei,
            "legal_name": gleif_record.legal_name,
            "isins": gleif_record.isins,
        },
        "nsm_result": result.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2))

    if persist:
        try:
            with session_scope(settings) as session:
                if company_id is None:
                    gleif_write = CompanyIdentityService(session).upsert_from_gleif(record=gleif_record)
                    target_company_id = gleif_write.company_id
                else:
                    target_company_id = company_id

                write_result = DocumentIngestionService(session).persist_nsm_download_result(
                    query=query,
                    result=result,
                    company_id=target_company_id,
                )
                materialized = None
                if materialize:
                    materialized = _derive_document_context(
                        session=session,
                        document_id=write_result.document_id,
                    )
        except Exception as exc:
            logger.error("NSM company persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"Stored NSM document: {write_result.document_id} "
            f"for company {write_result.company_id} "
            f"with {len(write_result.artifacts)} artifacts"
        )
        if materialize and materialized is not None:
            typer.echo(json.dumps({"derived_document_context": materialized}, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote NSM company acquisition metadata to {out}")


@app.command("discover-nsm-annual-history")
def discover_nsm_annual_history(
    lei: str = typer.Option(..., help="GLEIF LEI for the UK company."),
    years: int = typer.Option(5, min=1, max=10, help="How many filing years to try to discover."),
    headed: bool = typer.Option(
        False,
        help="Launch a visible browser window for debugging the site flow.",
    ),
    browser_channel: Optional[str] = typer.Option(
        None,
        help="Optional browser channel, for example 'chrome' or 'msedge'.",
    ),
    max_candidates: int = typer.Option(
        50,
        min=10,
        max=200,
        help="Maximum NSM result rows to inspect before local annual-history selection.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing discovery output as JSON.",
    ),
) -> None:
    """Discover likely annual-report candidates across recent years for a UK company via NSM."""
    try:
        gleif_client = GLEIFClient()
        gleif_record = gleif_client.get_record(lei)
        gleif_record.isins = gleif_client.get_isins(lei)
    except GLEIFError as exc:
        logger.error("GLEIF LEI lookup failed: %s", exc)
        raise typer.Exit(code=1) from exc

    settings = get_settings()
    service = NSMDownloadService(settings=settings)
    try:
        discovery = service.discover_annual_history(
            query=gleif_record.legal_name,
            years=years,
            headed=headed,
            browser_channel=browser_channel,
            max_candidates=max_candidates,
        )
    except NSMSearchError as exc:
        logger.error("NSM annual history discovery failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = {
        "resolved_identity": {
            "lei": gleif_record.lei,
            "legal_name": gleif_record.legal_name,
            "isins": gleif_record.isins,
        },
        "annual_history_discovery": discovery.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote NSM annual history discovery to {out}")


@app.command("discover-edgar-annual-history")
def discover_edgar_annual_history(
    cik: str = typer.Option(..., help="SEC CIK, with or without leading zeros."),
    years: int = typer.Option(5, min=1, max=10, help="How many filing years to try to discover."),
    limit: int = typer.Option(
        100,
        min=10,
        max=500,
        help="Maximum EDGAR annual filings to inspect before local year selection.",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Optional path for writing discovery output as JSON.",
    ),
) -> None:
    """Discover likely annual filing candidates across recent years for a US filer via EDGAR."""
    settings = get_settings()
    client = EdgarClient(settings)
    try:
        discovery = client.discover_annual_history(cik=cik, years=years, limit=limit)
    except EdgarError as exc:
        logger.error("EDGAR annual history discovery failed: %s", exc)
        raise typer.Exit(code=1) from exc

    payload = {
        "annual_history_discovery": discovery.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2))

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote EDGAR annual history discovery to {out}")


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
    document_id: Optional[str] = typer.Option(
        None,
        help="Optional existing document ID when persisting extracted facts.",
    ),
    persist: bool = typer.Option(
        False,
        "--persist/--no-persist",
        help="Persist extracted iXBRL facts and narratives into the store.",
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

    if persist:
        settings = get_settings()
        try:
            with session_scope(settings) as session:
                persisted = ExtractionPersistenceService(session).persist_ixbrl_extraction(
                    extraction=result,
                    document_id=document_id,
                )
        except Exception as exc:
            logger.error("iXBRL extraction persistence failed: %s", exc)
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"Stored iXBRL extraction: {persisted.extraction_id} "
            f"for document {persisted.document_id} "
            f"with {persisted.fact_count} facts and {persisted.narrative_count} narratives"
        )


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
