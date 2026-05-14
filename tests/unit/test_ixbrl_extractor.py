import xml.etree.ElementTree as ET

import pytest

from research_platform.documents.ixbrl_extractor import IXBRLExtractor

IX_NS = "http://www.xbrl.org/2013/inlineXBRL"


@pytest.fixture
def extractor():
    return IXBRLExtractor()


def make_element(tag="span", text=None, attrib=None, children=None):
    elem = ET.Element(tag, attrib=attrib or {})
    if text is not None:
        elem.text = text
    for child in children or []:
        elem.append(child)
    return elem


def make_continuation(id_, text, continued_at=None):
    attrib = {"id": id_}
    if continued_at:
        attrib["continuedAt"] = continued_at
    return make_element(text=text, attrib=attrib)


# ---------------------------------------------------------------------------
# _text_of
# ---------------------------------------------------------------------------


class TestTextOf:
    def test_none_returns_empty(self, extractor):
        assert extractor._text_of(None) == ""

    def test_plain_integer(self, extractor):
        assert extractor._text_of(make_element(text="1234")) == "1234"

    def test_comma_separated_number(self, extractor):
        assert extractor._text_of(make_element(text="1,234")) == "1,234"

    def test_number_with_spaces_is_stripped(self, extractor):
        assert extractor._text_of(make_element(text="1 234")) == "1234"

    def test_parenthesised_number(self, extractor):
        assert extractor._text_of(make_element(text="(1,234)")) == "(1,234)"

    def test_number_with_percent(self, extractor):
        assert extractor._text_of(make_element(text="12.5%")) == "12.5%"

    def test_number_with_x_suffix(self, extractor):
        assert extractor._text_of(make_element(text="3.2x")) == "3.2x"

    def test_negative_number(self, extractor):
        assert extractor._text_of(make_element(text="-1,234")) == "-1,234"

    def test_narrative_collapses_whitespace(self, extractor):
        assert extractor._text_of(make_element(text="  Hello   world  ")) == "Hello world"

    def test_narrative_multiword(self, extractor):
        assert extractor._text_of(make_element(text="Going concern risk")) == "Going concern risk"

    def test_nested_elements_concatenated(self, extractor):
        parent = ET.Element("span")
        parent.text = "Hello "
        child = ET.SubElement(parent, "em")
        child.text = "world"
        assert extractor._text_of(parent) == "Hello world"

    def test_empty_element_returns_empty(self, extractor):
        assert extractor._text_of(make_element()) == ""


# ---------------------------------------------------------------------------
# _apply_scale_sign
# ---------------------------------------------------------------------------


class TestApplyScaleSign:
    def test_none_value_returns_none(self, extractor):
        assert extractor._apply_scale_sign(None, None, None) is None

    def test_empty_string_returns_none(self, extractor):
        assert extractor._apply_scale_sign("", None, None) is None

    def test_plain_integer(self, extractor):
        assert extractor._apply_scale_sign("1234", None, None) == 1234.0

    def test_comma_separated(self, extractor):
        assert extractor._apply_scale_sign("1,234", None, None) == 1234.0

    def test_scale_thousands(self, extractor):
        assert extractor._apply_scale_sign("1234", "3", None) == 1_234_000.0

    def test_scale_millions(self, extractor):
        assert extractor._apply_scale_sign("1234", "6", None) == 1_234_000_000.0

    def test_explicit_negative_sign(self, extractor):
        assert extractor._apply_scale_sign("1234", None, "-") == -1234.0

    def test_parenthesis_means_negative(self, extractor):
        assert extractor._apply_scale_sign("(1234)", None, None) == -1234.0

    def test_parenthesis_with_commas(self, extractor):
        assert extractor._apply_scale_sign("(1,234)", None, None) == -1234.0

    def test_scale_and_sign_combined(self, extractor):
        assert extractor._apply_scale_sign("500", "6", "-") == -500_000_000.0

    def test_percent_suffix_stripped(self, extractor):
        assert extractor._apply_scale_sign("12.5%", None, None) == 12.5

    def test_x_suffix_stripped(self, extractor):
        assert extractor._apply_scale_sign("3.2x", None, None) == 3.2

    def test_unparseable_returns_none(self, extractor):
        assert extractor._apply_scale_sign("n/a", None, None) is None

    def test_bad_scale_value_ignored(self, extractor):
        assert extractor._apply_scale_sign("1000", "bad", None) == 1000.0

    def test_decimal_value(self, extractor):
        assert extractor._apply_scale_sign("3.14", None, None) == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# _follow_continuation
# ---------------------------------------------------------------------------


class TestFollowContinuation:
    def test_single_part_no_continuation(self, extractor):
        start = make_element(text="Part one.")
        result = extractor._follow_continuation(start, continuations={})
        assert result == "Part one."

    def test_two_part_chain(self, extractor):
        cont1 = make_continuation("c1", "Part two.")
        start = make_element(text="Part one.", attrib={"continuedAt": "c1"})
        result = extractor._follow_continuation(start, continuations={"c1": cont1})
        assert result == "Part one.\nPart two."

    def test_three_part_chain(self, extractor):
        cont2 = make_continuation("c2", "Part three.")
        cont1 = make_continuation("c1", "Part two.", continued_at="c2")
        start = make_element(text="Part one.", attrib={"continuedAt": "c1"})
        result = extractor._follow_continuation(
            start, continuations={"c1": cont1, "c2": cont2}
        )
        assert result == "Part one.\nPart two.\nPart three."

    def test_missing_continuation_terminates_cleanly(self, extractor):
        start = make_element(text="Part one.", attrib={"continuedAt": "missing"})
        result = extractor._follow_continuation(start, continuations={})
        assert result == "Part one."

    def test_cycle_guard_prevents_infinite_loop(self, extractor):
        cont1 = make_continuation("c1", "Part two.", continued_at="c1")
        start = make_element(text="Part one.", attrib={"continuedAt": "c1"})
        result = extractor._follow_continuation(start, continuations={"c1": cont1})
        assert result == "Part one.\nPart two."

    def test_empty_pieces_excluded(self, extractor):
        cont1 = make_continuation("c1", "")
        cont2 = make_continuation("c2", "Final.", continued_at=None)
        cont1.attrib["continuedAt"] = "c2"
        start = make_element(text="Start.", attrib={"continuedAt": "c1"})
        result = extractor._follow_continuation(
            start, continuations={"c1": cont1, "c2": cont2}
        )
        assert result == "Start.\nFinal."
