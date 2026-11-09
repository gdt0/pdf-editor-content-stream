"""
Positioned text model (Phase 3, items 12-14, 16).

Walks a content stream with full graphics/text state tracking and produces
positioned text tokens:

    q/Q/cm (CTM stack), BT/ET, Tf, Tm, Td/TD (TL), T*, Tc, Tw, Tz, Ts

Each token knows:
- its decoded text and where it lives (instruction index, TJ element)
- its user-space position (x0..x1 on a baseline y) via Trm = Trsf x Tlm
- its positioning "anchor" (the Tm/Td/TD/T* op that placed it) so runs
  positioned independently on the same line can be shifted by a width
  delta without touching anything else

Lines cluster tokens by baseline y; find() matches across operator
boundaries within a line (the cross-operator capability) and returns
occurrence identities.

The fit policy lives here too (item 14): after a replacement changes the
line's width by delta, same-flow runs after the insertion point are
shifted; if the line then exceeds the page's fit boundary, trailing words
move to the continuation line (limited reflow, item 11 scope); if that
cannot converge, the edit is refused with DOES_NOT_FIT.
"""
from dataclasses import dataclass, field

import pikepdf

TEXT_OPS = ("Tj", "TJ", "'", '"')
POS_OPS = ("Tm", "Td", "TD", "T*")

GAP_TOL_DEFAULT = 5.0  # max user-space gap between same-flow runs, pt
Y_CLUSTER_TOL = 2.0


def _mul(m1, m2):
    """Row-vector affine concat: (p*m1)*m2 == p*_mul(m1, m2)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


@dataclass
class TextToken:
    stream_id: int
    op_idx: int
    elem_idx: int  # -1 for Tj/'/", else TJ array element index
    text: str
    x0: float
    x1: float
    y: float
    font_key: str
    size: float
    anchor_idx: int
    anchor_op: str  # 'Tm' | 'Td' | 'TD' | 'T*'
    anchor_x_pos: int  # operand index holding the x translation
    ctm_a: float = 1.0
    anchor_k_x: float = 1.0  # divisor: user-space delta -> anchor operand
    anchor_k_y: float = 1.0

    @property
    def end(self):
        return self.x1


@dataclass
class StreamModel:
    """Tokens of one content stream (page main stream or Form XObject)."""

    stream_id: int
    instructions: list
    tokens: list = field(default_factory=list)
    changed: bool = False
    # (instruction_idx, new_operands) pending replacements for instructions
    # whose operand lists were snapshotted by parse_content_stream
    rebuilt: dict = field(default_factory=dict)


@dataclass
class Line:
    stream_id: int
    token_ids: list  # indices into stream tokens, sorted by x
    y: float
    min_x0: float
    max_x1: float

    def text(self, tokens):
        return "".join(tokens[i].text for i in self.token_ids)


def _scan_stream(instructions, stream_id, codecs, widths):
    """State-tracked walk -> list[TextToken]."""
    tokens = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm_stack = []
    tlm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    leading = 0.0
    font_key = None
    size = 0.0
    tc = tw = tz = 0.0
    rise = 0.0
    in_text = False
    anchor_idx = -1
    anchor_op = None
    anchor_x_pos = 0
    anchor_k_x = 1.0
    anchor_k_y = 1.0

    for idx, instruction in enumerate(instructions):
        operands, operator = instruction.operands, instruction.operator
        op = str(operator)

        if op == "q":
            ctm_stack.append(ctm)
        elif op == "Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
        elif op == "cm" and len(operands) >= 6:
            ctm = _mul(tuple(float(v) for v in operands[:6]), ctm)
        elif op == "BT":
            in_text = True
            tlm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            anchor_idx, anchor_op, anchor_x_pos = -1, None, 0
        elif op == "ET":
            in_text = False
        elif op == "Tf" and len(operands) >= 2:
            font_key = str(operands[0]).lstrip("/")
            size = float(operands[1])
        elif op == "Tc" and operands:
            tc = float(operands[0])
        elif op == "Tw" and operands:
            tw = float(operands[0])
        elif op == "Tz" and operands:
            tz = float(operands[0])
        elif op == "Ts" and operands:
            rise = float(operands[0])
        elif op == "TL" and operands:
            leading = float(operands[0])
        elif op in POS_OPS:
            if not in_text:
                in_text = True
            if op == "Tm" and len(operands) >= 6:
                tlm = tuple(float(v) for v in operands[:6])
                # Tm REPLACES the line matrix: translation is in text space,
                # transformed only by the CTM
                anchor_k_x, anchor_k_y = ctm[0], ctm[3]
                anchor_x_pos = 4
            elif op in ("Td", "TD") and len(operands) >= 2:
                if op == "TD":
                    leading = -float(operands[1])
                # Td composes THROUGH the previous line matrix: the operand
                # delta passes by the old matrix's a/d factors
                anchor_k_x = tlm[0] * ctm[0]
                anchor_k_y = tlm[3] * ctm[3]
                tlm = _mul(
                    (1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1])),
                    tlm,
                )
                anchor_x_pos = 0
            elif op == "T*":
                anchor_k_x = tlm[0] * ctm[0]
                anchor_k_y = tlm[3] * ctm[3]
                tlm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
                anchor_x_pos = None
            anchor_idx, anchor_op = idx, op

        if op not in TEXT_OPS or not in_text:
            continue
        codec = codecs.get(font_key)
        if codec is None:
            continue

        trsf = (size * tz / 100.0, 0.0, 0.0, size, 0.0, rise)
        trm = _mul(trsf, tlm)
        full = _mul(trm, ctm)
        x0, y = full[4], full[5]

        strings = []
        if op == "TJ":
            arr = operands[0] if operands else None
            if isinstance(arr, pikepdf.Array):
                for ei, e in enumerate(arr):
                    if isinstance(e, pikepdf.String):
                        strings.append((ei, bytes(e)))
        else:
            if operands and isinstance(operands[0], pikepdf.String):
                strings.append((0, bytes(operands[0])))

        advance_user = 0.0
        for ei, raw in strings:
            text = codec.decode(raw)
            if not text:
                continue
            w_units = None
            fw = widths.get(font_key)
            if fw is not None:
                w_units = fw.text_width(text, codec, size, tc, tw)
            if w_units is None:
                token_x1 = None  # unmeasurable run
            else:
                token_x1 = x0 + advance_user + w_units * ctm[0]
            tokens.append(TextToken(
                stream_id=stream_id, op_idx=idx, elem_idx=ei, text=text,
                x0=(x0 + advance_user if advance_user else x0), x1=token_x1,
                y=y, font_key=font_key, size=size,
                anchor_idx=anchor_idx, anchor_op=anchor_op,
                anchor_x_pos=(anchor_x_pos if anchor_x_pos is not None else -1),
                ctm_a=ctm[0], anchor_k_x=anchor_k_x, anchor_k_y=anchor_k_y,
            ))
            if w_units is not None:
                advance_user += w_units * ctm[0]

        # advance text position: width of ALL strings incl. numbers (TJ)
        total_units = 0.0
        known = True
        for ei, raw in strings:
            text = codec.decode(raw)
            fw = widths.get(font_key)
            w = fw.text_width(text, codec, size, tc, tw) if fw else None
            if w is None:
                known = False
                break
            total_units += w
        if op == "TJ" and known and isinstance(operands[0], pikepdf.Array):
            for e in operands[0]:
                if not isinstance(e, pikepdf.String):
                    total_units -= float(e) * size * tz / 100000.0
        if known:
            tlm = _mul((1, 0, 0, 1, total_units, 0), tlm)

    return tokens


def _cluster_lines(tokens):
    """Group token indices into lines (baseline clusters)."""
    order = sorted(range(len(tokens)), key=lambda i: (-tokens[i].y, tokens[i].x0))
    lines = []
    for i in order:
        t = tokens[i]
        placed = False
        for line in lines:
            if abs(line["y"] - t.y) <= Y_CLUSTER_TOL:
                line["ids"].append(i)
                placed = True
                break
        if not placed:
            lines.append({"y": t.y, "ids": [i]})
    out = []
    for line in lines:
        ids = sorted(line["ids"], key=lambda i: tokens[i].x0)
        out.append(Line(
            stream_id=tokens[ids[0]].stream_id, token_ids=ids, y=line["y"],
            min_x0=tokens[ids[0]].x0, max_x1=tokens[ids[-1]].x1 or tokens[ids[-1]].x0,
        ))
    out.sort(key=lambda ln: -ln.y)
    return out


@dataclass
class Match:
    line_idx: int
    start_tok: int  # token INDEX (global list)
    start_char: int
    end_tok: int
    end_char: int
    text: str

    def context(self, tokens, width=30):
        t = tokens[self.start_tok]
        prefix = t.text[: self.start_char][-width:]
        suffix = tokens[self.end_tok].text[self.end_char:][:width]
        return f"...{prefix}{self.text}{suffix}..."


def find_in_lines(tokens, lines, query, case_sensitive=True):
    """Cross-operator occurrences of `query` within clustered lines."""
    if not query:
        # an empty needle never advances .find(), so the loop below would
        # match forever and exhaust memory — refuse instead of hanging
        return []
    matches = []
    for li, line in enumerate(lines):
        ids = line.token_ids
        pieces = [tokens[i].text for i in ids]
        full = "".join(pieces)
        hay = full if case_sensitive else full.lower()
        needle = query if case_sensitive else query.lower()
        start = 0
        while True:
            pos = hay.find(needle, start)
            if pos == -1:
                break
            # map [pos, pos+len) back to tokens
            off = 0
            start_tok = end_tok = None
            start_char = end_char = 0
            for k, piece in zip(ids, pieces):
                plen = len(piece)
                if start_tok is None and pos < off + plen:
                    start_tok, start_char = k, pos - off
                if pos + len(needle) <= off + plen:
                    end_tok, end_char = k, pos + len(needle) - off
                    break
                off += plen
            if start_tok is None or end_tok is None:
                break
            matches.append(Match(
                line_idx=li, start_tok=start_tok, start_char=start_char,
                end_tok=end_tok, end_char=end_char, text=query,
            ))
            start = pos + len(needle)
    return matches


# ── Mutation engine (items 13-14): splice, shift, limited reflow ────────

class FitRefused(Exception):
    """Refusal is a first-class outcome: the edit cannot be applied
    without breaking the layout, and this says exactly why."""

    def __init__(self, reason, details=None):
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


def _line_of(tokens, lines, tok_idx):
    for li, line in enumerate(lines):
        if tok_idx in line.token_ids:
            return li, line
    return None, None


def _chain_after(tokens, line, end_tok, end_char_in_line, gap_tol=GAP_TOL_DEFAULT):
    """Same-flow trailing tokens: consecutive-in-x runs after the match.

    A run joins the chain only if it starts within gap_tol of the previous
    run's end — so justified continuation runs shift together, while a
    right-aligned column further along the baseline does not.
    """
    ids = line.token_ids
    pos = ids.index(end_tok)
    chain = []
    prev_end = None
    for k in ids[pos:]:
        t = tokens[k]
        if chain:
            if prev_end is None or t.x0 - prev_end > gap_tol:
                break
        elif t is not tokens[end_tok]:
            if prev_end is not None and t.x0 - prev_end > gap_tol:
                break
        chain.append(k)
        prev_end = t.x1 if t.x1 is not None else t.x0
    return chain


def plan_multi(tokens, line, matches, old, new, codecs, widths):
    """Width delta + trailing anchors for all matches on one line.

    delta = count*width(new) - width(all matched spans). Refuses UNMEASURED
    when any involved font cannot be measured (refusal is first-class).
    """
    ids = line.token_ids
    match_toks = set()
    for m in matches:
        i = ids.index(m.start_tok)
        j = ids.index(m.end_tok)
        match_toks.update(ids[i:j + 1])

    w_old = 0.0
    for k in match_toks:
        t = tokens[k]
        c = codecs.get(t.font_key)
        f = widths.get(t.font_key)
        if c is None or f is None:
            raise FitRefused("UNMEASURED", {"font": t.font_key})
        segs = [(m.start_char if m.start_tok == k else 0,
                 m.end_char if m.end_tok == k else len(t.text)) for m in matches
                if m.start_tok <= k <= m.end_tok]
        for s, e in segs:
            w = f.text_width(t.text[s:e], c, t.size)
            if w is None:
                raise FitRefused("UNMEASURED", {"font": t.font_key})
            w_old += w

    first = tokens[matches[0].start_tok]
    codec = codecs.get(first.font_key)
    missing = codec.can_encode(new)
    if missing:
        # coverage gate comes FIRST: report the absent glyphs, not a
        # measurement failure
        raise FitRefused("UNENCODABLE", {"missing": missing})
    fw = widths.get(first.font_key)
    w_new_unit = fw.text_width(new, codec, first.size)
    if w_new_unit is None:
        raise FitRefused("UNMEASURED", {"font": first.font_key})
    delta_user = (w_new_unit * len(matches) - w_old) * first.ctm_a

    # trailing same-flow chain after the LAST match
    last = matches[-1]
    chain = _chain_after(tokens, line, last.end_tok, last.end_char)
    own = {tokens[k].anchor_idx for k in chain if k in match_toks}
    anchor_ids = []
    for k in chain:
        t = tokens[k]
        if t.anchor_idx in own or t.anchor_idx in anchor_ids:
            continue
        if t.anchor_op in POS_OPS and t.anchor_x_pos >= 0:
            anchor_ids.append(t.anchor_idx)
    return delta_user, anchor_ids


def _encode_operand_text(codec, text):
    data, missing = codec.encode(text)
    if missing:
        raise FitRefused("UNENCODABLE", {"missing": missing})
    return pikepdf.String(data)


def _key(t):
    # dataclass equality is by value; ranges must key on identity
    return id(t)


def splice_multi(instructions, smodel, tokens, line, matches, new, codecs):
    """Apply all matches on one line in a single pass.

    Each match's replacement text is written at the match's start; tokens
    fully inside matches are emptied and TJ kerning numbers between
    rewritten elements are dropped (item 13); each match's end token keeps
    its post-match tail.
    """
    smodel.changed = True
    i0 = line.token_ids.index(matches[0].start_tok)
    i1 = line.token_ids.index(matches[-1].end_tok)
    span = line.token_ids[i0: i1 + 1]

    # ranges per token (keyed by token identity): (start, end, insert_here)
    per_tok = {}
    for m in matches:
        a = span.index(m.start_tok)
        b = span.index(m.end_tok)
        for k in span[a: b + 1]:
            s = m.start_char if m.start_tok == k else 0
            e = m.end_char if m.end_tok == k else len(tokens[k].text)
            ins = (m.start_tok == k)
            per_tok.setdefault(_key(tokens[k]), []).append((s, e, ins))

    def new_text_for(t):
        text = t.text
        for s, e, ins in sorted(per_tok[_key(t)], reverse=True):
            text = text[:s] + (new if ins else "") + text[e:]
        return text

    # pre-compute and pre-encode every token's new text BEFORE mutating:
    # a refusal here leaves the stream completely untouched
    planned = {}
    for k in span:
        t = tokens[k]
        text = new_text_for(t)
        data, missing = codecs[t.font_key].encode(text)
        if missing:
            raise FitRefused("UNENCODABLE",
                             {"missing": missing, "text": text})
        planned[_key(t)] = (text, data)

    # group by instruction
    ops = {}
    for k in span:
        ops.setdefault(tokens[k].op_idx, []).append(tokens[k])

    for op_idx, toks in ops.items():
        ins = instructions[op_idx]
        operands, operator = ins.operands, ins.operator
        tok_by_elem = {t.elem_idx: t for t in toks}
        if str(operator) == "TJ":
            arr = list(operands[0])
            lo = min(tok_by_elem)
            hi = max(tok_by_elem)
            out = []
            for ei, e in enumerate(arr):
                if ei < lo or ei > hi:
                    out.append(e)
                    continue
                if ei in tok_by_elem:
                    t = tok_by_elem[ei]
                    out.append(pikepdf.String(planned[_key(t)][1]))
                # else: kerning number inside the matched span -> dropped
            new_operands = list(operands)
            new_operands[0] = pikepdf.Array(out)
        else:
            t = toks[0]
            new_operands = list(operands)
            new_operands[0] = pikepdf.String(planned[_key(t)][1])
        instructions[op_idx] = pikepdf.ContentStreamInstruction(new_operands, operator)


def shift_anchors(instructions, smodel, anchor_ids, delta_user, tokens):
    """Move positioned runs right by delta (user-space delta converted to
    the anchor operand's space: text-line matrix, then CTM)."""
    smodel.changed = True
    for idx in anchor_ids:
        tok = next(t for t in tokens if t.anchor_idx == idx)
        ins = instructions[idx]
        operands, operator = ins.operands, ins.operator
        new_operands = list(operands)
        pos = tok.anchor_x_pos
        k = tok.anchor_k_x or 1.0
        new_operands[pos] = float(operands[pos]) + delta_user / k
        instructions[idx] = pikepdf.ContentStreamInstruction(new_operands, operator)


# ── Limited reflow (item 11/14): move trailing words to the continuation ─

def line_by_y(lines, y):
    for li, line in enumerate(lines):
        if abs(line.y - y) <= Y_CLUSTER_TOL:
            return li, line
    return None, None


def find_continuation(lines, li, hang_tol=40.0):
    """The paragraph continuation line: immediately below, same stream.

    The continuation may sit within a hang-indent tolerance of the edited
    line's left edge (bullet labels hang left of their continuation, e.g.
    LaTeX itemize); it must not be indented dramatically further, which
    would indicate a different block (nested list, table cell)."""
    if li + 1 >= len(lines):
        return None
    line, cont = lines[li], lines[li + 1]
    if cont.stream_id != line.stream_id:
        return None
    if not (line.min_x0 - 2.0 <= cont.min_x0 <= line.min_x0 + hang_tol):
        return None
    if line.y - cont.y > 200:
        return None
    return cont


def move_last_word(instructions, smodel, tokens, line, cont, codecs, widths,
                   after_tok=None):
    """Move the line's trailing word (plus its join space) to the start of
    the continuation line's first run. Returns the moved width in user pt,
    or None when nothing splittable remains.

    Operates on the CONCATENATED text of the trailing tokens, so per-glyph
    fragmentation (xetex emits one string per glyph) splits correctly at
    word boundaries across token boundaries. Moves come only from tokens
    AFTER `after_tok` (the match's end token) — the edited run's own tail
    advances naturally and the replacement itself must never be moved.
    """
    ids = line.token_ids
    start_pos = ids.index(after_tok) + 1 if after_tok in ids else 0
    tail_ids = ids[start_pos:]
    if not tail_ids:
        return None

    texts = [tokens[k].text for k in tail_ids]
    full = "".join(texts)
    stripped = full.rstrip()
    sep = max(stripped.rfind(" "), stripped.rfind("\xa0"))
    if sep < 0:
        return None  # no word boundary anywhere in the trailing text
    moved_word = stripped[sep + 1:]
    if not moved_word.strip():
        return None
    keep_full = full[: sep + 1]
    moved_text = moved_word + " "

    # every touched token must share one font so the moved text renders
    # identically at the continuation's first run
    fonts = {tokens[k].font_key for k, txt in zip(tail_ids, texts)
             if txt and (not tokens[k].text.strip() or True)}
    touched_fonts = set()
    off = 0
    for k, txt in zip(tail_ids, texts):
        t_start, t_end = off, off + len(txt)
        if t_end > sep + 1 and txt.strip():
            touched_fonts.add(tokens[k].font_key)
        off = t_end
    if len(touched_fonts) > 1:
        return None  # mixed-font word: refuse rather than restyle
    font_key = next(iter(touched_fonts)) if touched_fonts else None
    if font_key is None:
        return None

    cont_first = tokens[cont.token_ids[0]]
    if (cont_first.font_key != font_key
            or abs(cont_first.size - tokens[tail_ids[-1]].size) > 0.1):
        return None  # cannot render moved text in the continuation font

    codec = codecs.get(font_key)
    fw = widths.get(font_key)
    if codec is None or fw is None:
        return None
    moved_w = fw.text_width(moved_text, codec, tokens[tail_ids[-1]].size)
    if moved_w is None:
        return None

    smodel.changed = True
    # trim/clear the trailing tokens per the keep/move boundary
    off = 0
    for k, txt in zip(tail_ids, texts):
        t = tokens[k]
        t_start, t_end = off, off + len(txt)
        if t_end <= sep + 1:
            new = txt                      # entirely kept
        elif t_start >= sep + 1:
            new = ""                       # entirely moved
        else:
            new = txt[: sep + 1 - t_start]  # boundary token: keep prefix
        if new != txt:
            data, missing = codecs[t.font_key].encode(new)
            if missing:
                return None
            _write_operand_bytes(instructions, t.op_idx, t.elem_idx, data)
        off = t_end

    data_moved, miss2 = codec.encode(moved_text)
    if miss2:
        return None
    cont_first = tokens[cont.token_ids[0]]
    _write_operand_bytes(instructions, cont_first.op_idx,
                         cont_first.elem_idx, data_moved, prepend=True)
    return moved_w * tokens[tail_ids[-1]].ctm_a


def _write_operand_bytes(instructions, op_idx, elem_idx, data, prepend=False):
    ins = instructions[op_idx]
    operands, operator = ins.operands, ins.operator
    new_operands = list(operands)
    if str(operator) == "TJ":
        arr = list(operands[0])
        old = bytes(arr[elem_idx]) if elem_idx < len(arr) else b""
        new = data + old if prepend else data
        arr[elem_idx] = pikepdf.String(new)
        new_operands[0] = pikepdf.Array(arr)
    else:
        old = bytes(operands[0])
        new = data + old if prepend else data
        new_operands[0] = pikepdf.String(new)
    instructions[op_idx] = pikepdf.ContentStreamInstruction(new_operands, operator)


def fit_line_by_moves(smodel, tokens, lines, line_y, fit_right, delta,
                      end_tok, codecs, widths, instructions, touched=None,
                      max_moves=8, hard_right=None):
    """Limited reflow, decided BEFORE the splice: while the line would
    overflow the SOFT boundary (the page's original text extent), move
    trailing words (only from runs after the match) to the continuation
    line. When moves cannot help, the growth is still accepted if it stays
    within the HARD boundary (the physical page edge); only beyond the hard
    boundary is the edit refused (DOES_NOT_FIT). Returns the moved width."""
    moved_total = 0.0
    for _ in range(max_moves):
        li, line = line_by_y(lines, line_y)
        if line is None:
            return moved_total
        predicted = line.max_x1 + delta
        if predicted <= fit_right:
            return moved_total
        cont = find_continuation(lines, li)
        movable = cont is not None
        if movable:
            moved = move_last_word(instructions, smodel, tokens, line, cont,
                                   codecs, widths, after_tok=end_tok)
            if not moved:
                movable = False
        if not movable:
            # no reflow possible: accept growth within the hard page edge,
            # refuse beyond it (refusal stays first-class)
            if hard_right is not None and predicted <= hard_right:
                return moved_total
            raise FitRefused("DOES_NOT_FIT", {
                "line_end": round(predicted, 2),
                "fit_right": round(fit_right, 2),
                "hard_right": round(hard_right, 2) if hard_right else None,
                "reason": ("no continuation line available"
                           if cont is None else
                           "no splittable trailing word after the match"),
            })
        moved_total += moved
        if touched is not None:
            touched.append({"y0": cont.y - 16, "y1": cont.y + 16,
                            "x0": 0.0, "x1": 10 ** 6})
        tokens[:] = _scan_stream(instructions, smodel.stream_id,
                                 codecs, widths)
        lines[:] = _cluster_lines(tokens)
    raise FitRefused("DOES_NOT_FIT", {"reason": "too many moves"})
