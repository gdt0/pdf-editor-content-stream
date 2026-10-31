import pikepdf
import pytest

from fontcodec import SimpleFontCodec, CMapFontCodec, build_codec
from cmap import parse_tounicode


def _cmap_codec():
    cmap = parse_tounicode(b"""
    1 begincodespacerange
    <0000> <FFFF>
    endcodespacerange
    4 beginbfchar
    <0027> <0053>
    <0028> <0041>
    <0029> <004E>
    <003A> <00660069>
    endbfchar
    """)
    return CMapFontCodec(cmap, "TestType0")


def test_type0_decode_roundtrip():
    codec = _cmap_codec()
    assert codec.decode(b"\x00\x27\x00\x28\x00\x29") == "SAN"
    enc, missing = codec.encode("SAN")
    assert enc == b"\x00\x27\x00\x28\x00\x29"
    assert missing == []


def test_type0_ligature_greedy_encode():
    codec = _cmap_codec()
    # "fi" should encode to the single ligature CID (longest match wins).
    enc, missing = codec.encode("fi")
    assert enc == b"\x00\x3a"
    assert missing == []


def test_type0_missing_glyphs_reported():
    codec = _cmap_codec()
    enc, missing = codec.encode("SAZ")
    assert missing == ["Z"]
    assert enc == b"\x00\x27\x00\x28"  # encodable prefix is not silently lost


def test_simple_font_codec():
    codec = SimpleFontCodec({0x41: "A", 0x42: "B", 0x2D: "-"}, "T")
    assert codec.decode(b"AB-") == "AB-"
    enc, missing = codec.encode("B-A")
    assert enc == b"B-A"
    assert missing == []


_KEEP_ALIVE = []  # pikepdf objects die with their owning Pdf — keep refs


def _font_obj(overrides):
    pdf = pikepdf.new()
    _KEEP_ALIVE.append(pdf)
    font = pikepdf.Dictionary(Type=pikepdf.Name.Font)
    for k, v in overrides.items():
        if k == "ToUnicode":
            font["/ToUnicode"] = pdf.make_stream(v.encode("latin-1"))
        elif k == "Differences":
            diffs = pikepdf.Array(
                [int(x) if isinstance(x, int) else pikepdf.Name("/" + x) for x in v]
            )
            enc = pikepdf.Dictionary(
                BaseEncoding=pikepdf.Name("/WinAnsiEncoding"),
                Differences=diffs,
            )
            font["/Encoding"] = enc
        else:
            if isinstance(v, str) and v.startswith("/"):
                v = pikepdf.Name(v)
            font[pikepdf.Name("/" + k)] = v
    return font


def test_build_type0_with_tounicode():
    cmap_src = """
    1 beginbfchar
    <0041> <0058>
    endbfchar
    """
    codec, reason = build_codec(
        _font_obj({"Subtype": "/Type0", "BaseFont": "/ABC+Test", "ToUnicode": cmap_src})
    )
    assert codec is not None and reason is None
    assert codec.decode(b"\x00\x41") == "X"


def test_build_type0_without_tounicode_refused():
    codec, reason = build_codec(
        _font_obj({"Subtype": "/Type0", "BaseFont": "/ABC+Test"})
    )
    assert codec is None
    assert "ToUnicode" in reason


def test_build_simple_winansi():
    codec, reason = build_codec(
        _font_obj({
            "Subtype": "/Type1",
            "BaseFont": "/Helvetica",
            "Encoding": "/WinAnsiEncoding",
        })
    )
    assert codec is not None and reason is None
    assert codec.decode(b"Hi!") == "Hi!"
    assert codec.decode(bytes([0x93])) == "\u201c"


def test_build_simple_with_differences():
    codec, reason = build_codec(
        _font_obj({
            "Subtype": "/Type1",
            "BaseFont": "/Helvetica",
            "Differences": [96, "quoteleft"],
        })
    )
    assert codec is not None and reason is None
    assert codec.decode(bytes([0x60])) == "\u2018"


def test_build_simple_no_encoding_refused():
    codec, reason = build_codec(
        _font_obj({"Subtype": "/TrueType", "BaseFont": "/Mystery"})
    )
    assert codec is None
    assert "no /ToUnicode and no /Encoding" in reason


def test_build_unsupported_encoding_refused():
    codec, reason = build_codec(
        _font_obj({
            "Subtype": "/Type1",
            "BaseFont": "/Weird",
            "Encoding": "/SuperSpecialEncoding",
        })
    )
    assert codec is None
    assert "unsupported /Encoding" in reason


def test_build_cidfont_direct_with_tounicode():
    # Some producers put CIDFontType2 directly in page resources.
    cmap_src = """
    1 beginbfchar
    <0041> <0059>
    endbfchar
    """
    codec, reason = build_codec(
        _font_obj({"Subtype": "/CIDFontType2", "ToUnicode": cmap_src})
    )
    assert codec is not None and reason is None
    assert codec.decode(b"\x00\x41") == "Y"
