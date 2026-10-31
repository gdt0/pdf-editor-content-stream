"""
PDF Content Stream Editor (Phase 3: token-model engine)

Edits PDF text through a positioned token model: text is decoded across
operator boundaries into lines, matches carry occurrence identity, and the
write path splices replacements at the code level, drops TJ kerning numbers
inside replaced spans, shifts same-flow positioned runs by the measured
width delta, and — when a line would overflow its page's fit boundary —
moves trailing words to the continuation line (limited reflow). When it
cannot fit, the edit is REFUSED with DOES_NOT_FIT; nothing is silently
corrupted.

Usage:
    from pdf_editor import edit_pdf, find_pdf

    edit_pdf("in.pdf", "out.pdf", {"old": "new"}, verify=True)
    find_pdf("in.pdf", "old")   # occurrence listing (read-only)
"""
import json

import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream

from fontcodec import build_codec
from widths import build_widths
from textmodel import (
    FitRefused,
    StreamModel,
    _cluster_lines,
    _scan_stream,
    find_in_lines,
    fit_line_by_moves,
    line_by_y,
    plan_multi,
    shift_anchors,
    splice_multi,
)


def _page_resources(page):
    """Page /Resources, walking up the /Pages tree for inherited resources."""
    node = page
    for _ in range(8):
        res = node.get("/Resources")
        if res is not None:
            return res
        node = node.get("/Parent")
        if node is None:
            break
    return None


_W_SUFFIX = "::widths"


def _split_fonts(fonts_map):
    codecs = {k: v for k, v in fonts_map.items() if not k.endswith(_W_SUFFIX)}
    widths = {k[: -len(_W_SUFFIX)]: v
              for k, v in fonts_map.items() if k.endswith(_W_SUFFIX)}
    return codecs, widths


class _StreamCtx:
    """One editable content stream: page main stream, Form XObject or
    annotation appearance (item 15)."""

    def __init__(self, sid, page_no, page, instructions, fonts_map, writeback):
        self.sid = sid
        self.page_no = page_no
        self.page = page
        self.instructions = instructions
        self.codecs, self.widths = _split_fonts(fonts_map)
        self.writeback = writeback
        self.model = StreamModel(stream_id=sid, instructions=instructions)
        self.tokens = []
        self.lines = []
        self.rescan()
        # The fit boundary is the page's ORIGINAL typography — computed once
        # from the untouched model, so a widened line cannot redefine its
        # own boundary. The HARD boundary is the physical page edge.
        xs = [t.x1 for t in self.tokens if t.x1 is not None]
        self._fit_right = max(xs) if xs else None
        try:
            self._hard_right = float(page.mediabox[2]) - 2.0
        except Exception:
            self._hard_right = None

    def hard_right(self):
        return self._hard_right

    def rescan(self):
        self.tokens = _scan_stream(self.instructions, self.sid,
                                   self.codecs, self.widths)
        self.lines = _cluster_lines(self.tokens)

    def fit_right(self):
        return self._fit_right


def _fonts_map(resources):
    """{name: codec} + {name::widths: FontWidths} + {name: skip_reason}."""
    fonts = {}
    skipped = {}
    for fname, fobj in ((resources or {}).get("/Font") or {}).items():
        key = str(fname).lstrip("/")
        codec, reason = build_codec(fobj)
        if codec is not None:
            fonts[key] = codec
            fonts[key + _W_SUFFIX] = build_widths(fobj)
        elif reason:
            skipped[key] = reason
    return fonts, skipped


def _collect_streams(pdf, page_errors, fonts_skipped):
    """Page main streams + Form XObjects + annotation /AP normal streams."""
    streams = []
    seen = set()

    def add(page_no, page, target, resources, kind):
        fonts, skip = _fonts_map(resources)
        for name, reason in skip.items():
            fonts_skipped.setdefault(name, reason)
        try:
            instructions = list(parse_content_stream(target))
        except Exception as e:
            page_errors.append({"page": page_no,
                                "error": "parse failed (%s): %s" % (kind, e)})
            return
        sid = len(streams)

        if kind == "page":
            def writeback(instrs, changed, page=page):
                if changed:
                    page.Contents = pdf.make_stream(
                        unparse_content_stream(instrs))
        else:
            def writeback(instrs, changed, target=target):
                if changed:
                    target.write(unparse_content_stream(instrs))

        streams.append(_StreamCtx(sid, page_no, page, instructions,
                                  fonts, writeback))

    for page_no, page in enumerate(pdf.pages, 1):
        res = _page_resources(page)
        add(page_no, page, page, res, "page")

        def walk_xobjects(resources, seen):
            for _name, xobj in ((resources or {}).get("/XObject") or {}).items():
                if str(xobj.get("/Subtype", "")) != "/Form":
                    continue
                try:
                    key = xobj.objgen if xobj.is_indirect else None
                except Exception:
                    key = None
                if key is not None and key in seen:
                    continue
                if key is not None:
                    seen.add(key)
                sub_res = xobj.get("/Resources") or resources
                add(page_no, page, xobj, sub_res, "xobject")
                walk_xobjects(sub_res, seen)

        walk_xobjects(res, seen)

        for annot in page.get("/Annots", []) or []:
            ap = annot.get("/AP")
            n = ap.get("/N") if ap is not None else None
            if not isinstance(n, pikepdf.Stream):
                continue
            try:
                key = n.objgen if n.is_indirect else None
            except Exception:
                key = None
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            add(page_no, page, n, n.get("/Resources") or res, "annot")

    return streams


def find_pdf(input_path, query, case_sensitive=True):
    """Read-only occurrence listing (item 12): matches with identity.

    Returns a list of {id, page, y, x, text, context, stream} dicts.
    """
    pdf = pikepdf.open(input_path)
    page_errors = []
    streams = _collect_streams(pdf, page_errors, {})
    out = []
    mid = 0
    for sc in streams:
        for m in find_in_lines(sc.tokens, sc.lines, query, case_sensitive):
            t = sc.tokens[m.start_tok]
            mid += 1
            out.append({
                "id": mid,
                "page": sc.page_no,
                "y": round(t.y, 2),
                "x": round(t.x0, 2),
                "text": m.text,
                "context": m.context(sc.tokens),
                "stream": sc.sid,
            })
    pdf.close()
    return out


def edit_pdf(input_path, output_path, replacements, verify=False,
             dpi=(150, 300), fit="reflow"):
    """
    Edit text in a PDF via the token-model engine.

    Args:
        replacements: {old_text: new_text} — every occurrence is replaced;
            use find_pdf() first for occurrence identity.
        verify: run the verification harness on the saved output
        dpi: DPI list for the pixel-diff check
        fit: 'reflow' (default) — shift same-flow runs and move trailing
                words to the continuation line when needed;
             'shift' — shift runs but refuse if the line would overflow;
             'refuse' — only exact in-place fits, refuse anything that
                would move other content.

    Returns:
        dict with replacements_made, applied, not_found, missing_glyphs,
        fonts_skipped, page_errors, fit_actions, fit_refusals,
        touched_regions, reflowed, verification (when verify=True).

    Raises:
        ValueError: if no replacements could be applied
        VerificationError: if verify=True and the harness rejects the output
    """
    if fit not in ("reflow", "shift", "refuse"):
        raise ValueError("fit must be one of reflow|shift|refuse")

    pdf = pikepdf.open(input_path)
    applied = {old: 0 for old in replacements}
    matched = {old: False for old in replacements}
    ops_mutated = 0
    missing_glyphs = []
    fonts_skipped = {}
    page_errors = []
    fit_actions = []
    fit_refusals = []
    touched_regions = []
    reflowed = False
    shifted = False

    streams = _collect_streams(pdf, page_errors, fonts_skipped)

    for old, new_text in replacements.items():
        budget = None
        spliced = False
        while True:
            matches = [(sc, m) for sc in streams
                       for m in find_in_lines(sc.tokens, sc.lines, old)]
            if budget is None:
                budget = len(matches)
            if not matches or budget <= 0:
                break
            spliced = False
            sc, m = matches[0]
            li, line = line_by_y(sc.lines, sc.tokens[m.start_tok].y)
            group = [mm for msc, mm in matches
                     if msc is sc and mm.line_idx == li]
            try:
                delta, anchor_ids = plan_multi(
                    sc.tokens, line, group, old, new_text,
                    sc.codecs, sc.widths)
                limit = sc.fit_right()
                predicted_end = line.max_x1 + delta
                if delta != 0 and fit == "refuse":
                    raise FitRefused("DOES_NOT_FIT", {
                        "reason": "policy=refuse and width changes by %.2fpt"
                                  % delta,
                    })
                if delta != 0 and limit is not None \
                        and predicted_end > limit:
                    if fit == "shift":
                        raise FitRefused("DOES_NOT_FIT", {
                            "reason": "policy=shift forbids overflow",
                            "predicted_line_end": round(predicted_end, 2),
                            "fit_right": round(limit, 2),
                        })
                    if fit == "reflow":
                        # Limited reflow BEFORE the splice: move trailing
                        # words (only from runs after the match) so the line
                        # converges; then splice+shift the remainder.
                        moved = fit_line_by_moves(
                            sc.model, sc.tokens, sc.lines, line.y,
                            limit, delta, m.end_tok, sc.codecs, sc.widths,
                            sc.instructions, touched_regions,
                            hard_right=sc.hard_right())
                        if moved:
                            reflowed = True
                            # the model changed — re-find this target's match
                            # on the same line and re-plan the anchors
                            fresh = [mm for mm in
                                     find_in_lines(sc.tokens, sc.lines, old)
                                     if abs(sc.tokens[mm.start_tok].y
                                            - line.y) <= 2.0]
                            if not fresh:
                                raise FitRefused("DOES_NOT_FIT", {
                                    "reason": "match lost during reflow",
                                })
                            li, line = line_by_y(sc.lines,
                                                 sc.tokens[fresh[0].start_tok].y)
                            group = fresh
                            delta, anchor_ids = plan_multi(
                                sc.tokens, line, group, old, new_text,
                                sc.codecs, sc.widths)
                splice_multi(sc.instructions, sc.model, sc.tokens, line,
                             group, new_text, sc.codecs)
                spliced = True
                if delta != 0 and anchor_ids:
                    shift_anchors(sc.instructions, sc.model, anchor_ids,
                                  delta, sc.tokens)
                    shifted = True
                sc.rescan()

                touched_regions.append({
                    "page": sc.page_no,
                    "y0": line.y - 16, "y1": line.y + 16,
                    "x0": 0.0, "x1": 10 ** 6,
                })
                fit_actions.append({
                    "target": old, "page": sc.page_no,
                    "line_y": round(line.y, 2), "delta_pt": round(delta, 2),
                    "shifted_runs": len(anchor_ids), "mode": fit,
                })
                count = len(group)
                applied[old] += count
                ops_mutated += count
                matched[old] = True
                budget -= count
                sc.rescan()
            except FitRefused as e:
                matched[old] = True  # the text WAS found; refused for cause
                if spliced:
                    # the splice landed; only the shift/reflow stage failed —
                    # count honestly and record what could not be completed
                    count = len(group)
                    applied[old] += count
                    ops_mutated += count
                    matched[old] = True
                    budget -= count
                if e.reason == "UNENCODABLE":
                    missing_glyphs.append({
                        "old": old, "new": new_text,
                        "missing_chars": e.details.get("missing", []),
                    })
                    matched[old] = True
                else:
                    fit_refusals.append({
                        "target": old, "reason": e.reason,
                        "details": e.details,
                    })
                budget -= len(group)
                break  # this target hit an obstacle; keep the model current
            sc.rescan()

    for sc in streams:
        if sc.model.changed:
            try:
                sc.writeback(sc.instructions, True)
            except Exception as e:
                page_errors.append({
                    "page": sc.page_no, "error": "write-back failed: %s" % e,
                })

    pdf.save(output_path)
    pdf.close()

    not_found = [old for old, was in matched.items() if not was]
    result = {
        "replacements_made": ops_mutated,
        "applied": {old: count for old, count in applied.items() if count},
        "not_found": not_found,
        "missing_glyphs": _dedupe(missing_glyphs),
        "fonts_skipped": fonts_skipped,
        "page_errors": page_errors,
        "fit_actions": fit_actions,
        "fit_refusals": fit_refusals,
        "touched_regions": touched_regions,
        "reflowed": reflowed,
    }

    if verify and ops_mutated > 0:
        from verify import verify_pdf, VerificationError

        applied_reps = {
            old: replacements[old] for old, count in applied.items() if count
        }
        report = verify_pdf(
            input_path, output_path, applied_reps, dpi=dpi,
            touched_regions=touched_regions,
            extraction_mode="words" if reflowed else "exact",
            allow_positioning=shifted,
        )
        result["verification"] = report.to_dict()
        if not report.ok:
            raise VerificationError(report, result)

    if ops_mutated == 0:
        reasons = []
        if not_found:
            reasons.append("text not found: %s"
                           % ", ".join(repr(t) for t in not_found))
        if result["missing_glyphs"]:
            chars = sorted({c for m in result["missing_glyphs"]
                            for c in m["missing_chars"]})
            reasons.append("glyphs missing from embedded fonts: %s"
                           % " ".join(chars))
        for r in fit_refusals:
            reasons.append("refused %s: %s (%s)" % (
                repr(r["target"]), r["reason"], _json_compact(r["details"])))
        if fonts_skipped:
            reasons.append("fonts could not be decoded: %s"
                           % "; ".join(fonts_skipped.values()))
        raise ValueError("No replacements made. "
                         + ("; ".join(reasons) or "unsupported encoding."))

    return result


def _json_compact(d):
    try:
        return json.dumps(d, sort_keys=True)
    except Exception:
        return str(d)


def _dedupe(entries):
    seen = set()
    out = []
    for e in entries:
        key = (e.get("old"), e.get("new"), tuple(e.get("missing_chars", [])))
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out
