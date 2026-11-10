"""
Layout toolkit (Phase: tools, not guarantees).

The library does not promise an edit is layout-safe — these are the tools
for callers to DETECT what an edit broke and FIX it themselves:

- inspect_pdf(path)      geometry, base transform, fonts, line map
- find_overlaps(path)    text-vs-text collisions (e.g. after an insertion)
- shift_region(...)      move every positioned run in a region by a delta
                         (the fix primitive for overprints)

All results are plain dicts/lists — machine-readable, JSON-able.
"""
import fitz
import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream

from textmodel import _inv, _mul, _scan_stream, _cluster_lines


# ── inspect ─────────────────────────────────────────────────────────────

def _base_transform(page_instrs):
    """The CTM established before the first text op — flips and scales
    live here (xetex pages are typically 1 0 0 -1 0 H, sometimes scaled)."""
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack = []
    for ins in page_instrs:
        operands, operator = ins.operands, ins.operator
        op = str(operator)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(operands) >= 6:
            a2, b2, c2, d2, e2, f2 = (float(v) for v in operands[:6])
            ctm = (a2 * ctm[0] + b2 * ctm[2],
                   a2 * ctm[1] + b2 * ctm[3],
                   c2 * ctm[0] + d2 * ctm[2],
                   c2 * ctm[1] + d2 * ctm[3],
                   e2 * ctm[0] + f2 * ctm[2] + ctm[4],
                   e2 * ctm[1] + f2 * ctm[3] + ctm[5])
        elif op in ("Tj", "TJ", "'", '"', "BT"):
            break
    return ctm


def inspect_pdf(path):
    """Geometry, coordinate space, fonts and the text line map of a PDF.

    Returns a dict:
      pages: [{page, width, height, rotation, base_transform:{a,b,c,d,e,f},
               flipped, scaled, fonts, lines: [{y, x0, x1, text}]}]
    """
    pdf = pikepdf.open(path)
    pages = []
    for pno, page in enumerate(pdf.pages, 1):
        try:
            codecs, widths = _page_font_maps(page)
            instrs = list(parse_content_stream(page))
            ctm = _base_transform(instrs)
            tokens = _scan_stream(instrs, pno - 1, codecs, widths)
            lines = _cluster_lines(tokens)
            line_map = []
            for ln in lines:
                txt = "".join(tokens[k].text for k in ln.token_ids)
                line_map.append({"y": round(ln.y, 2), "x0": round(ln.min_x0, 2),
                                 "x1": round(ln.max_x1, 2), "text": txt[:120]})
            pages.append({
                "page": pno,
                "width": round(float(page.mediabox[2]) - float(page.mediabox[0]), 2),
                "height": round(float(page.mediabox[3]) - float(page.mediabox[1]), 2),
                "rotation": int(page.get("/Rotate", 0) or 0),
                "base_transform": {"a": round(ctm[0], 4), "b": round(ctm[1], 4),
                                   "c": round(ctm[2], 4), "d": round(ctm[3], 4),
                                   "e": round(ctm[4], 2), "f": round(ctm[5], 2)},
                "flipped": ctm[3] < 0,
                "scaled": abs(abs(ctm[3]) - 1.0) > 0.01,
                "fonts": sorted({t.font_key for t in tokens}),
                "n_lines": len(lines),
                "lines": line_map,
            })
        except Exception as e:
            # one corrupt page must not abort the whole inspection
            pages.append({"page": pno,
                          "error": "inspection failed: %s: %s"
                                   % (type(e).__name__, e)})
    pdf.close()
    return {"path": str(path), "pages": pages}


def _page_font_maps(page):
    """(codecs, widths) for a page's fonts, via pdf_editor's guarded
    build (this was previously a same-named, different-signature duplicate
    of pdf_editor._fonts_map)."""
    from pdf_editor import _fonts_map, _page_resources

    fonts, _skipped = _fonts_map(_page_resources(page))
    codecs = {k: v for k, v in fonts.items() if not k.endswith("::widths")}
    widths = {k[: -len("::widths")]: v
              for k, v in fonts.items() if k.endswith("::widths")}
    return codecs, widths


# ── overlap detection ───────────────────────────────────────────────────

def find_overlaps(path, min_frac=0.25, max_pairs=200):
    """Text collisions: pairs of WORDS whose rendered boxes intersect
    significantly (the signature of an overprinting edit).

    Renderer-truthful: measured on MuPDF's word boxes, so it catches what a
    viewer actually shows. Returns a dict {count, pairs, truncated} where
    pairs is a list of {page, a, b, frac} dicts sorted worst-first.
    """
    doc = fitz.open(path)
    out = []
    for pno in range(len(doc)):
        words = doc[pno].get_text("words")  # x0,y0,x1,y1,word,block,line,wordno
        boxes = [(x[0], x[1], x[2], x[3], x[4]) for x in words]
        n = len(boxes)
        for i in range(n):
            ax0, ay0, ax1, ay1, at = boxes[i]
            for j in range(i + 1, n):
                bx0, by0, bx1, by1, bt = boxes[j]
                ix = min(ax1, bx1) - max(ax0, bx0)
                iy = min(ay1, by1) - max(ay0, by0)
                if ix <= 0 or iy <= 0:
                    continue
                inter = ix * iy
                a_area = max((ax1 - ax0) * (ay1 - ay0), 1e-6)
                b_area = max((bx1 - bx0) * (by1 - by0), 1e-6)
                frac = inter / min(a_area, b_area)
                if frac >= min_frac:
                    out.append({
                        "page": pno + 1,
                        "a": {"text": at, "bbox": [round(v, 1) for v in boxes[i][:4]]},
                        "b": {"text": bt, "bbox": [round(v, 1) for v in boxes[j][:4]]},
                        "frac": round(frac, 3),
                    })
                    if len(out) >= max_pairs:
                        doc.close()
                        return {"count": len(out), "pairs": out,
                                "truncated": True}
    doc.close()
    return {"count": len(out), "pairs": out, "truncated": False}


# ── region shift (the fix primitive) ────────────────────────────────────

def shift_region(input_path, output_path, dy=0.0, dx=0.0, y0=None, y1=None,
                 x0=None, x1=None, page=None, include_vectors=False,
                 text_any=None):
    """Move every positioned run whose baseline falls inside the region
    [y0, y1] x [x0, x1] (user space, y-up; None = unbounded) by
    (dx, dy) user units.

    This is the manual fix primitive: after an insertion causes an
    overprint, shift the colliding block out of the way yourself.

    Self-checked: after applying, the model re-measures every moved anchor
    and reports any that did not land on the intended position (returns
    them in result['mismatches'] — non-empty means the transform defeated
    the move and you should not trust the output).
    """
    pdf = pikepdf.open(input_path)
    from pdf_editor import _collect_streams
    page_errors = []
    streams = _collect_streams(pdf, page_errors, {})
    # main page streams only: tokens from Form XObjects / annotations live
    # in the XOBJECT's coordinate space, not the page space the region
    # bounds are expressed in — shifting them by page-space rules would
    # move the wrong things
    contexts = [sc for sc in streams
                if (page is None or sc.page_no == page)
                and sc.kind == "page"]
    text_any = text_any or None  # an empty filter list must not select nothing

    y_lo = y0 if y0 is not None else -1e9
    y_hi = y1 if y1 is not None else 1e9
    x_lo = x0 if x0 is not None else -1e9
    x_hi = x1 if x1 is not None else 1e9

    moved = []
    for sc in contexts:
        # per-anchor selection from the CURRENT model; text_any narrows the
        # selection to lines whose text contains any of the substrings —
        # needed when the colliding content occupies the SAME coordinates
        line_text_by_anchor = {}
        if text_any:
            for ln in sc.lines:
                txt = "".join(sc.tokens[k].text for k in ln.token_ids)
                if any(s in txt for s in text_any):
                    for k in ln.token_ids:
                        line_text_by_anchor.setdefault(
                            sc.tokens[k].anchor_idx, txt)
        selection = {}
        for t in sc.tokens:
            if not (y_lo <= t.y <= y_hi):
                continue
            if not (x_lo <= t.x0 <= x_hi):
                continue
            if text_any is not None:
                lt = line_text_by_anchor.get(t.anchor_idx)
                if lt is None or not any(s in lt for s in text_any):
                    continue
            selection.setdefault(t.anchor_idx, t)
        deltas = {}
        for idx, t in selection.items():
            ins = sc.instructions[idx]
            op = str(ins.operator)
            if op == "Tm":
                if dy:
                    deltas.setdefault((idx, 5), dy / (t.anchor_k_y or 1.0))
                if dx:
                    deltas.setdefault((idx, 4), dx / (t.anchor_k_x or 1.0))
            elif op in ("Td", "TD"):
                if dy:
                    deltas.setdefault((idx, 1), dy / (t.anchor_k_y or 1.0))
                if dx:
                    deltas.setdefault((idx, 0), dx / (t.anchor_k_x or 1.0))
        before = {idx: t.y for idx, t in selection.items()}

        for (idx, ypos), d_raw in deltas.items():
            ins = sc.instructions[idx]
            operands, operator = ins.operands, ins.operator
            new_operands = list(operands)
            new_operands[ypos] = float(operands[ypos]) + d_raw
            sc.instructions[idx] = pikepdf.ContentStreamInstruction(
                new_operands, operator)
        sc.model.changed = True

        sc.rescan()
        # self-check: did every selected anchor land where intended?
        mismatches = []
        after = {}
        for idx, t_before in selection.items():
            t_after = next((t for t in sc.tokens
                            if t.anchor_idx == idx), None)
            if t_after is None:
                mismatches.append({"anchor": idx, "reason": "anchor vanished"})
                continue
            want_x = (t_before.x0 + dx) if dx else t_before.x0
            want_y = t_before.y + dy
            after[idx] = t_after.y
            if abs(t_after.y - want_y) > 0.5 or abs(t_after.x0 - want_x) > 0.5:
                mismatches.append({
                    "anchor": idx, "text": t_before.text[:40],
                    "want": [round(want_x, 2), round(want_y, 2)],
                    "got": [round(t_after.x0, 2), round(t_after.y, 2)],
                })
        moved.append({
            "page": sc.page_no, "moved_anchors": len(selection),
            "mismatches": mismatches,
        })
        if include_vectors:
            _shift_vectors(sc, y_lo, y_hi, x_lo, x_hi, dx, dy)

    for sc in streams:
        if sc.model.changed:
            sc.writeback(sc.instructions, True)
    pdf.save(output_path)
    pdf.close()

    total_mismatch = sum(len(m["mismatches"]) for m in moved)
    result = {
        "moved_anchors": sum(m["moved_anchors"] for m in moved),
        "pages": moved,
        "mismatches": [mm for m in moved for mm in m["mismatches"]],
        "self_check": "pass" if total_mismatch == 0 else "FAILED",
    }
    return result


def _shift_vectors(sc, y_lo, y_hi, x_lo, x_hi, dx, dy):
    """Shift path/rect ops whose reference point is inside the region.

    Region bounds are USER space: each op's raw coordinates convert through
    the CURRENT CTM for the region test (previously raw operands were
    compared against user-space bounds directly), and the delta applies
    through the CTM's inverse linear part — so on flipped/scaled pages
    vectors move the same direction and distance as text."""
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack = []
    for idx, ins in enumerate(sc.instructions):
        operands, operator = ins.operands, ins.operator
        op = str(operator)
        if op == "q":
            stack.append(ctm)
            continue
        if op == "Q":
            if stack:
                ctm = stack.pop()
            continue
        if op == "cm" and len(operands) >= 6:
            ctm = _mul(tuple(float(v) for v in operands[:6]), ctm)
            continue
        ypos = {"re": 1, "m": 1, "l": 1}.get(op)
        if ypos is None or len(operands) <= ypos:
            continue
        try:
            vx, vy = float(operands[0]), float(operands[ypos])
        except (TypeError, ValueError):
            continue
        ux = vx * ctm[0] + vy * ctm[2] + ctm[4]
        uy = vx * ctm[1] + vy * ctm[3] + ctm[5]
        if not (y_lo <= uy <= y_hi and x_lo <= ux <= x_hi):
            continue
        try:
            ictm = _inv(ctm)
        except ValueError:
            continue  # singular CTM: cannot convert, leave the op alone
        rdx = ictm[0] * dx + ictm[1] * dy
        rdy = ictm[2] * dx + ictm[3] * dy
        new_operands = list(operands)
        new_operands[ypos] = vy + rdy
        new_operands[0] = vx + rdx
        sc.instructions[idx] = pikepdf.ContentStreamInstruction(
            new_operands, operator)
    sc.model.changed = True
