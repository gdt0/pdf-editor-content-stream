"""Unit tests for the scan-loop positioning fixes: quote operators ('/"),
TJ kern displacement of per-token x, Tz scaling, and the PDF default Tz."""
import pikepdf
import pytest
from pikepdf import ContentStreamInstruction as CSI

from fontcodec import SimpleFontCodec
from fontencodings import build_base_encoding
from textmodel import _scan_stream
from widths import FontWidths


def _op(name):
    return pikepdf.Operator(name)


def _codec():
    return {"F1": SimpleFontCodec(build_base_encoding("WinAnsiEncoding"), "F1")}


def _widths():
    # every glyph is a flat 500/1000 em -> advances are easy to predict
    return {"F1": FontWidths(lambda code: 500.0)}


def test_tj_kern_displaces_subsequent_tokens():
    """TJ kern numbers between strings must move the FOLLOWING tokens'
    x positions (previously only the post-op line advance saw them)."""
    arr = pikepdf.Array([pikepdf.String(b"AB"), -200, pikepdf.String(b"CD")])
    instrs = [
        CSI(["/F1", 10], _op("Tf")),
        CSI([100.0, 700.0], _op("Td")),
        CSI([arr], _op("TJ")),
    ]
    toks = _scan_stream(instrs, 0, _codec(), _widths())
    assert [t.text for t in toks] == ["AB", "CD"]
    assert toks[0].x0 == pytest.approx(100.0)
    assert toks[0].x1 == pytest.approx(110.0)      # 2 glyphs x 500 x 10/1000
    # kern -200/1000 em = +2pt at size 10 after AB's 10pt
    assert toks[1].x0 == pytest.approx(112.0)
    assert toks[1].x1 == pytest.approx(122.0)


def test_apostrophe_starts_one_line_below():
    """' = T* then show: the string must be positioned one text line
    below the previous one (previously it landed on the same baseline)."""
    instrs = [
        CSI(["/F1", 10], _op("Tf")),
        CSI([100.0, 700.0], _op("Td")),
        CSI([20.0], _op("TL")),
        CSI([pikepdf.String(b"first")], _op("Tj")),
        CSI([pikepdf.String(b"second")], _op("'")),
    ]
    toks = _scan_stream(instrs, 0, _codec(), _widths())
    assert [t.text for t in toks] == ["first", "second"]
    assert toks[0].y == pytest.approx(700.0)
    assert toks[1].y == pytest.approx(680.0)
    assert toks[1].anchor_op == "'"


def test_double_quote_sets_spacing_and_line():
    '" sets word/char spacing, then acts like \' — and its string lives in\n    operand 2, not operand 0.'
    instrs = [
        CSI(["/F1", 10], _op("Tf")),
        CSI([100.0, 700.0], _op("Td")),
        CSI([20.0], _op("TL")),
        CSI([pikepdf.String(b"first")], _op("Tj")),
        CSI([0.0, 25.0, pikepdf.String(b"quoted")], _op(chr(34))),
    ]
    toks = _scan_stream(instrs, 0, _codec(), _widths())
    assert [t.text for t in toks] == ["first", "quoted"]
    assert toks[1].y == pytest.approx(680.0)


def test_tz_scales_advances_and_default_is_100():
    """Tz must scale x advances (the PDF default is 100, previously 0 —
    which zeroed every measured width once advances went through Tz)."""
    instrs = [
        CSI([50.0], _op("Tz")),
        CSI(["/F1", 10], _op("Tf")),
        CSI([100.0, 700.0], _op("Td")),
        CSI([pikepdf.String(b"AB")], _op("Tj")),
        CSI([pikepdf.String(b"CD")], _op("Tj")),
    ]
    toks = _scan_stream(instrs, 0, _codec(), _widths())
    assert toks[0].x0 == pytest.approx(100.0)
    assert toks[0].x1 == pytest.approx(105.0)      # 10pt advance x Tz 50
    assert toks[1].x0 == pytest.approx(105.0)      # line advance also Tz-scaled


def test_default_tz_measures_full_width():
    """No Tz op at all -> full-width advances (regression for the tz=0
    default that made x1 == x0 and broke run chaining)."""
    instrs = [
        CSI(["/F1", 10], _op("Tf")),
        CSI([100.0, 700.0], _op("Td")),
        CSI([pikepdf.String(b"AB")], _op("Tj")),
    ]
    toks = _scan_stream(instrs, 0, _codec(), _widths())
    assert toks[0].x1 == pytest.approx(110.0)
