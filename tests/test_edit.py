"""Integration tests: edit_pdf against the golden corpus fixtures."""
import fitz
import pytest

from pdf_editor import edit_pdf


def _text(path):
    doc = fitz.open(path)
    try:
        return doc[0].get_text("text")
    finally:
        doc.close()


def test_type0_name_replace(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F1_basic_tounicode.pdf"), str(out),
        {"SANKET DAWAR": "SACHIN KUMAR"},
    )
    text = _text(out)
    assert "SACHIN KUMAR" in text
    assert "SANKET" not in text
    assert result["applied"] == {"SANKET DAWAR": 1}
    assert result["not_found"] == []
    assert result["missing_glyphs"] == []
    assert result["fonts_skipped"] == {}
    assert result["page_errors"] == []


def test_repeat_occurrences_all_replaced(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F1_basic_tounicode.pdf"), str(out), {"alpha": "beta"}
    )
    text = _text(out)
    assert "alpha" not in text
    assert "gamma" in text                      # untouched words survive
    assert result["replacements_made"] == 3      # three TJ matches


def test_deletion_to_empty(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F1_basic_tounicode.pdf"), str(out), {"URGENT: ": ""}
    )
    text = _text(out)
    assert "URGENT" not in text
    assert "payroll" in text                    # tail of the line survives
    assert result["replacements_made"] == 1


def test_perchar_tj_array(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F2_perchar_tj.pdf"), str(out),
        {"SANKET DAWAR": "SACHIN KUMAR"},
    )
    text = _text(out)
    assert "SACHIN KUMAR" in text
    assert result["replacements_made"] == 1


def test_cross_operator_replaced(corpus_file, tmp_path):
    """Phase 3: the token model matches across operator boundaries."""
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F3_cross_operator.pdf"), str(out),
        {"SANKET DAWAR": "SACHIN KUMAR"},
    )
    text = _text(out)
    assert "SACHIN KUMAR" in text
    assert result["replacements_made"] == 1


def test_positioned_runs_shift(corpus_file, tmp_path):
    """Widening run1 of a positioned-run line shifts run2's anchor by the
    measured delta; rendering stays pixel-exact outside the line."""
    import fitz

    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F8_positioned_runs.pdf"), str(out),
        {"world": "wonderful"}, verify=True,
    )
    assert result["verification"]["ok"]
    assert result["fit_actions"][0]["shifted_runs"] == 1

    def tail_x(path):
        doc = fitz.open(path)
        words = doc[0].get_text("words")
        doc.close()
        return min(w[0] for w in words if w[4] == "tail")

    assert tail_x(str(out)) > tail_x(corpus_file("F8_positioned_runs.pdf"))


def test_reflow_moves_words_to_continuation(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F9_reflow.pdf"), str(out),
        {"alpha": "alpha-much-longer"}, verify=True,
    )
    assert result["reflowed"] is True
    assert result["verification"]["ok"]
    text = _text(out)
    assert "alpha-much-longer" in text
    assert "iota end second line here" in text


def test_fit_refuse_policy_rejects_width_change(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError) as exc:
        edit_pdf(
            corpus_file("F8_positioned_runs.pdf"), str(out),
            {"world": "wonderful"}, fit="refuse",
        )
    assert "DOES_NOT_FIT" in str(exc.value)


def test_fit_shift_policy_rejects_overflow(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError) as exc:
        edit_pdf(
            corpus_file("F9_reflow.pdf"), str(out),
            {"alpha": "alpha-much-longer"}, fit="shift",
        )
    assert "DOES_NOT_FIT" in str(exc.value)


def test_multifont_both_fonts(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F4_multifont.pdf"), str(out),
        {"REPORT": "DOSSIER", "Draft": "FINAL"},
    )
    text = _text(out)
    assert "DOSSIER" in text
    assert "FINAL" in text
    assert "2024" in text                       # suffix in same element survives
    assert result["replacements_made"] == 2


def test_differences_override(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F5_differences.pdf"), str(out),
        {"‘": "“"},
    )
    text = _text(out)
    assert "“" in text
    assert result["replacements_made"] == 1


def test_simple_font_with_tounicode(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F6_simple_tounicode.pdf"), str(out),
        {"pending": "done"},
    )
    text = _text(out)
    assert "done" in text
    assert "pending" not in text
    assert result["replacements_made"] == 2


def test_missing_glyphs_refused_not_corrupted(corpus_file, tmp_path):
    """THE regression test for silent glyph loss: the refused replacement
    must leave the original text intact while a sibling replacement applies."""
    out = tmp_path / "out.pdf"
    result = edit_pdf(
        corpus_file("F7_limited_tounicode.pdf"), str(out),
        {"SANKET": "SACHIN", "T": "TT"},
    )
    text = _text(out)
    assert "SACHIN" not in text                 # refused — never corrupted
    assert "SANKET" in text                     # original text intact
    assert "TT" in text                         # sibling replacement applied
    missing = result["missing_glyphs"]
    assert len(missing) == 1
    assert set(missing[0]["missing_chars"]) == {"C", "H", "I"}
    assert result["not_found"] == []            # found, just not applicable


def test_missing_glyphs_only_is_valueerror(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError) as exc:
        edit_pdf(
            corpus_file("F7_limited_tounicode.pdf"), str(out),
            {"SANKET": "SACHIN"},
        )
    msg = str(exc.value)
    assert "missing" in msg
    for ch in "CHI":
        assert ch in msg


def test_not_found_only_is_valueerror(corpus_file, tmp_path):
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError) as exc:
        edit_pdf(
            corpus_file("F1_basic_tounicode.pdf"), str(out),
            {"ZEBRA": "HORSE"},
        )
    assert "ZEBRA" in str(exc.value)


def test_write_back_survives_roundtrip(corpus_file, tmp_path):
    """The Tj scalar-operand bug: mutations must survive parse->unparse."""
    out = tmp_path / "out.pdf"
    edit_pdf(corpus_file("F6_simple_tounicode.pdf"), str(out), {"pending": "done"})
    assert "done" in _text(out)
