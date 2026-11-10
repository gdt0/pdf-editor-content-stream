import pytest

from cmap import parse_tounicode


C = lambda s: s.encode("latin-1") if isinstance(s, str) else s


def test_bfchar_basic():
    cmap = parse_tounicode(C("""
    /CIDInit /ProcSet findresource begin
    2 beginbfchar
    <0027> <0044>
    <0055> <0072>
    endbfchar
    end
    """))
    assert cmap.decode(C(b"\x00\x27\x00\x55")) == "Dr"


def test_bfchar_multi_codepoint_ligature():
    # One CID mapping to two codepoints (fi ligature decomposition).
    cmap = parse_tounicode(C("""
    1 beginbfchar
    <003A> <00660069>
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x3A")) == "fi"
    rev = cmap.reverse_map()
    assert rev["fi"] == b"\x00\x3a"


def test_bfchar_surrogate_pair():
    # U+1F600 written as UTF-16BE surrogate pair D83D DE00.
    cmap = parse_tounicode(C("""
    1 beginbfchar
    <0001> <D83DDE00>
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x01")) == "\U0001F600"


def test_bfrange_base_form():
    cmap = parse_tounicode(C("""
    1 beginbfrange
    <0000> <0040> <0041>
    endbfrange
    """))
    # CID 0x0000 -> U+0041 'A', CID 0x0002 -> 'C'
    assert cmap.decode(C(b"\x00\x00\x00\x02")) == "AC"


def test_bfrange_array_destination():
    cmap = parse_tounicode(C("""
    1 beginbfrange
    <0010> <0013> [<0041> <0042> <0043> <0044>]
    endbfrange
    """))
    assert cmap.decode(C(b"\x00\x10\x00\x11\x00\x12\x00\x13")) == "ABCD"
    # Beyond provided destinations: unmapped
    assert cmap.decode(C(b"\x00\x14")) == ""


def test_bfrange_array_partial_destination():
    cmap = parse_tounicode(C("""
    1 beginbfrange
    <0010> <0014> [<0041> <0042>]
    endbfrange
    """))
    assert cmap.decode(C(b"\x00\x10\x00\x11")) == "AB"
    assert cmap.decode(C(b"\x00\x12")) == ""


def test_codespace_1_byte():
    cmap = parse_tounicode(C("""
    1 begincodespacerange
    <00> <FF>
    endcodespacerange
    1 beginbfchar
    <41> <0061>
    endbfchar
    """))
    assert cmap.decode(C(b"\x41\x42")) == "a"  # 0x42 unknown -> ''
    assert cmap.code_lengths == [1]


def test_codespace_variable_mixed():
    cmap = parse_tounicode(C("""
    2 begincodespacerange
    <00> <7F>
    <8140> <9FFC>
    endcodespacerange
    2 beginbfchar
    <41> <0048>
    <8140> <0065>
    endbfchar
    """))
    assert cmap.decode(C(b"\x41\x81\x40")) == "He"


def test_comments_and_whitespace_ignored():
    cmap = parse_tounicode(C("""
    % this is a comment
    1 beginbfchar   % trailing comment
    <0001> <0058>   % X
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x01")) == "X"


def test_unknown_codes_decode_empty():
    cmap = parse_tounicode(C("""
    1 beginbfchar
    <0001> <0058>
    endbfchar
    """))
    assert cmap.decode(C(b"\x09\x09\x00\x01")) == "X"


def test_odd_hex_padded_per_pdf_rule():
    # <004> has odd digit count; PDF pads the final digit -> <0040>.
    cmap = parse_tounicode(C("""
    1 beginbfchar
    <0001> <004>
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x01")) == "@"


def test_malformed_destination_recorded():
    # Lone high surrogate D800 is illegal UTF-16BE -> recorded, not crash.
    cmap = parse_tounicode(C("""
    2 beginbfchar
    <0001> <D800>
    <0002> <0042>
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x02")) == "B"
    assert cmap.malformed, "unpaired surrogate destination must be recorded"


def test_no_codespace_defaults_2byte():
    cmap = parse_tounicode(C("""
    1 beginbfchar
    <0041> <0048>
    endbfchar
    """))
    assert cmap.decode(C(b"\x00\x41")) == "H"


def test_reverse_map_prefers_first():
    cmap = parse_tounicode(C("""
    2 beginbfchar
    <0001> <0058>
    <0002> <0058>
    endbfchar
    """))
    assert cmap.reverse_map()["X"] == b"\x00\x01"
