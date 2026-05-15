from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional
from zipfile import ZipFile

from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from research_platform.core.config import Settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


class NSMSearchError(RuntimeError):
    """Raised when the NSM site cannot be searched with the current configuration."""


class NSMCandidate(BaseModel):
    title: str
    date_text: Optional[str] = None
    organisation_name: Optional[str] = None
    category: Optional[str] = None
    href: Optional[str] = None


class NSMDownloadRequest(BaseModel):
    query: str
    document_type: str = "annual-report"
    headed: bool = False
    browser_channel: Optional[str] = None
    max_results: int = 10


class NSMDownloadResult(BaseModel):
    query: str
    document_type: str
    acquired_at: datetime
    base_url: str
    result_page_url: Optional[str] = None
    candidates: list[NSMCandidate] = Field(default_factory=list)
    selected_candidate: Optional[NSMCandidate] = None
    downloaded_file: Optional[str] = None
    extracted_dir: Optional[str] = None
    primary_report_file: Optional[str] = None
    extracted_files: list[str] = Field(default_factory=list)
    sha256: Optional[str] = None
    screenshot_path: Optional[str] = None
    html_snapshot_path: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class NSMDownloadService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, request: NSMDownloadRequest) -> NSMDownloadResult:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise NSMSearchError(
                "Playwright is not installed. Run 'pip install playwright' and "
                "'python -m playwright install'."
            ) from exc

        result = NSMDownloadResult(
            query=request.query,
            document_type=request.document_type,
            acquired_at=datetime.now(UTC),
            base_url=self.settings.nsm_base_url,
        )

        artifact_dir = self._artifact_dir(request.query)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        download_dir = self._download_dir(request.query)
        download_dir.mkdir(parents=True, exist_ok=True)

        channel = request.browser_channel or self.settings.browser_channel

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not request.headed, channel=channel)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            try:
                page.goto(self.settings.nsm_base_url, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")
                self._wait_for_shell(page=page, result=result)
                self._dismiss_cookie_banner(page=page, result=result)
                self._accept_terms_if_present(page=page, result=result)
                self._wait_for_shell(page=page, result=result)
                self._wait_for_loading_to_finish(page=page, result=result)
                result.result_page_url = page.url
                result.notes.append(
                    "Opened NSM landing page and captured debug artifacts."
                )

                self._capture_artifacts(page=page, artifact_dir=artifact_dir, result=result)

                if not self.settings.nsm_search_input_selector:
                    result.notes.append(
                        "Search selectors are not configured yet. This scaffold captured the landing page only."
                    )
                    return result

                self._run_search(page=page, request=request)
                result.result_page_url = page.url
                candidates = self._collect_candidates(
                    page=page,
                    max_results=request.max_results,
                    query=request.query,
                    document_type=request.document_type,
                )
                result.candidates = candidates
                result.selected_candidate = self._select_candidate(
                    candidates=candidates,
                    document_type=request.document_type,
                )

                # If the best match is from the old data-migration archive, retry without
                # the category filter — some companies (e.g. smaller UK firms) file annual
                # results under "Final Results" rather than "Annual Financial Report".
                if self._is_data_migration_result(result.selected_candidate):
                    result.notes.append(
                        "Best candidate was a data-migration file; retrying without category filter."
                    )
                    broad_request = request.model_copy(
                        update={"document_type": "annual-report-broad"}
                    )
                    self._run_search(page=page, request=broad_request)
                    broad_candidates = self._collect_candidates(
                        page=page,
                        max_results=request.max_results,
                        query=request.query,
                        document_type=request.document_type,
                    )
                    broad_selected = self._select_candidate(
                        candidates=broad_candidates,
                        document_type=request.document_type,
                    )
                    if broad_selected and not self._is_data_migration_result(broad_selected):
                        result.candidates = broad_candidates
                        result.selected_candidate = broad_selected

                self._capture_artifacts(page=page, artifact_dir=artifact_dir, result=result)

                if result.selected_candidate is None:
                    result.notes.append(
                        f"No '{request.document_type}' candidate was identified on the current results page."
                    )
                else:
                    downloaded_file = self._download_selected_candidate(
                        page=page,
                        candidate=result.selected_candidate,
                        download_dir=download_dir,
                    )
                    if downloaded_file is not None:
                        result.downloaded_file = str(downloaded_file)
                        extracted_dir, primary_report_file, extracted_files = (
                            self._prepare_downloaded_artifact(downloaded_file)
                        )
                        result.extracted_dir = str(extracted_dir) if extracted_dir else None
                        result.primary_report_file = (
                            str(primary_report_file) if primary_report_file else None
                        )
                        result.extracted_files = [str(path) for path in extracted_files]
                        result.sha256 = self._sha256(downloaded_file)
                    else:
                        result.notes.append("No downloadable candidate link was identified.")

            except PlaywrightTimeoutError as exc:
                raise NSMSearchError(f"Timed out while interacting with NSM: {exc}") from exc
            finally:
                context.close()
                browser.close()

        return result

    @staticmethod
    def _is_data_migration_result(candidate: Optional[NSMCandidate]) -> bool:
        """Return True if the candidate href points to an old data-migration file."""
        if candidate is None:
            return True
        href = (candidate.href or "").lower()
        return "/data-migration/" in href

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(NSMSearchError),
        reraise=True,
    )
    def _run_search(self, page, request: NSMDownloadRequest) -> None:
        if not self.settings.nsm_search_input_selector:
            raise NSMSearchError("NSM search input selector is not configured.")

        self._apply_search_filters(page=page, request=request)

        search_selector = self._visible_selector(self.settings.nsm_search_input_selector)
        search_box = page.locator(search_selector).first
        search_box.wait_for(state="visible", timeout=15000)
        search_box.scroll_into_view_if_needed()
        search_box.click()
        search_box.fill(request.query)

        if self.settings.nsm_submit_selector:
            submit_selector = self._visible_selector(self.settings.nsm_submit_selector)
            page.locator(submit_selector).first.click()
        else:
            search_box.press("Enter")

        page.wait_for_load_state("networkidle")
        self._wait_for_loading_to_finish(page=page, result=None)
        self._wait_for_search_results(page=page)

    def _apply_search_filters(self, page, request: NSMDownloadRequest) -> None:
        category_label = self._document_type_to_category_label(request.document_type)
        if not category_label or not self.settings.nsm_category_dropdown_selector:
            return

        dropdown = page.locator(
            self._visible_selector(self.settings.nsm_category_dropdown_selector)
        ).first
        dropdown.wait_for(state="visible", timeout=15000)
        dropdown.scroll_into_view_if_needed()

        dropdown.click()

        # Wait for options to render.
        for loc in [
            page.locator("mat-option").filter(has_text=category_label).first,
            page.locator("[role='option']").filter(has_text=category_label).first,
            page.get_by_text(category_label, exact=True).last,
        ]:
            try:
                if loc.count():
                    loc.wait_for(state="visible", timeout=5000)
                    loc.click()
                    page.wait_for_timeout(300)
                    page.keyboard.press("Escape")
                    return
            except Exception:
                continue

        raise NSMSearchError(
            f"Unable to set NSM category filter to '{category_label}'."
        )

    def _wait_for_shell(self, page, result: NSMDownloadResult) -> None:
        try:
            page.locator(self.settings.nsm_ready_selector).first.wait_for(
                state="visible",
                timeout=self.settings.nsm_ready_timeout_ms,
            )
            page.wait_for_timeout(self.settings.nsm_shell_settle_ms)
        except Exception:
            result.notes.append(
                "Timed out waiting for the NSM app shell selector; continuing with captured page state."
            )

    def _dismiss_cookie_banner(self, page, result: NSMDownloadResult) -> None:
        selector = self.settings.nsm_cookie_accept_selector
        if not selector:
            return

        banner_button = page.locator(selector).first
        try:
            if banner_button.is_visible(timeout=3000):
                banner_button.click()
                page.wait_for_timeout(self.settings.nsm_cookie_settle_ms)
                result.notes.append("Accepted the NSM cookie banner.")
        except Exception:
            result.notes.append("Cookie banner was not detected or could not be dismissed automatically.")

    def _accept_terms_if_present(self, page, result: NSMDownloadResult) -> None:
        selector = self.settings.nsm_terms_accept_selector
        if not selector:
            return

        try:
            terms_button = page.locator(self._visible_selector(selector)).first
            if terms_button.count():
                terms_button.scroll_into_view_if_needed()
                terms_button.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(self.settings.nsm_terms_settle_ms)
                self._wait_for_loading_to_finish(page=page, result=result)
                result.notes.append("Accepted the NSM terms of use gate.")
                return
        except Exception:
            pass

        try:
            terms_container = page.locator(self.settings.nsm_terms_container_selector).first
            if terms_container.count():
                result.notes.append(
                    "NSM terms of use block is still present; accept selector may need adjustment."
                )
        except Exception:
            result.notes.append("Terms of use gate was not detected or could not be accepted automatically.")

    def _wait_for_loading_to_finish(self, page, result: Optional[NSMDownloadResult]) -> None:
        selector = self.settings.nsm_loading_overlay_selector
        if not selector:
            return

        try:
            overlay = page.locator(selector).first
            if overlay.count():
                overlay.wait_for(state="hidden", timeout=self.settings.nsm_loading_timeout_ms)
        except Exception:
            if result is not None:
                result.notes.append(
                    "Timed out waiting for the NSM loading overlay to clear; continuing with captured page state."
                )

    def _wait_for_search_results(self, page) -> None:
        page.wait_for_function(
            """
            ([overlaySelector, tableSelector, errorSelector, containerSelector]) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== "none" &&
                        style.visibility !== "hidden" &&
                        el.offsetParent !== null;
                };

                const overlay = document.querySelector(overlaySelector);
                if (isVisible(overlay)) {
                    return false;
                }

                const table = document.querySelector(tableSelector);
                if (table) {
                    return true;
                }

                const error = document.querySelector(errorSelector);
                if (error && error.textContent && error.textContent.trim().length > 0) {
                    return true;
                }

                const container = document.querySelector(containerSelector);
                if (!container) {
                    return false;
                }

                const text = (container.textContent || "").trim();
                return text.length > 0;
            }
            """,
            arg=[
                self.settings.nsm_loading_overlay_selector,
                self.settings.nsm_results_table_selector,
                self.settings.nsm_error_selector,
                self.settings.nsm_results_container_selector,
            ],
            timeout=self.settings.nsm_results_timeout_ms,
        )

    def _collect_candidates(
        self,
        page,
        max_results: int,
        query: str,
        document_type: str,
    ) -> list[NSMCandidate]:
        if not self.settings.nsm_result_row_selector:
            return []

        rows = page.locator(self.settings.nsm_result_row_selector)

        # NSM renders rows in two batches: migrated (older) records first, then
        # current-system records. Wait for evidence of the second batch by watching
        # for tablerow-10 — if it never appears within 5s, we proceed with what we have.
        try:
            page.wait_for_selector("#tablerow-10", state="attached", timeout=5000)
        except Exception:
            pass
        count = rows.count()
        candidates: list[NSMCandidate] = []

        for index in range(count):
            row = rows.nth(index)
            title = self._extract_text(row, self.settings.nsm_result_title_selector) or row.inner_text()
            date_text = self._extract_text(row, self.settings.nsm_result_date_selector)
            organisation_name = self._extract_text(row, self.settings.nsm_result_org_name_selector)
            category = self._extract_text(row, self.settings.nsm_result_category_selector)
            href = self._extract_href(row, self.settings.nsm_result_link_selector)
            candidates.append(
                NSMCandidate(
                    title=" ".join(title.split()),
                    date_text=" ".join(date_text.split()) if date_text else None,
                    organisation_name=" ".join(organisation_name.split()) if organisation_name else None,
                    category=" ".join(category.split()) if category else None,
                    href=href,
                )
            )

        ranked = self._rank_candidates(
            candidates=candidates,
            query=query,
            document_type=document_type,
        )
        return ranked[:max_results]

    @staticmethod
    def _rank_candidates(
        candidates: list[NSMCandidate],
        query: str,
        document_type: str,
    ) -> list[NSMCandidate]:
        normalized_type = document_type.strip().lower()
        normalized_query = " ".join(query.lower().split())
        keyword_sets = {
            "annual-report": (
                "annual financial report",
                "annual report",
                "annual results",
                "esef annual financial report",
                "audited final results",
                "final results",
                "full year results",
                "preliminary results",
            ),
            "interim-report": (
                "half-year financial report",
                "half-year results",
                "half year results",
                "interim report",
                "interim results",
                "half-year report",
                "half year report",
                "half-yearly report",
            ),
        }
        preferred_keywords = keyword_sets.get(normalized_type, ())

        def score(candidate: NSMCandidate) -> tuple[int, int, int, datetime]:
            haystack = " ".join(
                part for part in (candidate.title, candidate.category or "") if part
            ).lower()
            match_score = 0
            for index, keyword in enumerate(preferred_keywords):
                if keyword in haystack:
                    match_score = len(preferred_keywords) - index
                    break

            org_name = " ".join((candidate.organisation_name or "").lower().split())
            org_score = 0
            if org_name:
                if org_name == normalized_query:
                    org_score = 3
                elif normalized_query and normalized_query in org_name:
                    org_score = 2
                elif org_name.startswith(normalized_query):
                    org_score = 1

            title_score = NSMDownloadService._candidate_title_score(
                candidate=candidate,
                document_type=normalized_type,
            )
            href_score = NSMDownloadService._candidate_href_score(candidate)
            date_key = NSMDownloadService._parse_candidate_datetime(candidate.date_text)
            return (org_score, date_key, title_score, href_score + match_score)

        return sorted(candidates, key=score, reverse=True)

    @staticmethod
    def _select_candidate(
        candidates: list[NSMCandidate],
        document_type: str,
    ) -> Optional[NSMCandidate]:
        normalized_type = document_type.strip().lower()
        keyword_sets = {
            "annual-report": (
                "annual financial report",
                "annual report",
                "annual results",
                "esef annual financial report",
                "audited final results",
                "final results",
                "full year results",
                "preliminary results",
            ),
            "interim-report": (
                "half-year financial report",
                "half-year results",
                "half year results",
                "interim report",
                "interim results",
                "half-year report",
                "half year report",
                "half-yearly report",
            ),
        }
        preferred_keywords = keyword_sets.get(normalized_type, ())
        if not preferred_keywords:
            return candidates[0] if candidates else None

        best_candidate: Optional[NSMCandidate] = None
        best_score: tuple[datetime, int, int] | None = None

        for candidate in candidates:
            haystack = " ".join(
                part for part in (candidate.title, candidate.category or "") if part
            ).lower()
            if not any(keyword in haystack for keyword in preferred_keywords):
                continue

            candidate_score = (
                NSMDownloadService._parse_candidate_datetime(candidate.date_text),
                NSMDownloadService._candidate_title_score(
                    candidate=candidate,
                    document_type=normalized_type,
                ),
                NSMDownloadService._candidate_href_score(candidate),
            )
            if best_score is None or candidate_score > best_score:
                best_candidate = candidate
                best_score = candidate_score

        return best_candidate

    def _download_candidate(self, page, candidate: NSMCandidate, download_dir: Path) -> Path:
        href = candidate.href
        if not href:
            raise NSMSearchError("Selected candidate has no href.")

        if href.strip().lower().endswith(".pdf"):
            return self._download_pdf(href, download_dir)

        response = page.goto(href, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        content_type = ""
        if response is not None:
            content_type = (response.header_value("content-type") or "").lower()

        if "application/pdf" in content_type:
            return self._download_pdf(href, download_dir)

        target = download_dir / "nsm_document.html"
        target.write_text(page.content(), encoding="utf-8")
        return target

    @staticmethod
    def _download_pdf(href: str, download_dir: Path) -> Path:
        # Fetch directly rather than via browser to avoid PDF viewer interception.
        import httpx
        filename = Path(href.split("/")[-1].split("?")[0]) or Path("nsm_document.pdf")
        if not filename.suffix:
            filename = Path("nsm_document.pdf")
        target = download_dir / filename
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            response = client.get(href)
            response.raise_for_status()
            target.write_bytes(response.content)
        return target

    def _download_selected_candidate(
        self,
        page,
        candidate: NSMCandidate,
        download_dir: Path,
    ) -> Optional[Path]:
        if candidate.href:
            return self._download_candidate(
                page=page,
                candidate=candidate,
                download_dir=download_dir,
            )

        return self._download_candidate_via_dialog(
            page=page,
            candidate=candidate,
            download_dir=download_dir,
        )

    def _download_candidate_via_dialog(
        self,
        page,
        candidate: NSMCandidate,
        download_dir: Path,
    ) -> Optional[Path]:
        matching_row = self._find_candidate_row(page=page, candidate=candidate)
        if matching_row is None:
            return None

        title_target = matching_row.locator(self.settings.nsm_result_title_selector).first
        if not title_target.count():
            return None

        title_target.scroll_into_view_if_needed()
        title_target.click()

        dialog = page.locator(self.settings.nsm_download_dialog_selector).first
        dialog.wait_for(state="visible", timeout=15000)

        download_button = page.locator(self.settings.nsm_download_button_selector).first
        with page.expect_download(timeout=30000) as download_info:
            download_button.click()
        download = download_info.value
        suggested_name = download.suggested_filename or "nsm_document.pdf"
        target = download_dir / suggested_name
        download.save_as(target)
        return target

    def _prepare_downloaded_artifact(
        self,
        downloaded_file: Path,
    ) -> tuple[Optional[Path], Optional[Path], list[Path]]:
        suffix = downloaded_file.suffix.lower()
        if suffix != ".zip":
            return (None, downloaded_file, [])

        extract_dir = downloaded_file.parent / downloaded_file.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        with ZipFile(downloaded_file) as archive:
            archive.extractall(extract_dir)

        extracted_files = [
            path for path in extract_dir.rglob("*") if path.is_file()
        ]
        primary_report_file = self._detect_primary_report_file(extracted_files)
        return (extract_dir, primary_report_file, extracted_files)

    @staticmethod
    def _detect_primary_report_file(extracted_files: list[Path]) -> Optional[Path]:
        if not extracted_files:
            return None

        def score(path: Path) -> tuple[int, int, str]:
            suffix = path.suffix.lower()
            suffix_score = {
                ".xhtml": 5,
                ".html": 4,
                ".htm": 3,
                ".pdf": 2,
            }.get(suffix, 0)

            parts = {part.lower() for part in path.parts}
            reports_bonus = 2 if "reports" in parts else 0
            return (suffix_score, reports_bonus, str(path))

        ranked = sorted(extracted_files, key=score, reverse=True)
        best = ranked[0]
        return best if score(best)[0] > 0 else None

    def _find_candidate_row(self, page, candidate: NSMCandidate):
        rows = page.locator(self.settings.nsm_result_row_selector)
        count = rows.count()
        for index in range(count):
            row = rows.nth(index)
            row_title = self._extract_text(row, self.settings.nsm_result_title_selector)
            row_date = self._extract_text(row, self.settings.nsm_result_date_selector)
            row_org = self._extract_text(row, self.settings.nsm_result_org_name_selector)

            if not row_title:
                continue

            normalized_title = " ".join(row_title.split())
            normalized_date = " ".join(row_date.split()) if row_date else None
            normalized_org = " ".join(row_org.split()) if row_org else None

            if (
                normalized_title == candidate.title
                and normalized_date == candidate.date_text
                and normalized_org == candidate.organisation_name
            ):
                return row

        return None

    def _capture_artifacts(self, page, artifact_dir: Path, result: NSMDownloadResult) -> None:
        screenshot_path = artifact_dir / "nsm_page.png"
        html_path = artifact_dir / "nsm_page.html"
        page.screenshot(path=str(screenshot_path), full_page=True)
        html_path.write_text(page.content(), encoding="utf-8")
        result.screenshot_path = str(screenshot_path)
        result.html_snapshot_path = str(html_path)

    def _artifact_dir(self, query: str) -> Path:
        return self.settings.nsm_artifact_dir / self._slugify(query)

    def _download_dir(self, query: str) -> Path:
        return self.settings.nsm_download_dir / self._slugify(query)

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
        return "-".join(part for part in cleaned.split("-") if part) or "query"

    @staticmethod
    def _document_type_to_category_label(document_type: str) -> Optional[str]:
        mapping = {
            "annual-report": "Annual Financial Report",
            "interim-report": "Half-year Financial Report",
        }
        return mapping.get(document_type.strip().lower())

    @staticmethod
    def _candidate_title_score(candidate: NSMCandidate, document_type: str) -> int:
        haystack = " ".join(
            part for part in (candidate.title, candidate.category or "") if part
        ).lower()

        if document_type == "annual-report":
            phrase_scores = (
                ("esef tagged annual report and audited accounts", 9),
                ("esef tagged annual report", 8),
                ("annual report and audited accounts", 7),
                ("annual report and financial statements", 6),
                ("annual report and accounts", 5),
                ("annual report", 4),
                ("annual financial report", 3),
                ("audited final results", 6),
                ("final results", 5),
                ("full year results", 4),
                ("preliminary results", 3),
            )
        elif document_type == "interim-report":
            phrase_scores = (
                ("half-year financial report", 8),
                ("half-year results", 7),
                ("half year results", 7),
                ("half-year report", 7),
                ("half year report", 7),
                ("interim report", 6),
                ("interim results", 6),
                ("half-yearly report", 6),
                ("results", 1),
            )
        else:
            phrase_scores = ()

        for phrase, score in phrase_scores:
            if phrase in haystack:
                return score

        return 0

    @staticmethod
    def _candidate_href_score(candidate: NSMCandidate) -> int:
        href = (candidate.href or "").lower()
        if not href:
            # Null href often means the richer popup/download flow that yields zip/XHTML packages.
            return 3
        if "/rns/" in href:
            return 0
        if href.endswith(".pdf"):
            return 2
        if href.endswith(".zip") or href.endswith(".xhtml") or href.endswith(".xml"):
            return 3
        if href.endswith(".html") or href.endswith(".htm"):
            return 1
        return 1

    @staticmethod
    def _parse_candidate_datetime(value: Optional[str]) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=UTC)

        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue

        return datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def _extract_text(row, selector: str) -> Optional[str]:
        if not selector:
            return None
        locator = row.locator(selector).first
        return locator.inner_text() if locator.count() else None

    @staticmethod
    def _extract_href(row, selector: str) -> Optional[str]:
        locator = row.locator(selector) if selector else row.locator("a")
        count = locator.count()
        for index in range(count):
            href = locator.nth(index).get_attribute("href")
            if not href:
                continue
            normalized = href.strip().lower()
            if normalized.startswith("javascript:"):
                continue
            if normalized.startswith("http://") or normalized.startswith("https://"):
                return href
            if normalized.endswith(".pdf") or normalized.endswith(".html"):
                return href
        return None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _visible_selector(selector: str) -> str:
        return selector if ":visible" in selector else f"{selector}:visible"
