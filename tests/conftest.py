import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")


@pytest.fixture
def corpus_dir():
    return CORPUS


@pytest.fixture
def manifest():
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def corpus_file():
    def _get(name):
        return os.path.join(CORPUS, name)

    return _get


def _esc(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _tounicode_map(pdf):
    """ToUnicode CMap covering printable ASCII + the bullet (0x95).

    MuPDF cannot map WinAnsi 0x95 back to U+2022 for bare base14 fonts,
    so both the codec layer and text extraction go through this map."""
    rows = ["<%02X> <%04X>" % (code, code) for code in range(0x20, 0x7F)]
    rows.append("<95> <2022>")  # bullet
    body = "%d beginbfchar\n%s\nendbfchar" % (len(rows), "\n".join(rows))
    cmap = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n/CMapName /FMap def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        + body + "\nendcmap\n"
        "CMapName currentdict /CMap defineresource pop\nend\nend\n")
    return pdf.make_stream(cmap.encode("latin-1"))


def build_synthetic_resume(path):
    """Build a resume-like single-page PDF (Type1 base14 fonts, WinAnsi).

    Structure mirrors what insertblock measures from real documents:
    role titles in one font (F1), section headers in another (F2), bullet
    lines carrying three fonts (bullet F3 / bold lead F2 / body F1), a
    vector rule under the first bullet (raw ops the insertion must move),
    and a bottom section close enough to the margin to overflow when a
    large block is inserted.
    """
    import pikepdf

    parts = []

    def text(x, y, font, size, s):
        # font includes its leading slash ("/F1")
        parts.append("BT %s %.1f Tf %.2f %.2f Td (%s) Tj ET"
                     % (font, size, x, y, _esc(s)))

    text(33, 740, "/F1", 12, "SYNTH RESUME")
    text(33, 722, "/F1", 10, "Engineer for synth documents")
    text(33, 700, "/F2", 11, "EXPERIENCE")
    text(33, 680, "/F1", 10, "ACME Corp, Springfield")
    text(33, 650, "/F1", 10, "SOFTWARE ENGINEER")
    text(380, 650, "/F1", 9, "Jan 2023 - Present")

    text(33, 630, "/F3", 9, "\u2022 ")
    text(45, 630, "/F2", 9, "Led ")
    text(70, 630, "/F1", 9,
         "the platform team across many services and components")
    # vector rule under bullet 1: routes with bullet 1's text and must
    # shift with it (insertblock moves re/m/l raw operands too)
    parts.append("33 624 m 300 624 l S")

    text(33, 612, "/F3", 9, "\u2022 ")
    text(45, 612, "/F2", 9, "Built ")
    text(80, 612, "/F1", 9,
         "internal tooling used by hundreds of engineers daily")
    text(33, 594, "/F3", 9, "\u2022 ")
    text(45, 594, "/F2", 9, "Shipped ")
    text(90, 594, "/F1", 9,
         "the migration to the new runtime on schedule and under budget")

    text(33, 560, "/F1", 10, "SENIOR DEVELOPER")
    text(200, 542, "/F1", 9, "Feb 2024 - Present")
    text(33, 524, "/F3", 9, "\u2022 ")
    text(45, 524, "/F2", 9, "Led ")
    text(70, 524, "/F1", 9,
         "the rewrite of the legacy billing system to the new stack")
    text(33, 506, "/F3", 9, "\u2022 ")
    text(45, 506, "/F2", 9, "Mentored ")
    text(95, 506, "/F1", 9,
         "four junior engineers across two teams and three projects")

    text(33, 480, "/F2", 11, "PROJECTS")
    text(33, 460, "/F1", 9,
         "Built a full featured classroom platform with live video chat")
    text(33, 210, "/F2", 11, "EDUCATION")
    text(33, 190, "/F1", 9,
         "Institute of Technology, B.Tech in Computer Science Engineering")
    text(33, 170, "/F2", 11, "SKILLS")
    text(33, 150, "/F1", 9,
         "Golang, PHP, JavaScript, TypeScript, Rust, SQL and more skills")

    content = ("\n".join(parts) + "\n").encode("cp1252")
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = pdf.make_stream(content)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
        # note: pikepdf auto-prefixes kwarg keys with '/', so no leading slash
        F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf)),
        F2=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica-Bold"),
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf)),
        F3=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Courier,
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf)),
    ))
    pdf.save(path)
    pdf.close()


@pytest.fixture(scope="session")
def synth_resume(tmp_path_factory):
    path = tmp_path_factory.mktemp("synth") / "synth_resume.pdf"
    build_synthetic_resume(str(path))
    return str(path)
