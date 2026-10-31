"""
Build the golden corpus fixtures.

Run once (committed artifacts; CI never rebuilds):

    python corpus/build_corpus.py

Fixtures exercise the fragmentation/encoding patterns from the architecture
doc. Requires Windows system fonts (arial.ttf / times.ttf) — only needed to
BUILD, never to RUN the tests (PDFs are committed).
"""
import json
import os

import fitz
import pikepdf
from pikepdf import Array, Dictionary, Name, String as PdfString
from pikepdf import parse_content_stream, unparse_content_stream

from cmap import parse_tounicode

HERE = os.path.dirname(os.path.abspath(__file__))
ARIAL = r"C:\Windows\Fonts\arial.ttf"
TIMES = r"C:\Windows\Fonts\times.ttf"


def make_fitz_doc(path, fonts, lines, subset=False):
    """Type0/Identity-H docs via PyMuPDF (writes real ToUnicode CMaps)."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for name, file in fonts:
        page.insert_font(fontname=name, fontfile=file)
    y = 100
    for fontname, text, size in lines:
        page.insert_text((72, y), text, fontname=fontname, fontsize=size)
        y += size + 14
    if subset:
        doc.subset_fonts()
    doc.save(path)
    doc.close()


def _font0_cmap(pdf):
    page = pdf.pages[0]
    fonts = page.Resources.Font
    for name, f in fonts.items():
        if f.get("/ToUnicode") is not None:
            return str(name).lstrip("/"), parse_tounicode(f.ToUnicode.read_bytes())
    raise RuntimeError("no font with ToUnicode found")


def fragment_tj(src, out, target, mode):
    """Rewrite the first Tj containing `target` into fragmented operators.

    mode='perchar': one TJ array with per-character strings + kerning offsets
                    (the Word/WeasyPrint pattern).
    mode='split':   two adjacent Tj operators splitting the text mid-word
                    (the cross-operator pattern the editor cannot match yet —
                    Phase 3 work; the corpus documents the limitation).
    """
    pdf = pikepdf.open(src)
    fontname, cmap = _font0_cmap(pdf)
    rev = cmap.reverse_map()

    def enc(s):
        return b"".join(rev[ch] for ch in s)

    page = pdf.pages[0]
    instrs = parse_content_stream(page)
    current = None
    new_instrs = []
    done = False
    for operands, operator in instrs:
        op = str(operator)
        if op == "Tf" and operands:
            current = str(operands[0]).lstrip("/")
            new_instrs.append((operands, operator))
            continue
        if not done and current == fontname:
            if op == "Tj" and isinstance(operands[0], pikepdf.String):
                text = cmap.decode(bytes(operands[0]))
            elif op == "TJ" and isinstance(operands[0], pikepdf.Array):
                text = "".join(
                    cmap.decode(bytes(e))
                    for e in operands[0]
                    if isinstance(e, pikepdf.String)
                )
            else:
                text = ""
            if target in text:
                if mode == "perchar":
                    arr = []
                    for i, ch in enumerate(text):
                        if i:
                            arr.append(8)  # small kerning offset
                        arr.append(PdfString(enc(ch)))
                    new_instrs.append(([arr], pikepdf.Operator("TJ")))
                else:  # split
                    i = text.index(target)
                    mid = i + len(target) // 2
                    new_instrs.append(([PdfString(enc(text[:mid]))], operator))
                    new_instrs.append(([PdfString(enc(text[mid:]))], operator))
                done = True
                continue
        new_instrs.append((operands, operator))
    if not done:
        raise RuntimeError("target not found in %s" % src)
    page.Contents = pdf.make_stream(unparse_content_stream(new_instrs))
    pdf.save(out)
    pdf.close()


SIMPLE_TOUNICODE = """%% a minimal 1-byte ToUnicode CMap
/CIDInit /ProcSet findresource begin
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfrange
<20> <7E> <0020>
endbfrange
end
"""

# ToUnicode that deliberately maps only the characters of "SANKET" + space.
LIMITED_TOUNICODE = """%% limited ToUnicode
/CIDInit /ProcSet findresource begin
1 begincodespacerange
<00> <FF>
endcodespacerange
7 beginbfchar
<20> <0020>
<41> <0041>
<45> <0045>
<4B> <004B>
<4E> <004E>
<53> <0053>
<54> <0054>
endbfchar
end
"""


def make_simple_font_pdf(path, lines, encoding_name=None, differences=None,
                         tounicode=None):
    """Hand-crafted base-14 Helvetica doc: no embedded font file needed.

    MuPDF renders base-14 from its built-ins, so these fixtures run on any
    machine. Exercises the simple-font codec paths (Phase 2).
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    fd = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    if encoding_name:
        fd.Encoding = Name("/" + encoding_name)
    if differences:
        arr = Array(
            [int(x) if isinstance(x, int) else Name("/" + x) for x in differences]
        )
        fd.Encoding = Dictionary(BaseEncoding=Name.WinAnsiEncoding, Differences=arr)
    if tounicode:
        fd.ToUnicode = pdf.make_stream(tounicode.encode("latin-1"))
    page.Resources = Dictionary(Font=Dictionary(F1=fd))
    chunks = []
    y = 700
    for text in lines:
        chunks.append(
            "BT /F1 12 Tf 72 %d Td (%s) Tj ET" % (y, text)
        )
        y -= 30
    page.Contents = pdf.make_stream("\n".join(chunks).encode("latin-1"))
    pdf.save(path)
    pdf.close()


def main():
    fixtures = {}

    # F1: Type0 + ToUnicode (PyMuPDF/Arial), simple Tj operators.
    f1 = os.path.join(HERE, "F1_basic_tounicode.pdf")
    make_fitz_doc(f1, [("F0", ARIAL)], [
        ("F0", "SANKET DAWAR", 18),
        ("F0", "alpha beta alpha gamma alpha", 12),
        ("F0", "URGENT: payroll report", 12),
        ("F0", "Draft proposal v2", 12),
        ("F0", "The quick brown fox jumps over the lazy dog", 12),
    ])
    fixtures["F1"] = f1

    # F2: same content, target rewritten as a per-character TJ array.
    f2 = os.path.join(HERE, "F2_perchar_tj.pdf")
    fragment_tj(f1, f2, "SANKET DAWAR", "perchar")
    fixtures["F2"] = f2

    # F3: target split across two Tj operators (known Phase 3 limitation).
    f3 = os.path.join(HERE, "F3_cross_operator.pdf")
    fragment_tj(f1, f3, "SANKET DAWAR", "split")
    fixtures["F3"] = f3

    # F4: two embedded fonts on one page.
    f4 = os.path.join(HERE, "F4_multifont.pdf")
    make_fitz_doc(f4, [("F0", ARIAL), ("F1", TIMES)], [
        ("F1", "Quarterly REPORT 2024", 14),
        ("F0", "SANKET DAWAR", 12),
        ("F0", "Draft proposal v2", 12),
        ("F1", "Confidential: internal use", 12),
    ])
    fixtures["F4"] = f4

    # F5: simple font + /Differences override (0x60 grave -> quoteleft).
    f5 = os.path.join(HERE, "F5_differences.pdf")
    make_simple_font_pdf(
        f5,
        ["Quote: ` end", "plain ascii line"],
        encoding_name="WinAnsiEncoding",
        differences=[96, "quoteleft"],
    )
    fixtures["F5"] = f5

    # F6: simple font WITH a 1-byte ToUnicode CMap.
    f6 = os.path.join(HERE, "F6_simple_tounicode.pdf")
    make_simple_font_pdf(
        f6,
        ["status: pending", "another pending item"],
        encoding_name="WinAnsiEncoding",
        tounicode=SIMPLE_TOUNICODE,
    )
    fixtures["F6"] = f6

    # F7: limited ToUnicode — replacement glyphs are provably absent.
    # The ToUnicode maps only the characters of "SANKET" plus space, so
    # replacements needing any other character must be refused.
    f7 = os.path.join(HERE, "F7_limited_tounicode.pdf")
    make_simple_font_pdf(
        f7,
        ["SANKET SANKET SANKET"],
        encoding_name="WinAnsiEncoding",
        tounicode=LIMITED_TOUNICODE,
    )
    fixtures["F7"] = f7

    manifest = {
        "_comment": "Golden corpus manifest. mode=ok entries have golden "
                    "verification reports in goldens/<name>.json.",
        "fixtures": [
            {"name": "f1_name", "file": "F1_basic_tounicode.pdf",
             "replacements": {"SANKET DAWAR": "SACHIN KUMAR"},
             "expect": {"mode": "ok"}},
            {"name": "f1_repeat", "file": "F1_basic_tounicode.pdf",
             "replacements": {"alpha": "beta"},
             "expect": {"mode": "ok"}},
            {"name": "f1_delete", "file": "F1_basic_tounicode.pdf",
             "replacements": {"URGENT: ": ""},
             "expect": {"mode": "ok"}},
            {"name": "f2_perchar", "file": "F2_perchar_tj.pdf",
             "replacements": {"SANKET DAWAR": "SACHIN KUMAR"},
             "expect": {"mode": "ok"}},
            {"name": "f3_crossop", "file": "F3_cross_operator.pdf",
             "replacements": {"SANKET DAWAR": "SACHIN KUMAR"},
             "expect": {"mode": "error", "message_contains": "not found"}},
            {"name": "f4_multifont", "file": "F4_multifont.pdf",
             "replacements": {"REPORT": "DOSSIER", "Draft": "FINAL"},
             "expect": {"mode": "ok"}},
            {"name": "f5_differences", "file": "F5_differences.pdf",
             "replacements": {"\u2018": "\u201c"},
             "expect": {"mode": "ok"}},
            {"name": "f6_simple_tounicode", "file": "F6_simple_tounicode.pdf",
             "replacements": {"pending": "done"},
             "expect": {"mode": "ok"}},
            {"name": "f7_limited_ok", "file": "F7_limited_tounicode.pdf",
             "replacements": {"SANKET": "TANKET"},
             "expect": {"mode": "ok"}},
            {"name": "f7_subset", "file": "F7_limited_tounicode.pdf",
             "replacements": {"SANKET": "SACHIN"},
             "expect": {"mode": "error", "message_contains": "missing"}},
        ],
    }
    with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print("corpus built:", ", ".join(os.path.basename(v) for v in fixtures.values()))


if __name__ == "__main__":
    main()
