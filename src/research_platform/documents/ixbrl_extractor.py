from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
XML_NS = "http://www.w3.org/XML/1998/namespace"


class IXBRLExtractionError(RuntimeError):
    """Raised when an iXBRL filing cannot be extracted."""


class IXBRLContext(BaseModel):
    id: str
    entity: Optional[str] = None
    period: dict[str, str] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)


class IXBRLFact(BaseModel):
    fact_type: str
    id: Optional[str] = None
    concept: Optional[str] = None
    context_ref: Optional[str] = None
    context: Optional[IXBRLContext] = None
    raw_text: Optional[str] = None
    value: Optional[float] = None
    text: Optional[str] = None
    unit: Optional[str] = None
    decimals: Optional[str] = None
    scale: Optional[str] = None
    sign: Optional[str] = None
    lang: Optional[str] = None
    escape: Optional[bool] = None
    continued_at: Optional[str] = None


class IXBRLExtractionResult(BaseModel):
    file_path: str
    context_count: int
    numeric_fact_count: int
    narrative_fact_count: int
    facts: list[IXBRLFact] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps(fact.model_dump(mode="json"), ensure_ascii=False)
            for fact in self.facts
        ) + ("\n" if self.facts else "")


class IXBRLExtractor:
    def extract(self, file_path: Path) -> IXBRLExtractionResult:
        if not file_path.exists():
            raise IXBRLExtractionError(f"XHTML report file not found: {file_path}")

        try:
            root = ET.parse(file_path).getroot()
        except ET.ParseError as exc:
            raise IXBRLExtractionError(f"Unable to parse XHTML/iXBRL report: {exc}") from exc

        contexts = self._parse_contexts(root)
        continuations = self._parse_continuations(root)

        facts: list[IXBRLFact] = []
        numeric_count = 0
        narrative_count = 0

        for element in root.iter(f"{{{IX_NS}}}nonFraction"):
            fact = self._extract_numeric_fact(element=element, contexts=contexts)
            facts.append(fact)
            numeric_count += 1

        for element in root.iter(f"{{{IX_NS}}}nonNumeric"):
            fact = self._extract_narrative_fact(
                element=element,
                contexts=contexts,
                continuations=continuations,
            )
            facts.append(fact)
            narrative_count += 1

        notes = [
            "Numeric facts are extracted from ix:nonFraction and normalized using scale/sign where possible.",
            "Narrative facts are stitched across ix:continuation chains when continuedAt is present.",
        ]

        return IXBRLExtractionResult(
            file_path=str(file_path),
            context_count=len(contexts),
            numeric_fact_count=numeric_count,
            narrative_fact_count=narrative_count,
            facts=facts,
            notes=notes,
        )

    def _parse_contexts(self, root: ET.Element) -> dict[str, IXBRLContext]:
        contexts: dict[str, IXBRLContext] = {}

        for element in root.iter(f"{{{XBRLI_NS}}}context"):
            context_id = element.get("id")
            if not context_id:
                continue

            identifier = element.find(f"{{{XBRLI_NS}}}entity/{{{XBRLI_NS}}}identifier")
            period_element = element.find(f"{{{XBRLI_NS}}}period")
            period: dict[str, str] = {}
            if period_element is not None:
                for child in period_element:
                    tag_name = self._local_name(child.tag)
                    period[tag_name] = (child.text or "").strip()

            dimensions: dict[str, str] = {}
            segment = element.find(f"{{{XBRLI_NS}}}entity/{{{XBRLI_NS}}}segment")
            if segment is None:
                segment = element.find(f"{{{XBRLI_NS}}}scenario")
            if segment is not None:
                for member in segment.iter(f"{{{XBRLDI_NS}}}explicitMember"):
                    dimension_name = member.get("dimension")
                    member_value = (member.text or "").strip()
                    if dimension_name and member_value:
                        dimensions[dimension_name] = member_value

            contexts[context_id] = IXBRLContext(
                id=context_id,
                entity=(identifier.text or "").strip() if identifier is not None else None,
                period=period,
                dimensions=dimensions,
            )

        return contexts

    def _parse_continuations(self, root: ET.Element) -> dict[str, ET.Element]:
        continuations: dict[str, ET.Element] = {}
        for element in root.iter(f"{{{IX_NS}}}continuation"):
            continuation_id = element.get("id")
            if continuation_id:
                continuations[continuation_id] = element
        return continuations

    def _extract_numeric_fact(
        self,
        element: ET.Element,
        contexts: dict[str, IXBRLContext],
    ) -> IXBRLFact:
        raw_text = self._text_of(element)
        return IXBRLFact(
            fact_type="numeric",
            id=element.get("id"),
            concept=element.get("name"),
            context_ref=element.get("contextRef"),
            context=contexts.get(element.get("contextRef", "")),
            raw_text=raw_text,
            value=self._apply_scale_sign(
                value_str=raw_text,
                scale=element.get("scale"),
                sign=element.get("sign"),
            ),
            unit=element.get("unitRef"),
            decimals=element.get("decimals"),
            scale=element.get("scale"),
            sign=element.get("sign"),
        )

    def _extract_narrative_fact(
        self,
        element: ET.Element,
        contexts: dict[str, IXBRLContext],
        continuations: dict[str, ET.Element],
    ) -> IXBRLFact:
        continued_at = element.get("continuedAt")
        text = (
            self._follow_continuation(element, continuations)
            if continued_at
            else self._text_of(element)
        )

        escape = element.get("escape") == "true"
        if escape:
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

        return IXBRLFact(
            fact_type="narrative",
            id=element.get("id"),
            concept=element.get("name"),
            context_ref=element.get("contextRef"),
            context=contexts.get(element.get("contextRef", "")),
            text=text,
            lang=element.get(f"{{{XML_NS}}}lang"),
            escape=escape,
            continued_at=continued_at,
        )

    def _follow_continuation(
        self,
        start_element: ET.Element,
        continuations: dict[str, ET.Element],
    ) -> str:
        pieces = [self._text_of(start_element)]
        continuation_id = start_element.get("continuedAt")
        seen: set[str] = set()

        while continuation_id and continuation_id not in seen:
            seen.add(continuation_id)
            continuation = continuations.get(continuation_id)
            if continuation is None:
                break
            pieces.append(self._text_of(continuation))
            continuation_id = continuation.get("continuedAt")

        return "\n".join(piece for piece in pieces if piece)

    def _text_of(self, element: Optional[ET.Element]) -> str:
        if element is None:
            return ""

        raw = "".join(text for text in element.itertext())
        stripped_numeric = re.sub(r"\s+", "", raw)
        if re.fullmatch(r"[-(]?[\d.,]+(?:%|x)?\)?", stripped_numeric):
            return stripped_numeric
        return re.sub(r"\s+", " ", raw).strip()

    def _apply_scale_sign(
        self,
        value_str: Optional[str],
        scale: Optional[str],
        sign: Optional[str],
    ) -> Optional[float]:
        if not value_str:
            return None

        normalized = value_str.replace(",", "").replace(" ", "")
        if normalized.startswith("(") and normalized.endswith(")"):
            normalized = "-" + normalized[1:-1]
        normalized = normalized.removesuffix("%").removesuffix("x")

        try:
            value = float(normalized)
        except ValueError:
            return None

        if scale:
            try:
                value *= 10 ** int(scale)
            except ValueError:
                pass

        if sign == "-":
            value = -value

        return value

    @staticmethod
    def _local_name(tag: str) -> str:
        if tag.startswith("{") and "}" in tag:
            return tag.split("}", 1)[1]
        return tag
