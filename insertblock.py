"""
Block insertion (beyond replacement): insert a formatted experience block
— role title, right-aligned dates, formatted bullets — into a PDF resume,
shifting the column below and moving whole sections to a new page when
they would cross the bottom margin.

The document's own geometry is the source of truth: title→bullet gap,
bullet pitch and block gap are measured from the existing layout; new
lines clone the exemplar fonts (italic title, bold bullet lead, regular
body, bullet glyph) and are right-aligned using measured widths.

Everything the insertion moves is declared to the verification harness
(touched regions, shift bands, the new page), so the harness still proves
that nothing OUTSIDE the declared blast radius changed.
"""
import os

import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream

from fontcodec import build_codec
from widths import build_widths
from textmodel import _scan_stream, _cluster_lines, find_in_lines, line_by_y

TOP_MARGIN_REF = 788.10   # page 1's first baseline (name line)
BOTTOM_MARGIN = 31.0      # matches this template's ~33pt symmetric margin
BAND = 16.0               # touched-region padding around a baseline


def _line_text(tokens, line):
    return "".join(tokens[k].text for k in line.token_ids)


def _detect_header_font(tokens, lines, anchor_y):
    """Section headers: single-font, left-margin, short lines above the
    anchor ('Experience', 'Projects', ...). Their common font is the
    document's heading font."""
    from collections import Counter

    counts = Counter()
    for ln in lines:
        if ln.y >= anchor_y:
            continue
        toks = [tokens[k] for k in ln.token_ids]
        keys = {t.font_key for t in toks}
        if len(keys) != 1 or abs(ln.min_x0 - 33.0) > 2.0:
            continue
        txt = "".join(t.text for t in toks).strip()
        if 0 < len(txt) < 30:
            counts[keys.pop()] += 1
    return counts.most_common(1)[0][0] if counts else None


def _is_section_header(tokens, line, header_font):
    toks = [tokens[k] for k in line.token_ids]
    if not toks or abs(line.min_x0 - 33.0) > 2.0:
        return False
    keys = {t.font_key for t in toks}
    txt = "".join(t.text for t in toks).strip()
    return keys == {header_font} and 0 < len(txt) < 40


def _measure_layout(tokens, lines, anchor_y):
    """Title→bullet gap, single-line bullet pitch, block gap — all measured
    from the document's own role blocks."""
    bullet_lines = [ln for ln in lines if 0 < ln.y < anchor_y + 1
                    and _line_text(tokens, ln).lstrip().startswith("\u2022")]
    below_anchor = [ln for ln in lines if ln.y < anchor_y - 0.5]
    d_title_to_bullet = anchor_y - below_anchor[0].y if below_anchor else 18.3

    # single-line bullet pitch: a bullet followed directly by another bullet
    ys = [ln.y for ln in bullet_lines]
    pitches = []
    for i in range(len(ys) - 1):
        nxt = ys[i] - ys[i + 1]
        if 15.0 < nxt < 22.0:  # single-line → single-line pitch band
            pitches.append(nxt)
    pitch = pitches[0] if pitches else 18.5

    # block gap: last line before the NEXT italic role title
    title2 = None
    prev_above = None
    for ln in lines:
        if ln.y >= anchor_y - 0.5:
            continue
        fk = {tokens[k].font_key for k in ln.token_ids}
        txt = _line_text(tokens, ln)
        if "NBTEVH" in fk and prev_above is not None:
            title2 = ln.y
            d_block_gap = prev_above - title2
            break
        prev_above = ln.y
    else:
        d_block_gap = 20.7
    return d_title_to_bullet, pitch, d_block_gap



def _actual_of(tokens, idx, orig_y):
    for t in tokens:
        if t.op_idx == idx:
            return t.y - orig_y[idx]
    return 0.0


def _route_streams(sc, cut_y, shift, move_threshold, dy2, page_errors):
    """Partition the main stream into (page1_instrs, page2_instrs).

    Transform-aware: the page content may live in a flipped space
    (cm 1 0 0 -1 0 H + Tm 1 0 0 -1, common in xetex output). The model's
    token y is true user space (y-up); raw stream coordinates relate to it
    through the current CTM. Vector ops route by the last TEXT y (so page
    furniture like background/clip never wanders off) and all coordinate
    edits convert user-space deltas through ctm.d.

    Coordinate edits are applied to sc.instructions before partitioning
    (parse-snapshot discipline)."""
    tokens = sc.tokens
    op_y = {}
    anchor_map = {}  # anchor op idx -> its token (own line y + divisor)
    for t in tokens:
        op_y.setdefault(t.op_idx, t.y)
        anchor_map.setdefault(t.anchor_idx, t)

    routes = []  # (idx, dest, dy_user) — user-space intent
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack = []
    last_text_y = None
    prefix_end = 0   # everything before the first positioned op = page
    prefix_done = False  # furniture (base transform, background, clip)
    for idx, ins in enumerate(sc.instructions):
        operands, operator = ins.operands, ins.operator
        op = str(operator)
        if op == "q":
            stack.append(ctm)
            routes.append((idx, "1", 0.0))
            continue
        if op == "Q":
            if stack:
                ctm = stack.pop()
            routes.append((idx, "1", 0.0))
            continue
        if op == "cm" and len(operands) >= 6:
            a2, b2, c2, d2, e2, f2 = (float(v) for v in operands[:6])
            ctm = (a2 * ctm[0] + b2 * ctm[2],
                   a2 * ctm[1] + b2 * ctm[3],
                   c2 * ctm[0] + d2 * ctm[2],
                   c2 * ctm[1] + d2 * ctm[3],
                   e2 * ctm[0] + f2 * ctm[2] + ctm[4],
                   e2 * ctm[1] + f2 * ctm[3] + ctm[5])
            routes.append((idx, "1", 0.0))
            continue

        if idx in anchor_map:
            # a positioning anchor routes by ITS OWN line
            y = anchor_map[idx].y
        elif op in ("re", "m", "l") and len(operands) >= 2:
            y = last_text_y                     # route with surrounding text
        else:
            y = last_text_y
        if y is not None:
            last_text_y = y
            if not prefix_done:
                prefix_end = idx
                prefix_done = True

        if y is None or y >= cut_y - 0.5:
            routes.append((idx, "1", 0.0))
        elif y < move_threshold - 0.5:
            routes.append((idx, "2", dy2))
        else:
            routes.append((idx, "1", shift))

    _YPOS = {"Tm": 5, "Td": 1, "TD": 1, "re": 1, "m": 1, "l": 1}

    def _apply_raw(delta_by_idx):
        for idx, d_raw in delta_by_idx.items():
            if not d_raw:
                continue
            ins = sc.instructions[idx]
            operands, operator = ins.operands, ins.operator
            ypos = _YPOS.get(str(operator))
            if ypos is not None and len(operands) > ypos:
                new_operands = list(operands)
                new_operands[ypos] = float(operands[ypos]) + d_raw
                sc.instructions[idx] = pikepdf.ContentStreamInstruction(
                    new_operands, operator)
        sc.model.changed = True

    # Self-calibrating application: raw stream deltas relate to user-space
    # deltas through a global +/- factor (the page's flip convention). Apply
    # the intent as a first guess, measure the real per-op movement with the
    # model, then correct once. No sign theory — the model is the oracle.
    orig_anchor_y = {t.anchor_idx: t.y for t in tokens}
    orig_raw = {}
    for idx, _dy in [(i, d) for i, _dest, d in routes if d]:
        t0 = next((t for t in tokens if t.anchor_idx == idx), None)
        if t0 is not None:
            ins = sc.instructions[idx]
            ypos = {"Tm": 5, "Td": 1, "TD": 1}.get(str(ins.operator))
            if ypos is not None and len(ins.operands) > ypos:
                orig_raw[idx] = float(ins.operands[ypos])
    movable = [(idx, dy) for idx, dest, dy in routes if dy]
    intent = {idx: dy for idx, dy in movable}
    applied = {idx: 0.0 for idx in orig_raw}
    k = 1.0
    converged = not movable
    for _ in range(4):
        deltas = {idx: intent[idx] / (k or 1.0) - applied[idx]
                  for idx in applied}
        _apply_raw(deltas)
        for idx, d in deltas.items():
            applied[idx] += d
        tokens = _scan_stream(sc.instructions, sc.sid,
                              sc.codecs, sc.widths)
        errs = []
        for idx, dy in movable:
            t0 = next((t for t in tokens if t.anchor_idx == idx), None)
            if t0 is not None:
                errs.append((idx, round(dy, 2), round(t0.y - orig_anchor_y[idx], 2)))
        if not errs or all(abs(e[1] - e[2]) < 0.5 for e in errs):
            converged = True
            break
        # k from the measured movement of the first moved anchor:
        idx_m, _dy_m, _act_m = errs[0]
        t_m = next(t for t in tokens if t.anchor_idx == idx_m)
        moved = t_m.y - orig_anchor_y[idx_m]
        if applied[idx_m]:
            k = moved / applied[idx_m]
    if not converged:
        raise RuntimeError("insertion shifts did not converge")

    # NOTE: the calibration loop above has already applied every raw delta
    # and verified convergence through the model — do NOT re-apply intents
    # here (the stale tail that did so produced quarter-applied shifts).

    # page-2 prefix: STATE ops only (base transform, color/alpha) — the
    # document's own geometry (background, section rules drawn before any
    # text) belongs to page 1. q/Q re-balanced after dropping geometry.
    _GEOMETRY_OPS = ("re", "m", "l", "W", "n", "W*", "f*", "f",
                     "BT", "ET", "Tf", "TJ", "Tj")
    prefix = []
    qb = 0
    for ins in sc.instructions[:prefix_end]:
        op = str(ins.operator)
        if op in _GEOMETRY_OPS:
            break
        prefix.append(ins)
        if op == "q":
            qb += 1
        elif op == "Q":
            qb -= 1
    while qb > 0:
        prefix.append(pikepdf.ContentStreamInstruction(
            [], pikepdf.Operator("Q")))
        qb -= 1
    while qb < 0:
        prefix.insert(0, pikepdf.ContentStreamInstruction(
            [], pikepdf.Operator("q")))
        qb += 1

    # partition the positioned part; per-page q/Q rebalance for the routed
    # ops only (the prefixes are balanced by construction)
    page1 = list(sc.instructions[:prefix_end])
    # page 2 exists only if real content routed there — a prefix-only
    # stream would mint a blank page even in the everything-fits case
    page2 = list(prefix) if any(dest == "2" for _i, dest, _d
                                in routes[prefix_end:]) else []
    balance = {"1": 0, "2": 0}
    for (idx, dest, _dy), ins in zip(routes[prefix_end:],
                                     sc.instructions[prefix_end:]):
        (page1 if dest == "1" else page2).append(ins)
        op = str(ins.operator)
        if op == "q":
            balance[dest] += 1
        elif op == "Q":
            balance[dest] -= 1
    for page, key in ((page1, "1"), (page2, "2")):
        while balance[key] < 0:
            page.insert(0, pikepdf.ContentStreamInstruction(
                [], pikepdf.Operator("q")))
            balance[key] += 1
        while balance[key] > 0:
            page.append(pikepdf.ContentStreamInstruction(
                [], pikepdf.Operator("Q")))
            balance[key] -= 1
    return page1, page2, ctm


def _inv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return (d / det, -b / det, -c / det, a / det,
            (c * f - d * e) / det, (b * e - a * f) / det)


def _mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def _runs_bytes(runs, base_ctm=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)):
    """runs: [(font_key, size, user_x, user_y, text, codec)] -> bytes.

    Each run's Tm is translate(user pos) x base_ctm^-1, so the rendered
    position is the user-space coordinate regardless of the graphics state
    at the append point (flipped xetex pages included)."""
    inv = _inv(base_ctm)
    parts = []
    for font_key, size, x, y, text, codec in runs:
        m = _mul((1.0, 0.0, 0.0, 1.0, x, y), inv)
        data, missing = codec.encode(text)
        if missing:
            raise FitError("UNENCODABLE", {"missing": missing, "text": text})
        parts.append(
            "BT /%s %.4f Tf %.6f %.6f %.6f %.6f %.6f %.6f Tm <%s> Tj ET"
            % (font_key, size, m[0], m[1], m[2], m[3], m[4], m[5],
               data.hex().upper())
        )
    return ("\n".join(parts) + "\n").encode("latin-1")


class FitError(Exception):
    def __init__(self, reason, details=None):
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


_FALLBACK_TTF = {
    "regular": r"C:\Windows\Fonts\times.ttf",
    "bold": r"C:\Windows\Fonts\timesbd.ttf",
    "italic": r"C:\Windows\Fonts\timesi.ttf",
}
_FALLBACK_BASE = {
    "regular": "TimesNewRomanPSMT",
    "bold": "TimesNewRomanPS-BoldMT",
    "italic": "TimesNewRomanPS-ItalicMT",
}


def _ensure_fallback_font(pdf, page, sc, style):
    """Embed a Times New Roman simple TrueType font (WinAnsi) for runs the
    document's subset fonts cannot encode. Registers it in the page
    resources and the stream codec/width maps. Returns the resource name."""
    res_name = "FZ" + style
    if res_name in sc.codecs:
        return res_name
    from fontTools.ttLib import TTFont

    path = _FALLBACK_TTF[style]
    if not os.path.exists(path):
        raise FitError("NO_FALLBACK_FONT", {"path": path})
    tt = TTFont(path)
    cmap = tt.getBestCmap()
    hmtx = tt["hmtx"].metrics
    upm = tt["head"].unitsPerEm

    widths = []
    for code in range(256):
        try:
            uni = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            uni = None
        gname = cmap.get(ord(uni)) if uni else None
        w = hmtx[gname][0] if gname else 0
        widths.append(round(w * 1000.0 / upm, 1))

    data = open(path, "rb").read()
    head = tt["head"]
    hhea = tt["hhea"]
    post = tt["post"]
    fd = pikepdf.Dictionary(
        FontName=pikepdf.Name("/" + _FALLBACK_BASE[style]),
        Flags=32,
        FontBBox=pikepdf.Array([float(head.xMin), float(head.yMin),
                                float(head.xMax), float(head.yMax)]),
        ItalicAngle=float(post.italicAngle),
        Ascent=float(hhea.ascent) * 1000.0 / upm,
        Descent=float(hhea.descent) * 1000.0 / upm,
        CapHeight=float(getattr(head, "unitsPerEm", 1000)) * 0.7,
        StemV=80.0,
        FontFile2=pdf.make_stream(data),
    )
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.TrueType,
        BaseFont=pikepdf.Name("/" + _FALLBACK_BASE[style]),
        FirstChar=0,
        LastChar=255,
        Widths=pikepdf.Array(widths),
        Encoding=pikepdf.Name.WinAnsiEncoding,
        FontDescriptor=fd,
    )
    res = _page_resources_of(sc.page)
    font_dict = res.get("/Font")
    font_dict[pikepdf.Name("/" + res_name)] = font
    sc.codecs[res_name] = build_codec(font)[0]
    sc.widths[res_name] = build_widths(font)
    return res_name


def _page_resources_of(page):
    node = page
    for _ in range(8):
        res = node.get("/Resources")
        if res is not None:
            return res
        node = node.get("/Parent")
        if node is None:
            break
    raise FitError("NO_RESOURCES", {})


def insert_experience_block(input_path, output_path, title, dates, bullets,
                            anchor_query=None, verify=True, dpi=(150, 300)):
    """Insert a role block (title + right-aligned dates + bullets) above the
    role line matching `anchor_query`, in the document's own format.

    bullets: list of (bold_lead, regular_rest).
    Sections below that would cross the bottom margin move to a new page.
    """
    if anchor_query is None:
        anchor_query = title  # not useful; callers pass the existing role
    pdf = pikepdf.open(input_path)
    page_errors = []
    fonts_skipped = {}
    from pdf_editor import _collect_streams
    streams = _collect_streams(pdf, page_errors, fonts_skipped)
    sc = streams[0]  # main page stream
    tokens, lines = sc.tokens, sc.lines

    # locate the anchor role line
    ms = find_in_lines(tokens, lines, anchor_query)
    if not ms:
        raise ValueError("anchor role line not found: %r" % anchor_query)
    li, anchor_line = line_by_y(lines, tokens[ms[0].start_tok].y)
    anchor_y = anchor_line.y
    right_edge = anchor_line.max_x1

    d_tb, pitch, d_gap = _measure_layout(tokens, lines, anchor_y)

    # exemplars
    title_toks = [tokens[k] for k in anchor_line.token_ids]
    title_font = title_toks[0].font_key
    title_size = title_toks[0].size
    title_codec = sc.codecs[title_font]
    title_fw = sc.widths[title_font]

    bullet_line = None
    for ln in lines:
        if anchor_y - 40 < ln.y < anchor_y - 5 and \
                _line_text(tokens, ln).lstrip().startswith("\u2022"):
            bullet_line = ln
            break
    if bullet_line is None:
        raise ValueError("no bullet line found below the anchor role")
    btoks = [tokens[k] for k in bullet_line.token_ids]
    bullet_tok = next(t for t in btoks if t.text.lstrip().startswith("\u2022"))
    bullet_font, bullet_size, bullet_x = bullet_tok.font_key, bullet_tok.size, bullet_tok.x0
    # bold exemplar: the document's heading font (same family used for the
    # bullet lead words); regular: the dominant body font on this line
    header_font = _detect_header_font(tokens, lines, anchor_y)
    bold_font = header_font or next(
        t.font_key for t in btoks if t.font_key != bullet_font)
    bold_size = bullet_size
    body_counts = {}
    for t in btoks:
        if t is not bullet_tok and t.font_key != bold_font:
            body_counts[t.font_key] = body_counts.get(t.font_key, 0) + len(t.text)
    reg_font = max(body_counts, key=body_counts.get)
    reg_size = next(t.size for t in btoks if t.font_key == reg_font)
    bullet_codec = sc.codecs[bullet_font]
    bold_codec = sc.codecs[bold_font]
    bold_fw = sc.widths[bold_font]
    reg_codec = sc.codecs[reg_font]
    reg_fw = sc.widths[reg_font]
    title_fw = sc.widths[title_font]

    # layout of the new block
    n = len(bullets)
    H = d_tb + (n - 1) * pitch + d_gap
    new_title_y = anchor_y  # the new role takes the anchor's slot
    bullet_ys = [new_title_y - d_tb - i * pitch for i in range(n)]

    # sections (keep-together units): header line + body until next header
    headers = [li2 for li2, ln in enumerate(lines)
               if _is_section_header(tokens, ln, header_font)]
    units = []  # (start line idx, end line idx exclusive)
    if headers:
        bounds = headers + [len(lines)]
        start = 0
        for h in bounds:
            if h > start:
                units.append((start, h))
            start = h
    else:
        units = [(0, len(lines))]

    # the FIRST unit below the cut must fit on page 1 after the shift —
    # it cannot move to page 2 (it continues the section the new block
    # is inserted into). Later units that would cross the bottom margin
    # move to page 2 wholesale.
    for a, b in units:
        uls = [lines[i] for i in range(a, b) if lines[i].y < anchor_y - 0.5]
        if uls:
            first_unit_min = min(ln.y for ln in uls)
            if first_unit_min - H < BOTTOM_MARGIN:
                raise FitError("DOES_NOT_FIT", {
                    "needed_shift": round(H, 2),
                    "first_unit_end_y": round(first_unit_min - H, 2),
                    "bottom_margin": BOTTOM_MARGIN,
                })
            break

    def unit_lines(a, b):
        return [lines[i] for i in range(a, b)]

    # a unit moves to page 2 if any of its lines would cross the floor
    move_threshold = None
    for a, b in units:
        uls = [lines[i] for i in range(a, b) if lines[i].y < anchor_y - 0.5]
        if not uls:
            continue
        if min(ln.y for ln in uls) - H < BOTTOM_MARGIN:
            # threshold sits ABOVE the unit's topmost line: the whole unit
            # (and everything below it) routes to page 2
            move_threshold = max(ln.y for ln in uls) + BAND + 1.0
            break
    dy2 = TOP_MARGIN_REF - move_threshold if move_threshold is not None else 0.0

    # move_threshold None = no unit crosses the bottom margin, so NOTHING
    # routes to page 2 — everything below the cut stays on page 1 shifted
    # by -H. Route with a threshold below every y to make that explicit
    # (passing anchor_y here used to dump the whole lower half onto an
    # unshifted mid-page page 2).
    route_threshold = (move_threshold if move_threshold is not None
                       else float("-inf"))

    # cut between the heading above and the anchor role: the new block takes
    # the anchor's slot, so the anchor line itself must shift too
    ys_above = [ln.y for ln in lines
                if anchor_y + 1.0 < ln.y < anchor_y + 60.0]
    cut_y = anchor_y + 7.0
    if ys_above:
        cut_y = (anchor_y + min(ys_above)) / 2.0
    page1_instrs, page2_instrs, ctm_final = _route_streams(
        sc, cut_y, -H, route_threshold, dy2, page_errors)

    # emit the new block at the anchor's slot — runs the subset fonts
    # cannot encode fall back to an embedded Times New Roman resource
    def run_ready(font_key, size, style, text):
        codec = sc.codecs[font_key]
        _miss = codec.can_encode(text)
        if _miss:
            # subset lacks glyphs for this run: embed the fallback resource
            res = _ensure_fallback_font(pdf, sc.page, sc, style)
            return res, sc.codecs[res], sc.widths[res]
        return font_key, codec, sc.widths[font_key]

    runs = []

    def add_run(font_key, size, style, x, y, text):
        fk, codec, fw = run_ready(font_key, size, style, text)
        w = fw.text_width(text, codec, size)
        if w is None:
            raise FitError("UNMEASURED", {"font": fk, "text": text})
        runs.append((fk, size, x, y, text, codec))
        return w

    title_w = add_run(title_font, title_size, "italic", 33.0, new_title_y,
                      title)
    date_w = add_run(title_font, title_size, "italic", 0.0, new_title_y,
                     dates)
    runs[-1] = (runs[-1][0], runs[-1][1], right_edge - date_w, runs[-1][3],
                runs[-1][4], runs[-1][5])
    for (bold_lead, rest), by in zip(bullets, bullet_ys):
        x = bullet_x
        x += add_run(bullet_font, bullet_size, "regular", x, by, "\u2022 ")
        x += add_run(bold_font, bold_size, "bold", x, by, bold_lead + " ")
        add_run(reg_font, reg_size, "regular", x, by, rest)

    new_bytes = _runs_bytes(runs, ctm_final)
    page1_data = unparse_content_stream(page1_instrs) + b"\n" + new_bytes
    sc.page.Contents = pdf.make_stream(page1_data)

    new_page_index = None
    if page2_instrs:
        p2 = pdf.add_blank_page(page_size=(
            float(sc.page.mediabox[2]), float(sc.page.mediabox[3])))
        p2.Resources = sc.page.Resources
        p2.Contents = pdf.make_stream(unparse_content_stream(page2_instrs))
        del pdf.pages[len(pdf.pages) - 1]  # unhook from the end (object lives)
        pdf.pages.insert(1, p2)            # move to position 1
        new_page_index = 2

    pdf.save(output_path)
    pdf.close()

    inserted_lines = ([title, dates] +
                      ["• %s %s" % (b[0], b[1]) for b in bullets])
    result = {
        "inserted_lines": inserted_lines,
        "shift": round(-H, 2),
        "new_page": new_page_index,
        "touched_regions": [
            {"page": 1, "y0": bullet_ys[-1] - BAND,
             "y1": new_title_y + BAND, "x0": 0.0, "x1": 10 ** 6},
            {"page": 1, "y0": -10 ** 6, "y1": anchor_y - BAND,
             "x0": 0.0, "x1": 10 ** 6},
        ] + ([{"page": 2, "y0": -10 ** 6, "y1": 10 ** 6,
               "x0": 0.0, "x1": 10 ** 6}] if new_page_index else []),
        "page_errors": page_errors,
        "fonts_skipped": fonts_skipped,
    }
    if verify:
        from verify import verify_pdf, VerificationError
        report = verify_pdf(
            input_path, output_path, {}, dpi=dpi,
            insertion={
                "lines": inserted_lines,
                "new_page": new_page_index,
                "cut_y": anchor_y,
            },
            touched_regions=result["touched_regions"],
            extraction_mode="words",
            allow_positioning=True,
            allow_movement_ops=True,
        )
        result["verification"] = report.to_dict()
        if not report.ok:
            raise VerificationError(report, result)
    return result
