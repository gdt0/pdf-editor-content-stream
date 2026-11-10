"""Regression tests for insertblock stream routing (P0: no-threshold case)
and the P1 fixes (vector ops move, page-1 stream selection)."""
import pikepdf
import pytest

from insertblock import _route_streams, insert_experience_block
from pdf_editor import _collect_streams
from pikepdf import parse_content_stream
from textmodel import find_in_lines, line_by_y

ANCHOR = "SOFTWARE ENGINEER"
NAME = "SYNTH RESUME"
RULE_END_X = 300.0   # the fixture's vector rule: "33 624 m 300 624 l S"
RULE_Y = 624.0


def _main_stream(path):
    pdf = pikepdf.open(path)
    page_errors, fonts_skipped = [], {}
    streams = _collect_streams(pdf, page_errors, fonts_skipped)
    # the same explicit selection insertblock now performs
    mains = [s for s in streams if s.page_no == 1 and s.kind == "page"]
    assert mains, "no editable page-1 stream found"
    return pdf, mains[0]


def _line_y(sc, query):
    ms = find_in_lines(sc.tokens, sc.lines, query)
    assert ms, "line not found: %r" % query
    return sc.tokens[ms[0].start_tok].y


def _cut_y_for(sc, anchor_y):
    """Same cut placement as insert_experience_block: between the anchor
    role line and the nearest line above it."""
    ys_above = [ln.y for ln in sc.lines
                if anchor_y + 1.0 < ln.y < anchor_y + 60.0]
    cut_y = anchor_y + 7.0
    if ys_above:
        cut_y = (anchor_y + min(ys_above)) / 2.0
    return cut_y


def _rule_y_operand(instrs):
    """Raw y operand of the fixture's vector rule ('l' op ending at x=300)."""
    for ins in instrs:
        if str(ins.operator) == "l" and len(ins.operands) >= 2 \
                and float(ins.operands[0]) == RULE_END_X:
            return float(ins.operands[1])
    return None


def test_no_threshold_keeps_everything_on_page1(synth_resume):
    """P0 regression: move_threshold=None (no unit crosses the bottom
    margin) must route NOTHING to page 2 — everything below the cut stays
    on page 1 shifted by -H. The old code passed anchor_y as the threshold,
    dumping the whole lower half onto an unshifted mid-page page 2, and the
    prefix-only page2 stream minted a blank page on top of that."""
    # pristine reference values, taken before any mutation
    pdf0, sc0 = _main_stream(synth_resume)
    try:
        anchor_y = _line_y(sc0, ANCHOR)
        name_y = _line_y(sc0, NAME)
    finally:
        pdf0.close()
    cut_y = _cut_y_for(sc0, anchor_y)
    H = 54.0

    pdf, sc = _main_stream(synth_resume)
    try:
        page1, page2, _ctm = _route_streams(
            sc, cut_y, -H, float("-inf"), 0.0)

        # nothing routed -> no page-2 stream at all (no prefix-only page)
        assert page2 == []
        # every original instruction survives on page 1 (q/Q rebalancing
        # may append padding, never drop content)
        assert len(page1) >= len(sc.instructions)

        # below-cut content physically shifted down by H, above-cut intact
        sc.rescan()
        assert _line_y(sc, ANCHOR) == pytest.approx(anchor_y - H, abs=1.0)
        assert _line_y(sc, NAME) == pytest.approx(name_y, abs=0.5)

        # the vector rule moved with its text (raw operand edited too)
        assert _rule_y_operand(sc.instructions) == pytest.approx(
            RULE_Y - H, abs=0.5)
    finally:
        pdf.close()


def test_explicit_threshold_splits_to_page2(synth_resume):
    """Sanity on the crossing regime: an explicit threshold above the lower
    content must produce a page-2 stream carrying that content (plus the
    state prefix), not an empty one."""
    pdf, sc = _main_stream(synth_resume)
    try:
        anchor_y = _line_y(sc, ANCHOR)
        cut_y = _cut_y_for(sc, anchor_y)
        # force the split: everything well below the anchor's section
        threshold = anchor_y - 120.0
        page1, page2, _ctm = _route_streams(
            sc, cut_y, -54.0, threshold, 200.0)
        assert page2, "expected routed content on page 2"
        assert len(page1) + len(page2) >= len(sc.instructions)
    finally:
        pdf.close()


def test_insert_block_no_overflow_stays_on_one_page(synth_resume, tmp_path):
    """End-to-end regression of the fixed no-crossing path: a small block
    above the first role shifts the lower sections down without any of
    them crossing the bottom margin -> same page count, no page minted,
    and the full verification harness (incl. the insertion checks with
    new_page=None) must pass."""
    out = tmp_path / "out.pdf"
    result = insert_experience_block(
        synth_resume, str(out),
        "PLATFORM ENGINEER", "Jan 2025 - Present",
        [("Led", "the platform team across services"),
         ("Built", "internal tooling for engineers"),
         ("Shipped", "the migration to the new runtime")],
        anchor_query=ANCHOR,
        verify=True,
    )
    assert result["new_page"] is None
    doc = pikepdf.open(str(out))
    try:
        assert len(doc.pages) == 1              # no page was added
    finally:
        doc.close()

    # the vector rule moved with the shifted content
    pdf = pikepdf.open(str(out))
    try:
        instrs = list(parse_content_stream(pdf.pages[0]))
        assert _rule_y_operand(instrs) == pytest.approx(RULE_Y - 90.0, abs=0.5)
    finally:
        pdf.close()


def test_insert_large_block_moves_overflow_to_page2(synth_resume, tmp_path):
    """End-to-end crossing regime: a 10-bullet block (H ~= 216pt) pushes
    the bottom sections past the margin -> a new page 2 receives them,
    page 1 keeps the new block, and the harness verifies the whole move."""
    out = tmp_path / "out.pdf"
    bullets = [("Led", "initiative %d for the platform" % i)
               for i in range(1, 11)]
    result = insert_experience_block(
        synth_resume, str(out),
        "PLATFORM ENGINEER", "Jan 2025 - Present",
        bullets,
        anchor_query=ANCHOR,
        verify=True,
    )
    assert result["new_page"] == 2
    doc = pikepdf.open(str(out))
    try:
        assert len(doc.pages) == 2
    finally:
        doc.close()


# ── non-reference-template regressions (service corpus resume-01/21/41) ──

def test_48pt_margin_single_font_bullets(synth_factory, tmp_path):
    """Root cause 1 (margin-48 docs): header detection was pinned to
    x0≈33 AND the bold fallback needed a second font ON the bullet line.
    Plain resumes (single-font bullet lines at any other margin) refused
    with NO_BOLD_EXEMPLAR. Now the margin derives from the document and
    the header font supplies the bold exemplar."""
    path = synth_factory(left_margin=48.0, single_font_bullets=True)
    out = tmp_path / "out.pdf"
    result = insert_experience_block(
        path, str(out),
        "PLATFORM ENGINEER", "Jan 2025 - Present",
        [("Led", "the platform team across services"),
         ("Built", "internal tooling for engineers")],
        anchor_query=ANCHOR,
        verify=True,
    )
    assert result["verification"]["ok"]
    assert result["bold_font_source"] == "header"
    assert result["left_margin"] == 48.0
    doc = pikepdf.open(str(out))
    try:
        assert len(doc.pages) == 1
    finally:
        doc.close()


def test_single_font_document_degrades_bold(synth_factory, tmp_path):
    """Root causes 2+3 (resume-21 class): a single-font document used to
    refuse (header detection returned the body font -> empty body
    candidates). Now it degrades: bold leads render in the body font and
    the choice is reported."""
    path = synth_factory(left_margin=34.0, single_font=True)
    out = tmp_path / "out.pdf"
    result = insert_experience_block(
        path, str(out),
        "PLATFORM ENGINEER", "Jan 2025 - Present",
        [("Led", "the platform team across services"),
         ("Built", "internal tooling for engineers")],
        anchor_query=ANCHOR,
        verify=True,
    )
    assert result["verification"]["ok"]
    assert result["bold_font_source"] == "body"
    assert result["left_margin"] == 34.0


def test_bullet_marker_is_cloned(synth_factory, tmp_path):
    """En-dash documents (resume-31/35 class): the inserted block must use
    the document's OWN bullet marker, not the hard-coded U+2022."""
    import fitz

    path = synth_factory(bullet_marker="\u2013")
    out = tmp_path / "out.pdf"
    result = insert_experience_block(
        path, str(out),
        "PLATFORM ENGINEER", "Jan 2025 - Present",
        [("Led", "the platform team across services"),
         ("Built", "internal tooling for engineers")],
        anchor_query=ANCHOR,
        verify=True,
    )
    assert result["verification"]["ok"]
    doc = fitz.open(str(out))
    try:
        text = doc[0].get_text("text")
    finally:
        doc.close()
    assert "\u2013 Led the platform team across services" in text
    assert result["inserted_lines"][2].startswith("\u2013")


def test_long_title_collides_with_dates(synth_resume, tmp_path):
    """The dates rail is right-aligned to the block's right edge — a title
    wider than the rail minus the dates must refuse honestly instead of
    rendering the two runs on top of each other (found on the service
    corpus: frac-1.0 'Analyst'/'2023' overprints)."""
    from insertblock import FitError

    out = tmp_path / "out.pdf"
    with pytest.raises(FitError) as exc:
        insert_experience_block(
            synth_resume, str(out),
            "Platform Engineering Lead for Distributed Systems and Global "
            "Infrastructure Operations Worldwide 2025",
            "Jan 2025 - Present",
            [("Led", "the platform team")],
            anchor_query=ANCHOR,
            verify=False,
        )
    assert exc.value.reason == "TITLE_DATES_COLLIDE"
