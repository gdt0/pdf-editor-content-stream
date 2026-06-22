"""Regression tests for matching/replacing text split across Tj/TJ operators."""
import pytest

from pdf_editor import edit_pdf
from conftest import build_pdf, decode_page_text, cid_hex

ALPHABET = "HelloWorldHiabcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ .,@"


def _tj(text):
    return f"<{cid_hex(text)}> Tj\n"


def test_replace_split_across_multiple_tj(tmp_path):
    """The visible word is split one character per Tj operator."""
    content = "BT\n/F1 12 Tf\n72 720 Td\n" + "".join(_tj(c) for c in "Hello") + "ET\n"
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "World"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "World"


def test_replace_split_with_horizontal_td_same_line(tmp_path):
    """Fragments separated by a purely horizontal Td stay one block."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("Hel")
        + "8 0 Td\n"
        + _tj("lo")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "World"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "World"


def test_replace_split_across_tj_and_tj_array(tmp_path):
    """Match spans a plain Tj followed by a kerned TJ array."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("He")
        + f"[<{cid_hex('l')}> 3 <{cid_hex('l')}> 2 <{cid_hex('o')}>] TJ\n"
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "World"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "World"


# ── Control: text contained in a single operator still works ──

def test_replace_within_single_tj(tmp_path):
    content = "BT\n/F1 12 Tf\n72 720 Td\n" + _tj("Hello") + "ET\n"
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "World"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "World"


def test_replace_within_single_tj_array(tmp_path):
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + f"[<{cid_hex('H')}> 5 <{cid_hex('e')}> 3 <{cid_hex('l')}>"
        + f" 2 <{cid_hex('l')}> 4 <{cid_hex('o')}>] TJ\n"
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "World"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "World"


def test_suffix_preserved_when_match_ends_mid_operator(tmp_path):
    """Replacement keeps the tail of the operator that holds the match end."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("He")
        + _tj("lloWorld")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "Hi"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "HiWorld"


# ── Control: no replacement should occur ──

def test_no_replacement_when_text_absent(tmp_path):
    content = "BT\n/F1 12 Tf\n72 720 Td\n" + _tj("Hello") + "ET\n"
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    with pytest.raises(ValueError):
        edit_pdf(str(src), str(out), {"Goodbye": "World"})


def test_no_false_match_across_line_break(tmp_path):
    """A vertical Td (new line) must end the block; 'Hello' must not match."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("Hel")
        + "0 -14 Td\n"
        + _tj("lo")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    with pytest.raises(ValueError):
        edit_pdf(str(src), str(out), {"Hello": "World"})


def test_no_false_match_across_font_change(tmp_path):
    """A font switch must end the block; 'Hello' must not match across it."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("Hel")
        + "/F2 12 Tf\n"
        + _tj("lo")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET, second_font_alphabet=ALPHABET)

    with pytest.raises(ValueError):
        edit_pdf(str(src), str(out), {"Hello": "World"})


def test_no_false_match_across_nontext_operator(tmp_path):
    """A non-text operator between fragments must end the block."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + _tj("Hel")
        + "1 0 0 rg\n"
        + _tj("lo")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    with pytest.raises(ValueError):
        edit_pdf(str(src), str(out), {"Hello": "World"})


def test_missing_glyph_skips_replacement(tmp_path):
    """Replacement requiring a glyph absent from the font is not applied."""
    content = "BT\n/F1 12 Tf\n72 720 Td\n" + "".join(_tj(c) for c in "Hello") + "ET\n"
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)  # alphabet has no '#'

    with pytest.raises(ValueError):
        edit_pdf(str(src), str(out), {"Hello": "Wor#d"})


def test_untouched_text_unchanged(tmp_path):
    """Only the matched text changes; surrounding text is preserved."""
    content = (
        "BT\n/F1 12 Tf\n72 720 Td\n"
        + "".join(_tj(c) for c in "abHellocd")
        + "ET\n"
    )
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    build_pdf(src, content, ALPHABET)

    result = edit_pdf(str(src), str(out), {"Hello": "Hi"})

    assert result["replacements_made"] == 1
    assert decode_page_text(out) == "abHicd"
