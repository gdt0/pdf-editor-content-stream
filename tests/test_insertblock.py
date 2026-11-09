"""Regression tests for insertblock stream routing (P0: no-threshold case)."""
import os

import fitz
import pikepdf
import pytest

from insertblock import _route_streams, insert_experience_block
from pdf_editor import _collect_streams
from textmodel import find_in_lines, line_by_y

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESUME = os.path.join(ROOT, "sanket_resume_v2_edited.pdf")
V7_RESUME = os.path.join(ROOT, "sanket_dawar_v7_fixed.pdf")


def _main_stream(path):
    pdf = pikepdf.open(path)
    page_errors, fonts_skipped = [], {}
    streams = _collect_streams(pdf, page_errors, fonts_skipped)
    assert streams, "no editable streams found"
    return pdf, streams[0]


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


def test_no_threshold_keeps_everything_on_page1():
    """P0 regression: move_threshold=None (no unit crosses the bottom
    margin) must route NOTHING to page 2 — everything below the cut stays
    on page 1 shifted by -H. The old code passed anchor_y as the threshold,
    dumping the whole lower half onto an unshifted mid-page page 2, and the
    prefix-only page2 stream minted a blank page on top of that."""
    # pristine reference values, taken before any mutation
    pdf0, sc0 = _main_stream(RESUME)
    try:
        anchor_y = _line_y(sc0, "Associate Software Engineer")
        name_y = _line_y(sc0, "SANKET KUMAR DAWAR")
    finally:
        pdf0.close()
    cut_y = _cut_y_for(sc0, anchor_y)
    H = 54.0

    pdf, sc = _main_stream(RESUME)
    try:
        page1, page2, _ctm = _route_streams(
            sc, cut_y, -H, float("-inf"), 0.0, [])

        # nothing routed -> no page-2 stream at all (no prefix-only page)
        assert page2 == []
        # every original instruction survives on page 1 (q/Q rebalancing
        # may append padding, never drop content)
        assert len(page1) >= len(sc.instructions)

        # below-cut content physically shifted down by H, above-cut intact
        sc.rescan()
        assert _line_y(sc, "Associate Software Engineer") == \
            pytest.approx(anchor_y - H, abs=1.0)
        assert _line_y(sc, "SANKET KUMAR DAWAR") == pytest.approx(
            name_y, abs=0.5)
    finally:
        pdf.close()


def test_explicit_threshold_splits_to_page2():
    """Sanity on the unchanged crossing regime: an explicit threshold above
    the lower content must produce a page-2 stream carrying that content
    (plus the state prefix), not an empty one."""
    pdf, sc = _main_stream(RESUME)
    try:
        anchor_y = _line_y(sc, "Associate Software Engineer")
        cut_y = _cut_y_for(sc, anchor_y)
        # force the split: everything well below the anchor's section
        threshold = anchor_y - 120.0
        page1, page2, _ctm = _route_streams(
            sc, cut_y, -54.0, threshold, 200.0, [])
        assert page2, "expected routed content on page 2"
        assert len(page1) + len(page2) >= len(sc.instructions)
    finally:
        pdf.close()


def test_insert_block_no_overflow_stays_on_one_page(tmp_path):
    """End-to-end regression of the fixed no-crossing path: a small block
    above the first role shifts the lower sections down without any of
    them crossing the bottom margin -> same page count, no page minted,
    and the full verification harness (incl. the insertion checks with
    new_page=None) must pass."""
    out = tmp_path / "out.pdf"
    result = insert_experience_block(
        V7_RESUME, str(out),
        "Platform Engineer", "Jan 2025 - Present",
        [("Led", "the platform team across services"),
         ("Built", "internal tooling for 200+ engineers"),
         ("Shipped", "the migration to the new runtime")],
        anchor_query="Associate Software Engineer",
        verify=True,
    )
    assert result["new_page"] is None
    doc = fitz.open(str(out))
    try:
        assert len(doc) == 2                      # no page was added
        text1 = doc[0].get_text()
        text2 = doc[1].get_text()
    finally:
        doc.close()
    assert "Platform Engineer" in text1
    # below-anchor content stayed on page 1 (shifted), page 2 untouched
    assert "Senior Engineer" in text1
    assert "Skills" in text2
