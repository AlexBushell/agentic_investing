from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}


class XHTMLReportParseError(RuntimeError):
    """Raised when an XHTML annual report cannot be parsed."""


class XHTMLPage(BaseModel):
    page_number: int
    line_count: int
    lines: list[str] = Field(default_factory=list)
    normalized_lines: list[str] = Field(default_factory=list)
    full_text: str
    normalized_full_text: str


class XHTMLContentsEntry(BaseModel):
    title: str
    normalized_title: str
    page_reference: int
    section_group: Optional[str] = None
    matched_page_number: Optional[int] = None
    match_confidence: str = "unmatched"


class XHTMLSection(BaseModel):
    title: str
    normalized_title: str
    start_page_number: int
    end_page_number: int
    source: str
    page_numbers: list[int] = Field(default_factory=list)


class XHTMLReportParseResult(BaseModel):
    file_path: str
    page_count: int
    contents_page_number: Optional[int] = None
    pages: list[XHTMLPage] = Field(default_factory=list)
    contents_entries: list[XHTMLContentsEntry] = Field(default_factory=list)
    sections: list[XHTMLSection] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class XHTMLReportParser:
    def parse(self, file_path: Path) -> XHTMLReportParseResult:
        if not file_path.exists():
            raise XHTMLReportParseError(f"XHTML report file not found: {file_path}")

        try:
            root = ET.parse(file_path).getroot()
        except ET.ParseError as exc:
            raise XHTMLReportParseError(f"Unable to parse XHTML report: {exc}") from exc

        pages = self._extract_pages(root)
        contents_page_number = self._find_contents_page_number(pages)
        contents_entries: list[XHTMLContentsEntry] = []
        notes: list[str] = []

        if contents_page_number is not None:
            contents_entries = self._extract_contents_entries(
                pages[contents_page_number - 1].lines
            )
            if contents_entries:
                self._match_contents_entries(contents_entries, pages)
            else:
                notes.append(
                    "A contents page was detected, but no TOC entries were parsed from it."
                )
        else:
            notes.append("No contents page was detected in the XHTML report.")

        sections = self._build_sections(contents_entries, pages)
        if not sections:
            notes.append(
                "No sections were built from the contents page; headings may require a different matching strategy."
            )

        notes.append(
            "The NSM/ESEF XHTML appears structurally consistent at the page-text level, but visible text can contain PDF-to-HTML spacing artifacts."
        )

        return XHTMLReportParseResult(
            file_path=str(file_path),
            page_count=len(pages),
            contents_page_number=contents_page_number,
            pages=pages,
            contents_entries=contents_entries,
            sections=sections,
            notes=notes,
        )

    def _extract_pages(self, root: ET.Element) -> list[XHTMLPage]:
        page_nodes = root.findall('.//x:div[@class="pf w0 h0"]', XHTML_NS)
        if not page_nodes:
            raise XHTMLReportParseError(
                "No XHTML page containers were found. Expected div.pf.w0.h0 blocks."
            )

        pages: list[XHTMLPage] = []
        for page_number, page_node in enumerate(page_nodes, start=1):
            lines = self._extract_page_lines(page_node)
            normalized_lines = [self._normalize_for_matching(line) for line in lines]
            full_text = "\n".join(lines)
            pages.append(
                XHTMLPage(
                    page_number=page_number,
                    line_count=len(lines),
                    lines=lines,
                    normalized_lines=normalized_lines,
                    full_text=full_text,
                    normalized_full_text=self._normalize_for_matching(full_text),
                )
            )
        return pages

    def _extract_page_lines(self, page_node: ET.Element) -> list[str]:
        lines: list[str] = []
        previous_line: Optional[str] = None

        for node in page_node.iter():
            if not isinstance(node.tag, str) or not node.tag.endswith("div"):
                continue

            css_class = node.attrib.get("class", "")
            if not css_class.startswith("t "):
                continue

            style = (node.attrib.get("style") or "").replace(" ", "").lower()
            if "display:none" in style or "visibility:hidden" in style:
                continue

            text = " ".join(" ".join(node.itertext()).split())
            if not text:
                continue

            if text == previous_line:
                continue

            lines.append(text)
            previous_line = text

        return lines

    def _find_contents_page_number(self, pages: list[XHTMLPage]) -> Optional[int]:
        best_page_number: Optional[int] = None
        best_score = 0

        for page in pages[: min(len(pages), 25)]:
            score = 0
            for line in page.lines:
                normalized = self._normalize_for_matching(line)
                if normalized == "contents":
                    score += 4
                elif "contents" in normalized:
                    score += 2

                if self._looks_like_toc_entry(line):
                    score += 1

            if score > best_score:
                best_score = score
                best_page_number = page.page_number

        return best_page_number if best_score >= 5 else None

    def _extract_contents_entries(self, lines: list[str]) -> list[XHTMLContentsEntry]:
        entries: list[XHTMLContentsEntry] = []
        current_group: Optional[str] = None

        for line in lines:
            compact = " ".join(line.split())
            normalized = self._normalize_for_matching(compact)
            if not normalized:
                continue

            if normalized in {"contents", "hello"}:
                continue

            if self._is_section_group_line(compact):
                current_group = compact
                continue

            match = re.match(r"^(?P<title>.+?)\s+(?P<page>\d(?:\s*\d)*)$", compact)
            if not match:
                continue

            raw_title = match.group("title").strip(" .")
            page_reference = int(re.sub(r"\s+", "", match.group("page")))
            normalized_title = self._normalize_for_matching(raw_title)
            if not normalized_title:
                continue

            entries.append(
                XHTMLContentsEntry(
                    title=raw_title,
                    normalized_title=normalized_title,
                    page_reference=page_reference,
                    section_group=current_group,
                )
            )

        return entries

    def _match_contents_entries(
        self,
        entries: list[XHTMLContentsEntry],
        pages: list[XHTMLPage],
    ) -> None:
        for entry in entries:
            matched_page = self._find_best_page_for_entry(entry, pages)
            if matched_page is not None:
                entry.matched_page_number = matched_page.page_number
                if matched_page.page_number == entry.page_reference:
                    entry.match_confidence = "exact_page"
                elif abs(matched_page.page_number - entry.page_reference) <= 1:
                    entry.match_confidence = "nearby_page"
                else:
                    entry.match_confidence = "text_only"

    def _find_best_page_for_entry(
        self,
        entry: XHTMLContentsEntry,
        pages: list[XHTMLPage],
    ) -> Optional[XHTMLPage]:
        normalized_title = entry.normalized_title
        if not normalized_title:
            return None

        candidate_windows: list[XHTMLPage] = []
        lower_bound = max(1, entry.page_reference - 2)
        upper_bound = min(len(pages), entry.page_reference + 2)
        candidate_windows.extend(pages[lower_bound - 1 : upper_bound])

        broader_lower = max(1, entry.page_reference - 6)
        broader_upper = min(len(pages), entry.page_reference + 6)
        for page in pages[broader_lower - 1 : broader_upper]:
            if page not in candidate_windows:
                candidate_windows.append(page)

        for page in candidate_windows:
            if normalized_title in page.normalized_full_text:
                return page

        for page in candidate_windows:
            if self._title_token_overlap(normalized_title, page.normalized_full_text):
                return page

        return None

    def _build_sections(
        self,
        entries: list[XHTMLContentsEntry],
        pages: list[XHTMLPage],
    ) -> list[XHTMLSection]:
        section_entries = [
            entry
            for entry in entries
            if entry.normalized_title != "contents"
        ]
        if not section_entries:
            return []

        sections: list[XHTMLSection] = []
        for index, entry in enumerate(section_entries):
            start_page = self._effective_section_start(entry, len(pages))
            if index + 1 < len(section_entries):
                next_start = self._effective_section_start(
                    section_entries[index + 1],
                    len(pages),
                )
                end_page = max(start_page, next_start - 1)
            else:
                end_page = len(pages)

            page_numbers = list(range(start_page, end_page + 1))
            sections.append(
                XHTMLSection(
                    title=entry.title,
                    normalized_title=entry.normalized_title,
                    start_page_number=start_page,
                    end_page_number=end_page,
                    source=entry.match_confidence,
                    page_numbers=page_numbers,
                )
            )

        return sections

    @staticmethod
    def _effective_section_start(entry: XHTMLContentsEntry, page_count: int) -> int:
        if entry.matched_page_number is None:
            return min(max(1, entry.page_reference), page_count)

        if abs(entry.matched_page_number - entry.page_reference) <= 2:
            return min(max(1, entry.page_reference), page_count)

        return min(max(1, entry.matched_page_number), page_count)

    @staticmethod
    def _looks_like_toc_entry(line: str) -> bool:
        compact = " ".join(line.split())
        return bool(re.search(r"\d(?:\s*\d)*$", compact))

    def _is_section_group_line(self, line: str) -> bool:
        normalized = self._normalize_for_matching(line)
        if normalized in {
            "strategicreport",
            "governance",
            "financialstatements",
            "additionalinformation",
        }:
            return True

        words = [word for word in line.split() if word]
        return len(words) <= 3 and not any(char.isdigit() for char in line)

    @staticmethod
    def _normalize_for_matching(text: str) -> str:
        lowered = text.lower().replace("’", "'").replace("–", "-")
        return re.sub(r"[^a-z0-9]+", "", lowered)

    @staticmethod
    def _title_token_overlap(normalized_title: str, normalized_page_text: str) -> bool:
        if not normalized_title or not normalized_page_text:
            return False

        if normalized_title in normalized_page_text:
            return True

        chunks = [
            normalized_title[index : index + 6]
            for index in range(0, max(1, len(normalized_title) - 5), 4)
        ]
        hits = sum(1 for chunk in chunks if chunk and chunk in normalized_page_text)
        return hits >= max(2, len(chunks) // 2)
