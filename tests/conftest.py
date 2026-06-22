"""Shared helpers for building synthetic content-stream PDFs.

These fixtures construct PDFs with full control over how visible text is split
across ``Tj``/``TJ`` operators, plus a ``/ToUnicode`` CMap so the editor can
decode the CID bytes. The font program itself is not embedded — the regression
tests only exercise the content-stream decode/replace/encode round-trip, not
visual rendering.
"""
import pikepdf

# CID encoding used by these fixtures: every character maps to the CID equal to
# its Unicode code point, stored as 2 big-endian bytes (Identity-H style).


def cid_hex(text: str) -> str:
    """Encode a string as a hex CID string body, e.g. 'Hi' -> '00480069'."""
    return "".join(f"{ord(ch):04X}" for ch in text)


def _build_cmap(chars: str) -> bytes:
    """Build a minimal ToUnicode CMap mapping CID(code point) -> Unicode."""
    uniq = sorted(set(chars))
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "1 begincodespacerange",
        "<0000> <ffff>",
        "endcodespacerange",
        f"{len(uniq)} beginbfchar",
    ]
    for ch in uniq:
        lines.append(f"<{ord(ch):04X}> <{ord(ch):04X}>")
    lines += ["endbfchar", "endcmap", "end", "end"]
    return ("\n".join(lines) + "\n").encode("latin-1")


def build_pdf(path, content_stream: str, alphabet: str, second_font_alphabet=None):
    """Create a one-page PDF with the given raw content stream.

    ``alphabet`` lists every character that font /F1's ToUnicode CMap should
    cover. If ``second_font_alphabet`` is given, a second font /F2 is added so
    content streams can switch fonts via ``/F2 ... Tf``.
    """
    pdf = pikepdf.new()

    def make_font(alpha):
        tounicode = pdf.make_stream(_build_cmap(alpha))
        return pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name("/TestFont"),
                Encoding=pikepdf.Name("/Identity-H"),
                ToUnicode=tounicode,
            )
        )

    font_dict = pikepdf.Dictionary(F1=make_font(alphabet))
    if second_font_alphabet is not None:
        font_dict.F2 = make_font(second_font_alphabet)

    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = pdf.make_stream(content_stream.encode("latin-1"))
    page.Resources = pikepdf.Dictionary(Font=font_dict)
    pdf.save(str(path))
    pdf.close()


def decode_page_text(path) -> str:
    """Decode the full visible text of a PDF's first page (concatenated)."""
    from pdf_editor import _extract_font_maps, _decode_cid
    from pikepdf import parse_content_stream

    pdf = pikepdf.open(str(path))
    try:
        page = pdf.pages[0]
        font_maps = _extract_font_maps(pdf, page)
        current_font = None
        out = []
        for operands, operator in parse_content_stream(page):
            op = str(operator)
            if op == "Tf" and len(operands) >= 1:
                current_font = str(operands[0]).lstrip("/")
                continue
            if current_font not in font_maps:
                continue
            cid2uni = font_maps[current_font]["cid2uni"]
            if op == "Tj" and len(operands) == 1:
                out.append(_decode_cid(bytes(operands[0]), cid2uni))
            elif op == "TJ" and len(operands) == 1:
                arr = operands[0]
                if isinstance(arr, pikepdf.Array):
                    for elem in arr:
                        if isinstance(elem, pikepdf.String):
                            out.append(_decode_cid(bytes(elem), cid2uni))
        return "".join(out)
    finally:
        pdf.close()
