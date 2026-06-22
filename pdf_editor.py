"""
PDF Content Stream Editor
Edits PDF text by modifying raw content stream operators (Tj/TJ).
Parses font ToUnicode CMaps to decode CID-encoded text.
Zero format change — only text bytes are modified.

Usage:
    from pdf_editor import edit_pdf

    edit_pdf("input.pdf", "output.pdf", {"old text": "new text"})
"""
import re
import pikepdf
from pikepdf import parse_content_stream, unparse_content_stream


def _parse_tounicode_cmap(stream_bytes: bytes) -> dict:
    """Parse a PDF ToUnicode CMap stream → {CID: unicode_char}"""
    text = stream_bytes.decode("latin-1", errors="replace")
    cid2uni = {}

    for m in re.finditer(r"beginbfchar\s*(.*?)\s*endbfchar", text, re.DOTALL):
        for pair in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            cid = int(pair.group(1), 16)
            uni = int(pair.group(2), 16)
            if uni < 0x110000:
                cid2uni[cid] = chr(uni)

    for m in re.finditer(r"beginbfrange\s*(.*?)\s*endbfrange", text, re.DOTALL):
        for rng in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            start = int(rng.group(1), 16)
            end = int(rng.group(2), 16)
            base = int(rng.group(3), 16)
            for cid in range(start, end + 1):
                target = base + (cid - start)
                if target < 0x110000:
                    cid2uni[cid] = chr(target)
    return cid2uni


def _extract_font_maps(pdf, page) -> dict:
    """Extract {font_name: {'cid2uni': ..., 'uni2cid': ...}} for a page."""
    fonts = {}
    resources = page.get("/Resources", {})
    font_dict = resources.get("/Font", {}) if resources else {}
    if not font_dict:
        return fonts

    for fname, fobj in font_dict.items():
        name = str(fname).lstrip("/")
        to_unicode = fobj.get("/ToUnicode")
        if not to_unicode:
            continue
        try:
            cid2uni = _parse_tounicode_cmap(to_unicode.read_bytes())
            fonts[name] = {
                "cid2uni": cid2uni,
                "uni2cid": {v: k for k, v in cid2uni.items()},
            }
        except Exception:
            pass
    return fonts


def _decode_cid(raw_bytes: bytes, cid2uni: dict) -> str:
    """Decode 2-byte CID string to Unicode."""
    result = []
    for i in range(0, len(raw_bytes), 2):
        if i + 1 < len(raw_bytes):
            cid = (raw_bytes[i] << 8) | raw_bytes[i + 1]
            result.append(cid2uni.get(cid, ""))
    return "".join(result)


def _encode_cid(text: str, uni2cid: dict) -> bytes:
    """Encode Unicode to 2-byte CID bytes."""
    result = bytearray()
    for ch in text:
        cid = uni2cid.get(ch)
        if cid is not None:
            result.append((cid >> 8) & 0xFF)
            result.append(cid & 0xFF)
    return bytes(result)


_Y_EPS = 1e-3


def _slots_full_text(slots: list) -> tuple:
    """Concatenate slot texts → (full_text, offsets) where offsets[i] is the
    starting index of slot i within full_text."""
    offsets = []
    pos = 0
    for slot in slots:
        offsets.append(pos)
        pos += len(slot["text"])
    return "".join(slot["text"] for slot in slots), offsets


def _slot_at(slots: list, offsets: list, char_pos: int):
    """Return the index of the slot containing absolute char position."""
    for idx, slot in enumerate(slots):
        start = offsets[idx]
        if start <= char_pos < start + len(slot["text"]):
            return idx
    return None


def _apply_to_slots(slots: list, old: str, new: str, uni2cid: dict) -> int:
    """Replace ``old`` with ``new`` across the concatenated text of ``slots``.

    The match may span multiple slots (i.e. multiple Tj/TJ operands). The whole
    replacement is written into the slot holding the match start; intermediate
    slots are cleared; the tail of the slot holding the match end is preserved.
    Slots whose text changes are flagged ``modified`` so only those operands are
    rewritten. Returns the number of replacements made.
    """
    if not old:
        return 0
    if any(ch not in uni2cid for ch in new):
        # Replacement contains a glyph the subset font cannot encode.
        return 0

    count = 0
    search_start = 0
    while True:
        full_text, offsets = _slots_full_text(slots)
        pos = full_text.find(old, search_start)
        if pos == -1:
            break

        end_pos = pos + len(old)
        i = _slot_at(slots, offsets, pos)
        j = _slot_at(slots, offsets, end_pos - 1)
        if i is None or j is None:
            search_start = pos + 1
            continue

        prefix = slots[i]["text"][: pos - offsets[i]]
        suffix = slots[j]["text"][end_pos - offsets[j]:]

        if i == j:
            slots[i]["text"] = prefix + new + suffix
            slots[i]["modified"] = True
        else:
            slots[i]["text"] = prefix + new
            slots[i]["modified"] = True
            for k in range(i + 1, j):
                if slots[k]["text"]:
                    slots[k]["text"] = ""
                    slots[k]["modified"] = True
            slots[j]["text"] = suffix
            slots[j]["modified"] = True

        count += 1
        search_start = pos + len(new)

    return count


def edit_pdf(input_path: str, output_path: str, replacements: dict) -> dict:
    """
    Edit text in a PDF by modifying content stream operators directly.

    Text is matched across consecutive ``Tj``/``TJ`` operators that belong to
    the same text block. Consecutive text-show operators are accumulated while
    the font and text line position stay the same; the block is flushed (its
    accumulated text searched and rewritten) when the font changes, when a
    non-text operator appears, or when text positioning moves to a new line or
    paragraph.

    Args:
        input_path: Path to input PDF
        output_path: Path for output PDF
        replacements: Dict of {old_text: new_text} to replace

    Returns:
        dict with 'replacements_made' count

    Raises:
        ValueError: If no replacements were made (text not found or encoding issue)
    """
    pdf = pikepdf.open(input_path)
    total = 0

    for page in pdf.pages:
        font_maps = _extract_font_maps(pdf, page)
        if not font_maps:
            continue

        try:
            instructions = parse_content_stream(page)
        except Exception:
            continue

        current_font = None
        block_slots: list = []
        block_uni2cid: dict = {}
        block_y = None  # text-line y at which the current block started
        current_y = 0.0

        def flush():
            nonlocal total, block_slots, block_y
            if block_slots:
                for old, new in replacements.items():
                    total += _apply_to_slots(block_slots, old, new, block_uni2cid)
                # Rebuild only the operators whose text actually changed; every
                # untouched instruction is left byte-for-byte identical.
                tj_changes: dict = {}
                tj_array_changes: dict = {}
                for slot in block_slots:
                    if not slot["modified"]:
                        continue
                    encoded = _encode_cid(slot["text"], block_uni2cid)
                    if slot["kind"] == "Tj":
                        tj_changes[slot["op_index"]] = encoded
                    else:
                        tj_array_changes.setdefault(slot["op_index"], {})[
                            slot["arr_index"]
                        ] = encoded
                for op_index, encoded in tj_changes.items():
                    instructions[op_index] = pikepdf.ContentStreamInstruction(
                        [pikepdf.String(encoded)], pikepdf.Operator("Tj")
                    )
                for op_index, changes in tj_array_changes.items():
                    original = instructions[op_index].operands[0]
                    new_elems = [
                        pikepdf.String(changes[ei]) if ei in changes else elem
                        for ei, elem in enumerate(original)
                    ]
                    instructions[op_index] = pikepdf.ContentStreamInstruction(
                        [pikepdf.Array(new_elems)], pikepdf.Operator("TJ")
                    )
            block_slots = []
            block_y = None

        for op_index, (operands, operator) in enumerate(instructions):
            op = str(operator)

            # ── Text block / line positioning state ──
            if op == "BT":
                flush()
                current_y = 0.0
                continue
            if op == "ET":
                flush()
                continue
            if op == "Tf":
                flush()
                if len(operands) >= 1:
                    current_font = str(operands[0]).lstrip("/")
                continue
            if op in ("Td", "TD"):
                if len(operands) >= 2:
                    current_y += float(operands[1])
                if block_slots and block_y is not None and abs(current_y - block_y) > _Y_EPS:
                    flush()
                continue
            if op == "Tm":
                if len(operands) >= 6:
                    current_y = float(operands[5])
                    skewed = abs(float(operands[1])) > _Y_EPS or abs(float(operands[2])) > _Y_EPS
                else:
                    skewed = False
                if block_slots and (skewed or block_y is None or abs(current_y - block_y) > _Y_EPS):
                    flush()
                continue

            # ── Text-show operators: accumulate into the current block ──
            if op == "Tj" and len(operands) == 1:
                if current_font not in font_maps:
                    flush()
                    continue
                maps = font_maps[current_font]
                if not block_slots:
                    block_uni2cid = maps["uni2cid"]
                    block_y = current_y
                block_slots.append({
                    "kind": "Tj",
                    "op_index": op_index,
                    "text": _decode_cid(bytes(operands[0]), maps["cid2uni"]),
                    "modified": False,
                })
                continue

            if op == "TJ" and len(operands) == 1:
                arr = operands[0]
                if current_font not in font_maps or not isinstance(arr, pikepdf.Array):
                    flush()
                    continue
                maps = font_maps[current_font]
                if not block_slots:
                    block_uni2cid = maps["uni2cid"]
                    block_y = current_y
                for elem_idx, elem in enumerate(arr):
                    if isinstance(elem, pikepdf.String):
                        block_slots.append({
                            "kind": "TJ",
                            "op_index": op_index,
                            "arr_index": elem_idx,
                            "text": _decode_cid(bytes(elem), maps["cid2uni"]),
                            "modified": False,
                        })
                continue

            # ── Any other operator ends the current text block ──
            flush()

        flush()

        # Write modified stream back
        try:
            page.Contents = pdf.make_stream(unparse_content_stream(instructions))
        except Exception:
            pass

    pdf.save(output_path)
    pdf.close()

    if total == 0:
        raise ValueError(
            "No replacements made. The target text may span a layout change "
            "(e.g. a new line or font switch) or uses an encoding this tool "
            "cannot parse."
        )

    return {"replacements_made": total}
