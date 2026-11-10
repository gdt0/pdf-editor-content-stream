import pytest

from fontencodings import (
    build_base_encoding,
    build_simple_encoding,
    parse_differences,
)


def test_winansi_ascii():
    t = build_base_encoding("WinAnsiEncoding")
    assert t[0x41] == "A"
    assert t[0x7A] == "z"
    assert t[0x2D] == "-"


def test_winansi_high():
    t = build_base_encoding("WinAnsiEncoding")
    assert t[0x80] == "\u20ac"          # Euro
    assert t[0x93] == "\u201c"          # left double quote
    assert t[0x96] == "\u2013"          # en dash
    assert t[0x92] == "\u2019"          # right single quote
    assert t[0x85] == "\u2026"          # ellipsis
    assert t[0xA0] == "\u00a0"          # nbsp (not plain space)
    assert t[0xAD] == "\u2010"          # WinAnsi hyphen (not cp1252 soft hyphen)
    assert t[0xE9] == "\u00e9"          # é


def test_winansi_undefined_slots():
    t = build_base_encoding("WinAnsiEncoding")
    for code in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
        assert code not in t


def test_standard_quotes_swapped():
    t = build_base_encoding("StandardEncoding")
    assert t[0x27] == "\u2019"          # quoteright, not apostrophe
    assert t[0x60] == "\u2018"          # quoteleft, not grave
    assert t[0x80] == "\u2260"          # notequal
    assert t[0xE9] == "\u00e9"


def test_macroman_matches_codec():
    # Derived from Python's mac_roman codec (classic Mac OS variant). The
    # PDF spec's MacRomanEncoding deviates in a few high slots; ASCII and
    # accented Latin (the slots that matter for editing) are identical.
    t = build_base_encoding("MacRomanEncoding")
    expected = bytes([0xD6, 0xD8, 0x8C, 0xBA]).decode("mac_roman")
    assert [t[0xD6], t[0xD8], t[0x8C], t[0xBA]] == list(expected)
    assert t[0x41] == "A"


def test_parse_differences_increment():
    diffs = parse_differences([24, "/a", "/b", 30, "/z"])
    assert diffs == {24: "a", 25: "b", 30: "z"}


def test_differences_override():
    # Code 0x60 remapped from grave to quoteleft (common real-world case).
    code2uni, uni2code = build_simple_encoding("WinAnsiEncoding", [96, "/quoteleft"])
    assert code2uni[0x60] == "\u2018"
    assert uni2code["\u2018"] == 0x60
    # quoteright still at its WinAnsi slot
    assert uni2code["\u2019"] == 0x92


def test_differences_uni_name():
    code2uni, _ = build_simple_encoding("WinAnsiEncoding", [65, "/uni2018"])
    assert code2uni[65] == "\u2018"


def test_unknown_glyph_name_ignored():
    code2uni, _ = build_simple_encoding("WinAnsiEncoding", [65, "/a.bogus"])
    assert code2uni[65] == "A"  # unknown glyph name: base 'A' is preserved


def test_reverse_first_code_wins():
    _, uni2code = build_simple_encoding("WinAnsiEncoding")
    assert uni2code["A"] == 0x41


def test_winansi_and_standard_latin1_mid_block():
    """0xA1-0xBF must decode in both WinAnsi and Standard (previously the
    whole block was missing: £ © ° « ¿ decoded to '' and edits touching
    them were falsely refused as missing glyphs)."""
    expected = {
        0xA1: "¡", 0xA3: "£", 0xA9: "©", 0xAB: "«", 0xB0: "°",
        0xB5: "µ", 0xBF: "¿",
    }
    for name in ("WinAnsiEncoding", "StandardEncoding"):
        table = build_base_encoding(name)
        for code, ch in expected.items():
            assert table[code] == ch, "%s 0x%02X" % (name, code)
    # 0xAD stays the PDF hyphen deviation in both
    for name in ("WinAnsiEncoding", "StandardEncoding"):
        assert build_base_encoding(name)[0xAD] == "\u2010"
