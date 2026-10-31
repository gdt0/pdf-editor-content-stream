"""
PDF Content Stream Editor
Edits PDF text by modifying raw content stream operators (Tj/TJ).
Decodes text through a per-font codec layer (ToUnicode CMaps, simple-font
encodings, /Differences). Zero format change — only text bytes are modified.

Glyph coverage is validated BEFORE any mutation: replacements that need
characters missing from the font are refused and reported in
missing_glyphs — never partially applied, never silently truncated.

Usage:
    from pdf_editor import edit_pdf

    edit_pdf("input.pdf", "output.pdf", {"old text": "new text"})
"""
import pikepdf
from pikepdf import Name, String as PdfString
from pikepdf import parse_content_stream, unparse_content_stream

from fontcodec import build_codec

TEXT_OPS = ("Tj", "TJ", "'", '"')


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


def _page_codecs(page, cache):
    """Build {font_name: codec} for a page. Undecodable fonts are recorded."""
    codecs = {}
    res = _page_resources(page)
    fonts = res.get("/Font") if res is not None else None
    if fonts is None:
        return codecs
    for fname, fobj in fonts.items():
        name = str(fname).lstrip("/")
        key = None
        try:
            key = fobj.objgen if fobj.is_indirect else None
        except Exception:
            key = None
        if key is not None and key in cache:
            codec, reason = cache[key]
        else:
            codec, reason = build_codec(fobj)
            if key is not None:
                cache[key] = (codec, reason)
        if codec is not None:
            codecs[name] = codec
    return codecs


def _skipped_fonts(page, cache):
    """{font_name: reason} for fonts on this page that have no codec."""
    skipped = {}
    res = _page_resources(page)
    fonts = res.get("/Font") if res is not None else None
    if fonts is None:
        return skipped
    for fname, fobj in fonts.items():
        name = str(fname).lstrip("/")
        key = None
        try:
            key = fobj.objgen if fobj.is_indirect else None
        except Exception:
            key = None
        if key is not None and key in cache:
            codec, reason = cache[key]
        else:
            codec, reason = build_codec(fobj)
            if key is not None:
                cache[key] = (codec, reason)
        if codec is None:
            skipped[name] = reason
    return skipped


def _apply_simple(decoded, replacements, codec, missing_glyphs, matched):
    """str.replace pass over one decoded string, coverage-gated.

    Returns (new_text, hit_olds). A replacement is applied only when the
    full result is encodable; otherwise it is recorded and skipped.
    `matched` marks targets that occurred in decoded text even when the
    replacement was refused (so they are not reported as not_found).
    """
    new = decoded
    hit = []
    for old, new_text in replacements.items():
        if old not in new:
            continue
        matched[old] = True
        candidate = new.replace(old, new_text)
        missing = codec.can_encode(candidate)
        if missing:
            missing_glyphs.append(
                {"old": old, "new": new_text, "missing_chars": missing}
            )
            continue
        new = candidate
        hit.append(old)
    return new, hit


def edit_pdf(input_path, output_path, replacements, verify=False, dpi=(150, 300)):
    """
    Edit text in a PDF by modifying content stream operators directly.

    Args:
        input_path: Path to input PDF
        output_path: Path for output PDF
        replacements: Dict of {old_text: new_text} to replace
        verify: Run the verification harness on the saved output and
            embed the report in the result (raises VerificationError on fail)
        dpi: DPI list for the pixel-diff check when verify=True

    Returns:
        dict with 'replacements_made' (operator-level count), 'applied'
        (per-target counts), 'not_found', 'missing_glyphs', 'fonts_skipped',
        'page_errors', and 'verification' when verify=True.

    Raises:
        ValueError: If no replacements were made
        VerificationError: If verify=True and the harness rejects the output
    """
    pdf = pikepdf.open(input_path)
    applied = {old: 0 for old in replacements}
    matched = {old: False for old in replacements}
    ops_mutated = 0
    missing_glyphs = []
    fonts_skipped = {}
    page_errors = []
    codec_cache = {}

    for page_no, page in enumerate(pdf.pages, 1):
        codecs = _page_codecs(page, codec_cache)
        for fname, (codec, reason) in _skipped_fonts(page, codec_cache).items():
            fonts_skipped.setdefault(fname, reason)

        try:
            instructions = parse_content_stream(page)
        except Exception as e:
            page_errors.append({"page": page_no, "error": "parse failed: %s" % e})
            continue

        current_font = None
        page_changed = False

        # NOTE: unpacking an Instruction gives a snapshot of its operands —
        # mutating that list is silently lost on write-back (a latent bug in
        # the old code; all of its test PDFs happened to be TJ-only, where
        # array element mutation goes through the live pikepdf Array).
        # Mutations here rebuild the Instruction and replace it by index.
        for instr_idx, instruction in enumerate(instructions):
            operands, operator = instruction.operands, instruction.operator
            op = str(operator)

            if op == "Tf" and len(operands) >= 1:
                current_font = str(operands[0]).lstrip("/")
                continue

            if op not in TEXT_OPS:
                continue

            codec = codecs.get(current_font)
            if codec is None:
                continue

            # ── Tj: simple string ──
            if op == "Tj" and len(operands) == 1:
                if not isinstance(operands[0], pikepdf.String):
                    continue
                decoded = codec.decode(bytes(operands[0]))
                new_text, hit = _apply_simple(decoded, replacements, codec, missing_glyphs, matched)
                if hit:
                    new_operands = list(operands)
                    new_operands[0] = PdfString(codec.encode(new_text)[0])
                    instructions[instr_idx] = pikepdf.ContentStreamInstruction(new_operands, operator)
                    page_changed = True
                    ops_mutated += 1
                    for old in hit:
                        applied[old] += 1

            # ── TJ: array with kerning offsets ──
            elif op == "TJ" and len(operands) == 1:
                arr = operands[0]
                if not isinstance(arr, pikepdf.Array):
                    continue
                tj_mutated = False

                # Decode all string elements into one text with positions.
                def _decode_array():
                    parts = []
                    text = ""
                    for elem_idx, elem in enumerate(arr):
                        if isinstance(elem, pikepdf.String):
                            decoded = codec.decode(bytes(elem))
                            parts.append((elem_idx, decoded, len(text)))
                            text += decoded
                    return parts, text

                str_parts, work = _decode_array()
                for old, new_text in replacements.items():
                    search_start = 0
                    while True:
                        pos = work.find(old, search_start)
                        if pos == -1:
                            break

                        end_pos = pos + len(old)
                        first_elem = None
                        for ei, edec, estart in str_parts:
                            if estart <= pos < estart + len(edec):
                                first_elem = (ei, estart)
                                break
                        if not first_elem:
                            break
                        matched[old] = True

                        first_ei, first_start = first_elem
                        first_edec = next(
                            edec for ei, edec, _ in str_parts if ei == first_ei
                        )
                        first_end = first_start + len(first_edec)
                        # Preserve text before the match, the replacement,
                        # AND the tail of the first element after the match
                        # (the old code dropped that tail — a latent bug).
                        candidate = (
                            work[first_start:pos]
                            + new_text
                            + work[end_pos:first_end]
                        )
                        missing = codec.can_encode(candidate)
                        if missing:
                            # Refuse: record and skip past this match. The
                            # stream and bookkeeping stay untouched (the old
                            # code updated bookkeeping anyway — a bug).
                            missing_glyphs.append(
                                {"old": old, "new": new_text, "missing_chars": missing}
                            )
                            search_start = end_pos
                            continue

                        # Replacement is fully encodable — mutate the array.
                        arr[first_ei] = PdfString(codec.encode(candidate)[0])
                        for ei, edec, estart in str_parts:
                            if ei == first_ei or estart <= first_start:
                                continue  # first element + everything before it
                            est_end = estart + len(edec)
                            if estart >= end_pos:
                                break  # untouched from here on
                            if est_end <= end_pos:
                                arr[ei] = PdfString(b"")  # fully inside match
                            else:
                                # Partial overlap: keep the tail after the match.
                                arr[ei] = PdfString(
                                    codec.encode(work[end_pos:est_end])[0]
                                )

                        # Re-derive state from the mutated array instead of
                        # delta-shifting bookkeeping (the old code's shift
                        # produced negative starts and corrupted subsequent
                        # matches — the array is the source of truth).
                        str_parts, work = _decode_array()
                        search_start = pos + len(new_text)
                        applied[old] += 1
                        ops_mutated += 1
                        page_changed = True
                        tj_mutated = True

                if tj_mutated:
                    instructions[instr_idx] = pikepdf.ContentStreamInstruction(
                        list(operands), operator
                    )

        if page_changed:
            try:
                page.Contents = pdf.make_stream(unparse_content_stream(instructions))
            except Exception as e:
                page_errors.append(
                    {"page": page_no, "error": "write-back failed: %s" % e}
                )

    pdf.save(output_path)
    pdf.close()

    not_found = [old for old, was_matched in matched.items() if not was_matched]
    result = {
        "replacements_made": ops_mutated,
        "applied": {old: count for old, count in applied.items() if count},
        "not_found": not_found,
        "missing_glyphs": _dedupe_missing(missing_glyphs),
        "fonts_skipped": fonts_skipped,
        "page_errors": page_errors,
    }

    if verify and ops_mutated > 0:
        from verify import verify_pdf, VerificationError

        applied_reps = {
            old: replacements[old] for old, count in applied.items() if count > 0
        }
        report = verify_pdf(input_path, output_path, applied_reps, dpi=dpi)
        result["verification"] = report.to_dict()
        if not report.ok:
            raise VerificationError(report, result)

    if ops_mutated == 0:
        reasons = []
        if not_found:
            reasons.append("text not found: %s" % ", ".join(repr(t) for t in not_found))
        if result["missing_glyphs"]:
            chars = sorted(
                {c for m in result["missing_glyphs"] for c in m["missing_chars"]}
            )
            reasons.append("glyphs missing from embedded fonts: %s" % " ".join(chars))
        if fonts_skipped:
            reasons.append("fonts could not be decoded: %s" % "; ".join(fonts_skipped.values()))
        raise ValueError(
            "No replacements made. "
            + ("; ".join(reasons) or "The text may use an unsupported encoding.")
        )

    return result


def _skipped_fonts(page, cache):
    """{font_name: reason} for fonts on this page that have no codec."""
    skipped = {}
    res = _page_resources(page)
    fonts = res.get("/Font") if res is not None else None
    if fonts is None:
        return skipped
    for fname, fobj in fonts.items():
        name = str(fname).lstrip("/")
        key = None
        try:
            key = fobj.objgen if fobj.is_indirect else None
        except Exception:
            key = None
        if key is not None and key in cache:
            codec, reason = cache[key]
        else:
            codec, reason = build_codec(fobj)
            if key is not None:
                cache[key] = (codec, reason)
        if codec is None:
            skipped[name] = reason
    return skipped


def _dedupe_missing(entries):
    seen = set()
    out = []
    for e in entries:
        key = (e["old"], e["new"], tuple(e["missing_chars"]))
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out
