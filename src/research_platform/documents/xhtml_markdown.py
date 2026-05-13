from __future__ import annotations

from pathlib import Path

from research_platform.documents.xhtml_parser import XHTMLReportParseResult


class XHTMLMarkdownRenderer:
    def render(self, parsed: XHTMLReportParseResult) -> str:
        lines: list[str] = []
        source = Path(parsed.file_path)

        lines.append(f"# {self._report_title(parsed)}")
        lines.append("")
        lines.append(f"- Source file: `{source}`")
        lines.append(f"- Page count: {parsed.page_count}")
        if parsed.contents_page_number is not None:
            lines.append(f"- Contents page: {parsed.contents_page_number}")
        if parsed.notes:
            lines.append("- Notes:")
            for note in parsed.notes:
                lines.append(f"  - {note}")
        lines.append("")

        if parsed.contents_entries:
            lines.append("## Contents")
            lines.append("")
            for entry in parsed.contents_entries:
                target = entry.matched_page_number or entry.page_reference
                lines.append(
                    f"- {self._clean_line(entry.title)} "
                    f"(TOC {entry.page_reference}, text {target}, {entry.match_confidence})"
                )
            lines.append("")

        if parsed.sections:
            lines.append("## Sections")
            lines.append("")
            for section in parsed.sections:
                lines.extend(self._render_section(parsed, section))
        else:
            lines.append("## Pages")
            lines.append("")
            for page in parsed.pages:
                lines.extend(self._render_page(page.page_number, page.lines))

        return "\n".join(lines).rstrip() + "\n"

    def _render_section(self, parsed: XHTMLReportParseResult, section) -> list[str]:
        lines: list[str] = []
        lines.append(f"## {self._clean_line(section.title)}")
        lines.append("")
        lines.append(
            f"_Pages {section.start_page_number}-{section.end_page_number} "
            f"({section.source})_"
        )
        lines.append("")

        for page_number in section.page_numbers:
            page = parsed.pages[page_number - 1]
            lines.extend(self._render_page(page.page_number, page.lines))

        return lines

    def _render_page(self, page_number: int, page_lines: list[str]) -> list[str]:
        lines: list[str] = []
        lines.append(f"### Page {page_number}")
        lines.append("")
        for line in page_lines:
            cleaned = self._clean_line(line)
            if cleaned:
                lines.append(cleaned)
        lines.append("")
        return lines

    def _report_title(self, parsed: XHTMLReportParseResult) -> str:
        for page in parsed.pages[:10]:
            for line in page.lines:
                cleaned = self._clean_line(line)
                lowered = cleaned.lower()
                if "annual report" in lowered and "financial" in lowered:
                    return cleaned
        return Path(parsed.file_path).stem

    def _clean_line(self, text: str) -> str:
        tokens = text.split()
        if not tokens:
            return ""

        merged: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if len(token) == 1 and token.isalpha():
                letters = [token]
                look_ahead = index + 1
                while look_ahead < len(tokens):
                    next_token = tokens[look_ahead]
                    if len(next_token) == 1 and next_token.isalpha():
                        letters.append(next_token)
                        look_ahead += 1
                        continue
                    if len(next_token) <= 4 and next_token.isalpha():
                        letters.append(next_token)
                        look_ahead += 1
                    break

                if len(letters) >= 2:
                    merged.append("".join(letters))
                    index = look_ahead
                    continue

            merged.append(token)
            index += 1

        cleaned = " ".join(merged)
        cleaned = cleaned.replace(" .", ".").replace(" ,", ",")
        cleaned = cleaned.replace(" ’ ", "’").replace(" ' ", "'")
        cleaned = cleaned.replace(" :", ":").replace(" ;", ";")
        cleaned = cleaned.replace("( ", "(").replace(" )", ")")
        return cleaned.strip()
