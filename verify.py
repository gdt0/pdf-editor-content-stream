"""
Verification harness for content-stream PDF edits.

Proves "only the intended thing changed, and it rendered as intended"
with four complementary checks (each catches a failure class the others
are blind to):

1. structure     — reopen + page/box/rotation/font/annotation comparison
2. stream_diff   — parsed content streams identical except string operands
                   of text-showing operators (semantic byte fidelity)
3. text_extract  — extraction of the edited PDF equals extraction of the
                   original with the replacements applied (catches silent
                   glyph drops and mojibake that render fine)
4. pixel_diff    — same-rasterizer renders must be EXACTLY equal outside
                   the edited regions' masks; zero-threshold, run at
                   multiple DPIs (catches accidental shifts/kerning drift)

Ships as a library function (VerificationReport.to_dict) so the server
and CI consume the same machine-readable report the tests use.
"""
import fitz
import numpy as np
import pikepdf
from pikepdf import parse_content_stream
from dataclasses import dataclass, field

PIXEL_INFLATE_PT = 2.0  # mask padding around the searched text boxes


class VerificationError(Exception):
    """Raised by edit_pdf(verify=True) when the harness rejects the output."""

    def __init__(self, report, context=None):
        self.report = report
        self.context = context or {}
        super().__init__("verification failed: %s" % report.summary())


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return {"name": self.name, "passed": self.passed, "details": self.details}


@dataclass
class VerificationReport:
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return all(c.passed for c in self.checks)

    def summary(self):
        failed = [c.name for c in self.checks if not c.passed]
        return "ok" if self.ok else "failed checks: %s" % ", ".join(failed)

    def to_dict(self):
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "warnings": list(self.warnings),
        }


def _obj_eq(a, b):
    try:
        return bool(a == b)
    except Exception:
        return repr(a) == repr(b)


# ── Check 1: structure ──────────────────────────────────────────────────

def _check_structure(original, edited, insertion=None):
    details = {}
    ok = True
    old_pdf = pikepdf.open(original)
    new_pdf = pikepdf.open(edited)
    # "new_page" carries the page NUMBER (2 = one new page after page 1)
    expected_new_pages = 1 if (insertion or {}).get("new_page") else 0
    try:
        details["pages"] = {"original": len(old_pdf.pages), "edited": len(new_pdf.pages)}
        if len(new_pdf.pages) != len(old_pdf.pages) + expected_new_pages:
            ok = False
        for pno, (po, pn) in enumerate(zip(old_pdf.pages, new_pdf.pages), 1):
            for attr in ("/MediaBox", "/Rotate"):
                a, b = po.get(attr), pn.get(attr)
                if a != b:
                    ok = False
                    details.setdefault("mismatch", []).append(
                        "page %d %s: %r vs %r" % (pno, attr, a, b)
                    )
            fa = set(str(k) for k in (po.get("/Resources", {}).get("/Font", {}) or {}))
            fb = set(str(k) for k in (pn.get("/Resources", {}).get("/Font", {}) or {}))
            if insertion and fa and not (fa - fb):
                if fa != fb:
                    details.setdefault("fonts_added", []).append(
                        {"page": pno, "added": sorted(fb - fa)})
            elif fa != fb:
                ok = False
                details.setdefault("font_sets_changed", []).append(
                    {"page": pno, "original": sorted(fa), "edited": sorted(fb)}
                )
            na = len(po.get("/Annots", []) or [])
            nb = len(pn.get("/Annots", []) or [])
            if na != nb:
                ok = False
                details.setdefault("annots_changed", []).append(
                    {"page": pno, "original": na, "edited": nb}
                )
        for root, label in ((old_pdf.Root, "original"), (new_pdf.Root, "edited")):
            acro = root.get("/AcroForm")
            if acro is not None:
                for fld in acro.get("/Fields", []) or []:
                    if str(fld.get("/FT", "")).lstrip("/") == "Sig":
                        details.setdefault("signature_fields", []).append(label)
    finally:
        old_pdf.close()
        new_pdf.close()
    return CheckResult("structure", ok, details)


# ── Check 2: stream semantic diff ───────────────────────────────────────

_TEXT_SHOW_OPS = ("Tj", "TJ", "'", '"')


def _operands_differ(aops, bops):
    """Compare operand lists; return None or a description of the first diff."""
    if len(aops) != len(bops):
        return "operand count %d vs %d" % (len(aops), len(bops))
    for i, (a, b) in enumerate(zip(aops, bops)):
        if isinstance(a, pikepdf.Array) and isinstance(b, pikepdf.Array):
            if len(a) != len(b):
                return "operand %d: array length %d vs %d" % (i, len(a), len(b))
            for j, (ae, be) in enumerate(zip(a, b)):
                if not _obj_eq(ae, be):
                    return "operand %d element %d: %r vs %r" % (i, j, a, b)
        elif not _obj_eq(a, b):
            return "operand %d: %r vs %r" % (i, a, b)
    return None


_POS_OPS = ("Tm", "Td", "TD", "T*")
_MOVE_OPS = ("re", "m", "l")


def _check_stream_diff(original, edited, allow_positioning=False,
                       allow_movement_ops=False, light=False):
    details = {"pages_checked": 0, "changed_strings": 0,
               "positioning_ops_changed": 0}
    ok = True
    old_pdf = pikepdf.open(original)
    new_pdf = pikepdf.open(edited)
    if light:
        # insertion mode: ops legitimately move between pages and the new
        # block appends instructions — index alignment is meaningless. The
        # deep guarantees for insertions come from text_extract + pixel.
        try:
            for pno in range(len(new_pdf.pages)):
                try:
                    list(parse_content_stream(new_pdf.pages[pno]))
                    details["pages_checked"] += 1
                except Exception as e:
                    ok = False
                    details["parse_error"] = "page %d: %s" % (pno + 1, e)
            for pno in range(len(old_pdf.pages)):
                try:
                    list(parse_content_stream(old_pdf.pages[pno]))
                except Exception as e:
                    ok = False
                    details["original_parse_error"] = str(e)
            details["mode"] = "light (insertion)"
            return CheckResult("stream_diff", ok, details)
        finally:
            old_pdf.close()
            new_pdf.close()
    try:
        for pno, (po, pn) in enumerate(zip(old_pdf.pages, new_pdf.pages), 1):
            try:
                a = list(parse_content_stream(po))
                b = list(parse_content_stream(pn))
            except Exception as e:
                ok = False
                details["parse_error"] = "page %d: %s" % (pno, e)
                break
            details["pages_checked"] += 1
            if len(b) < len(a):
                ok = False
                details["instruction_count"] = {
                    "page": pno, "original": len(a), "edited": len(b)
                }
                break
            if len(b) > len(a):
                details["inserted_instructions"] =                     details.get("inserted_instructions", 0) + (len(b) - len(a))
            for idx, (ao, aname), (bo, bname) in zip(
                range(len(a)), ((x[0], str(x[1])) for x in a), ((x[0], str(x[1])) for x in b)
            ):
                if aname != bname:
                    ok = False
                    details["operator_changed"] = {
                        "page": pno, "index": idx, "original": aname, "edited": bname
                    }
                    break
                if aname == "INLINE IMAGE":
                    # inline image payloads round-trip loosely through
                    # parse/unparse, so they cannot be byte-compared — but
                    # they are COUNTED, not silently skipped
                    details["inline_images_skipped"] = \
                        details.get("inline_images_skipped", 0) + 1
                    continue
                if aname in _TEXT_SHOW_OPS:
                    diff = _operands_differ(ao, bo)
                    if diff is not None:
                        details["changed_strings"] += 1
                    continue
                diff = _operands_differ(ao, bo)
                if diff is not None:
                    if allow_movement_ops and aname in _MOVE_OPS:
                        details["movement_ops_changed"] =                             details.get("movement_ops_changed", 0) + 1
                        continue
                    if allow_positioning and aname in _POS_OPS:
                        # the editor intentionally moved positioned runs by
                        # the measured width delta — declared and audited
                        details["positioning_ops_changed"] += 1
                        continue
                    ok = False
                    details["non_text_operand_changed"] = {
                        "page": pno, "index": idx, "operator": aname, "diff": diff
                    }
                    break
            if not ok:
                break
    finally:
        old_pdf.close()
        new_pdf.close()
    if details["changed_strings"] == 0:
        ok = False
        details["reason"] = "no text strings changed — replacements were not applied"
    return CheckResult("stream_diff", ok, details)


# ── Check 3: text extraction ────────────────────────────────────────────

def _normalize_ws(s):
    return " ".join(s.split())


def _apply_replacements(text, applied):
    """Apply {old: new} in ONE pass (longest key wins at each position).

    Sequential str.replace over pairs corrupts the expectation on
    chained/overlapping pairs ({"A": "B", "B": "C"} would cascade)."""
    import re

    keys = sorted((k for k in applied if k), key=len, reverse=True)
    if not keys:
        return text
    rx = re.compile("|".join(re.escape(k) for k in keys))
    return rx.sub(lambda m: applied[m.group(0)], text)


def _check_text_extract(original, edited, applied, mode="exact",
                        inserted_words=None):
    details = {"pages_checked": 0, "mismatches": []}
    ok = True
    odo, ndo = fitz.open(original), fitz.open(edited)
    try:
        if inserted_words is not None:
            # insertion: whole-document word multiset must be original
            # words plus exactly the inserted words
            all_old = " ".join(p.get_text("text") for p in odo)
            all_new = " ".join(p.get_text("text") for p in ndo)
            details["pages_checked"] = len(ndo)
            expected_words = sorted((all_old + " " + inserted_words).split())
            actual_words = sorted(all_new.split())
            if expected_words != actual_words:
                ok = False
                details["word_multiset_diff"] = {
                    "missing": [w for w in expected_words
                                if expected_words.count(w)
                                > actual_words.count(w)][:12],
                    "unexpected": [w for w in actual_words
                                   if actual_words.count(w)
                                   > expected_words.count(w)][:12],
                }
            if all_new.count("�") > all_old.count("�"):
                ok = False
                details["replacement_chars"] = {"edited": all_new.count("�")}
            return CheckResult("text_extract", ok, details)
        for pno, (po, pn) in enumerate(zip(odo, ndo), 1):
            details["pages_checked"] += 1
            to = po.get_text("text")
            tn = pn.get_text("text")
            expected = _apply_replacements(to, applied)
            if expected != tn:
                if mode == "words" and sorted(expected.split()) == sorted(tn.split()):
                    # reflowed edit: words moved across lines but none lost
                    details.setdefault("word_multiset_pages", []).append(pno)
                elif _normalize_ws(expected) == _normalize_ws(tn):
                    details.setdefault("whitespace_normalized_pages", []).append(pno)
                else:
                    ok = False
                    details["mismatches"].append({
                        "page": pno,
                        "expected_head": expected[:160],
                        "actual_head": tn[:160],
                    })
            if tn.count("\ufffd") > to.count("\ufffd"):
                ok = False
                details["replacement_chars"] = {
                    "page": pno,
                    "original": to.count("\ufffd"),
                    "edited": tn.count("\ufffd"),
                }
    finally:
        odo.close()
        ndo.close()
    return CheckResult("text_extract", ok, details)


# ── Check 4: masked pixel diff ──────────────────────────────────────────

def _render(page, dpi):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    return arr.reshape(pix.height, pix.width, pix.n)


def _mask_boxes(page, texts, dpi):
    """Pixel-space boxes (x0, y0, x1, y1) covering `texts` on this page.

    Returns (boxes, located_all). Each text's line is masked as a
    full-width horizontal band: a replacement with a different width than
    the original legitimately shifts the remainder of its line (the Phase 3
    fit policy shifts/reflows same-flow runs), so anything changing OUTSIDE
    the edited lines is a failure. Both the old text (searched in the
    original) and the new text (searched in the edited file) are masked.
    """
    scale = dpi / 72.0
    boxes = []
    located_all = True
    for t in texts:
        if not t:
            continue
        found = False
        for r in page.search_for(t):
            band = fitz.Rect(
                page.rect.x0,
                r.y0 - PIXEL_INFLATE_PT,
                page.rect.x1,
                r.y1 + PIXEL_INFLATE_PT,
            ) & page.rect
            boxes.append((
                max(0, int(band.x0 * scale)), max(0, int(band.y0 * scale)),
                min(page.rect.width * scale, (band.x1 + 1) * scale),
                min(page.rect.height * scale, (band.y1 + 1) * scale),
            ))
            found = True
        if not found:
            located_all = False
    return boxes, located_all


def _touched_boxes(page, regions, pno, dpi):
    """Pixel boxes for editor-declared touched regions (content-space bands).

    Content space is y-up; the pixmap is y-down. Only applied when the page
    has no /Rotate (rotation handling is a documented limitation).
    """
    if page.rotation != 0:
        return [], False
    try:
        h = page.mediabox.height
    except Exception:
        return [], False
    scale = dpi / 72.0
    boxes = []
    for r in regions or []:
        if r.get("page") is not None and r["page"] != pno + 1:
            continue
        y0 = max(0.0, (h - r["y1"]) * scale)
        y1 = min(h * scale, (h - r["y0"]) * scale)
        # finite region x-bounds tighten the band; the editor's
        # full-width declarations (x1 = 10**6) keep the whole width
        xr0, xr1 = r.get("x0"), r.get("x1")
        if xr0 is None or xr1 is None or xr1 >= 1e6:
            bx0, bx1 = 0, int(page.rect.width * scale)
        else:
            bx0 = max(0, int(xr0 * scale))
            bx1 = min(int(page.rect.width * scale), int(xr1 * scale) + 1)
        boxes.append((bx0, int(y0), bx1, int(y1) + 1))
    return boxes, True


def _check_pixel_diff(original, edited, applied, dpi_list, touched_regions=None,
                      declared_new_pages=0):
    details = {"dpi": list(dpi_list), "outside_mask_pixels": 0}
    ok = True
    warnings = []
    odo, ndo = fitz.open(original), fitz.open(edited)
    try:
        if len(ndo) != len(odo) + declared_new_pages:
            return CheckResult("pixel_diff", False,
                               {"reason": "page count differs"})
        for dpi in dpi_list:
            for pno in range(len(ndo)):
                b = _render(ndo[pno], dpi)
                if pno >= len(odo):
                    # declared new page: fully masked via touched regions,
                    # there is no original counterpart to diff against
                    continue
                a = _render(odo[pno], dpi)
                if a.shape != b.shape:
                    ok = False
                    details["shape_mismatch"] = {
                        "page": pno + 1, "dpi": dpi,
                        "original": list(a.shape), "edited": list(b.shape),
                    }
                    break
                diff = np.any(a != b, axis=2)
                old_boxes, old_ok = _mask_boxes(odo[pno], list(applied.keys()), dpi)
                new_boxes, new_ok = _mask_boxes(ndo[pno], list(applied.values()), dpi)
                touch_boxes, touch_ok = _touched_boxes(
                    ndo[pno], touched_regions, pno, dpi)
                boxes = old_boxes + new_boxes + touch_boxes
                located_all = old_ok and new_ok
                if not touch_ok:
                    warnings.append(
                        "page %d: touched-region masks skipped (rotated page)"
                        % (pno + 1))
                outside = diff.copy()
                inside_any = False
                for x0, y0, x1, y1 in boxes:
                    xi1, yi1 = int(x1), int(y1)
                    inside_any = inside_any or bool(diff[y0:yi1, x0:xi1].any())
                    outside[y0:yi1, x0:xi1] = False
                outside_count = int(outside.sum())
                details["outside_mask_pixels"] += outside_count
                if outside_count > 0:
                    ok = False
                    rows = np.any(outside, axis=1)
                    cols = np.any(outside, axis=0)
                    rr = np.where(rows)[0]
                    cc = np.where(cols)[0]
                    details.setdefault("unexpected_change_regions", []).append({
                        "page": pno + 1,
                        "dpi": dpi,
                        "pixels": outside_count,
                        "bbox": (
                            [int(cc[0]), int(rr[0]), int(cc[-1]), int(rr[-1])]
                            if rr.size and cc.size else None
                        ),
                    })
                if boxes and not inside_any:
                    warnings.append(
                        "page %d @%ddpi: mask region unchanged (replacement may "
                        "render identically)" % (pno + 1, dpi)
                    )
                if not located_all:
                    warnings.append(
                        "page %d: could not locate mask for some replacement "
                        "text via search (fragmented across operators?) — diffs "
                        "there are reported but cannot be masked" % (pno + 1)
                    )
    finally:
        odo.close()
        ndo.close()
    return CheckResult("pixel_diff", ok, details), warnings


# ── Entry point ─────────────────────────────────────────────────────────

def verify_pdf(original_path, edited_path, applied, dpi=(150, 300),
               touched_regions=None, extraction_mode="exact",
               allow_positioning=False, allow_movement_ops=False,
               insertion=None):
    """Verify that `edited_path` equals `original_path` except that the
    text replacements in `applied` ({old: new}) were rendered.

    `applied` must contain only the replacements that were actually
    applied (edit_pdf passes result['applied'] inverted for this).

    touched_regions: editor-declared content-space bands whose pixels may
        differ (edited lines plus reflow targets) — masked in pixel_diff.
    extraction_mode: 'exact' (default) — extraction must equal the original
        with replacements applied; 'words' — for reflowed edits, the word
        multiset must match (order may change across line breaks, but no
        word may be lost or corrupted).
    allow_positioning: accept changed Tm/Td/TD/T* operands in stream_diff
        (the editor's declared run shifts), still counting them.
    allow_movement_ops: also accept changed re/m/l path operands (declared
        column shifts from block insertion).
    insertion: declared block insertion {'lines': [...], 'new_page': N} —
        verifies the new page, the inserted words, and masks the declared
        blast radius.
    """
    applied = {o: n for o, n in (applied or {}).items() if o != n}
    report = VerificationReport()
    new_pages = (insertion or {}).get("new_page") or 0

    if not applied and not insertion:
        report.checks.append(CheckResult(
            "applicability", False, {"reason": "no applied replacements to verify"}
        ))
        return report
    report.checks.append(CheckResult("applicability", True, {
        "targets": len(applied),
        **({"inserted_lines": len(insertion["lines"])} if insertion else {}),
    }))

    report.checks.append(_check_structure(original_path, edited_path, insertion))
    report.checks.append(_check_stream_diff(original_path, edited_path,
                                            allow_positioning,
                                            allow_movement_ops,
                                            light=bool(insertion)))
    if insertion:
        report.checks.append(_check_text_extract(
            original_path, edited_path, applied, "words",
            inserted_words=" ".join(insertion["lines"])))
    else:
        report.checks.append(_check_text_extract(original_path, edited_path,
                                                 applied, extraction_mode))
    pixel = _check_pixel_diff(original_path, edited_path, applied, dpi,
                              touched_regions,
                              declared_new_pages=1 if new_pages else 0)
    if isinstance(pixel, tuple):
        report.checks.append(pixel[0])
        report.warnings.extend(pixel[1])
    else:
        report.checks.append(pixel)
    return report
