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


def edit_pdf(input_path: str, output_path: str, replacements: dict) -> dict:
    """
    Edit text in a PDF by modifying content stream operators directly.

    Args:
        input_path: Path to input PDF
        output_path: Path for output PDF
        replacements: Dict of {old_text: new_text} to replace

    Returns:
        dict with 'replacements_made' count and any 'missing_glyphs'

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

        for operands, operator in instructions:
            op = str(operator)

            if op == "Tf" and len(operands) >= 1:
                current_font = str(operands[0]).lstrip("/")
                continue

            if current_font not in font_maps:
                continue

            maps = font_maps[current_font]
            cid2uni = maps["cid2uni"]
            uni2cid = maps["uni2cid"]

            # ── Tj: simple string ──
            if op == "Tj" and len(operands) == 1:
                decoded = _decode_cid(bytes(operands[0]), cid2uni)
                new_decoded = decoded
                for old, new in replacements.items():
                    new_decoded = new_decoded.replace(old, new)
                if new_decoded != decoded:
                    encoded = _encode_cid(new_decoded, uni2cid)
                    if encoded:
                        operands[0] = pikepdf.String(encoded)
                        total += 1

            # ── TJ: array with kerning offsets ──
            elif op == "TJ" and len(operands) == 1:
                arr = operands[0]
                if not isinstance(arr, pikepdf.Array):
                    continue

                # Decode all string elements
                str_parts = []
                full_text = ""
                for elem_idx, elem in enumerate(arr):
                    if isinstance(elem, pikepdf.String):
                        decoded = _decode_cid(bytes(elem), cid2uni)
                        str_parts.append((elem_idx, decoded, len(full_text)))
                        full_text += decoded

                # Apply replacements
                for old, new in replacements.items():
                    search_start = 0
                    while True:
                        pos = full_text.find(old, search_start)
                        if pos == -1:
                            break

                        end_pos = pos + len(old)
                        first_elem = None
                        for ei, edec, estart in str_parts:
                            if estart <= pos < estart + len(edec):
                                first_elem = (ei, estart, len(edec))
                                break
                        if not first_elem:
                            break

                        first_ei, first_start, first_len = first_elem
                        prefix = full_text[first_start:pos]
                        new_first_text = prefix + new

                        if all(ch in uni2cid for ch in new_first_text):
                            arr[first_ei] = pikepdf.String(
                                _encode_cid(new_first_text, uni2cid)
                            )
                            for ei, edec, estart in str_parts:
                                if estart > first_start and estart < end_pos:
                                    arr[ei] = pikepdf.String(b"")
                                elif estart >= end_pos:
                                    break
                            total += 1

                        # Update full_text for subsequent searches
                        full_text = full_text[:pos] + new + full_text[end_pos:]
                        delta = len(new) - len(old)
                        new_parts = []
                        for ei, edec, estart in str_parts:
                            offset = delta if estart >= pos else 0
                            new_parts.append((ei, edec, estart + offset))
                        str_parts = new_parts
                        search_start = pos + len(new)

        # Write modified stream back
        try:
            page.Contents = pdf.make_stream(unparse_content_stream(instructions))
        except Exception:
            pass

    pdf.save(output_path)
    pdf.close()

    if total == 0:
        raise ValueError(
            "No replacements made. The target text may be split across "
            "operators or uses an encoding this tool cannot parse."
        )

    return {"replacements_made": total}
