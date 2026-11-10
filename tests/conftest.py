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
    """ToUnicode CMap covering printable ASCII + bullet (0x95) + en-dash
    (0x96).

    MuPDF cannot map WinAnsi high bytes back to their characters for bare
    base14 fonts, so both the codec layer and text extraction go through
    this map."""
    rows = ["<%02X> <%04X>" % (code, code) for code in range(0x20, 0x7F)]
    rows.append("<95> <2022>")  # bullet
    rows.append("<96> <2013>")  # en dash (the en-dash-marker fixture)
    body = "%d beginbfchar\n%s\nendbfchar" % (len(rows), "\n".join(rows))
    cmap = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n/CMapName /FMap def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        + body + "\nendcmap\n"
        "CMapName currentdict /CMap defineresource pop\nend\nend\n")
    return pdf.make_stream(cmap.encode("latin-1"))


def build_synthetic_resume(path, left_margin=33.0, single_font=False,
                           single_font_bullets=False,
                           bullet_marker="\u2022"):
    """Build a resume-like single-page PDF (Type1 base14 fonts, WinAnsi).

    Structure mirrors what insertblock measures from real documents:
    role titles in one font (F1), section headers in another (F2), bullet
    lines carrying three fonts (bullet F3 / bold lead F2 / body F1), a
    vector rule under the first bullet (raw ops the insertion must move),
    and a bottom section close enough to the margin to overflow when a
    large block is inserted.

    Variants cover the non-reference-template classes that shipped broken:
    left_margin (header detection was pinned to 33pt), single_font (one
    font for everything — bold exemplars were impossible),
    single_font_bullets (plain resumes: bullet lines carry no second
    font), and bullet_marker (hard-coded to U+2022).
    """
    import pikepdf

    lm = left_margin
    hdr = "/F1" if single_font else "/F2"
    bul = "/F1" if single_font or single_font_bullets else "/F3"
    lead = "/F1" if single_font or single_font_bullets else hdr
    parts = []

    def text(x, y, font, size, s):
        # font includes its leading slash ("/F1")
        parts.append("BT %s %.1f Tf %.2f %.2f Td (%s) Tj ET"
                     % (font, size, x, y, _esc(s)))

    text(lm, 740, "/F1", 12, "SYNTH RESUME")
    text(lm, 722, "/F1", 10, "Engineer for synth documents")
    text(lm, 700, hdr, 11, "EXPERIENCE")
    text(lm, 680, "/F1", 10, "ACME Corp, Springfield")
    text(lm, 650, "/F1", 10, "SOFTWARE ENGINEER")
    text(lm + 347, 650, "/F1", 9, "Jan 2023 - Present")

    text(lm, 630, bul, 9, bullet_marker + " ")
    text(lm + 12, 630, lead, 9, "Led ")
    text(lm + 37, 630, "/F1", 9,
         "the platform team across many services and components")
    # vector rule under bullet 1: routes with bullet 1's text and must
    # shift with it (insertblock moves re/m/l raw operands too)
    parts.append("%.2f 624 m %.2f 624 l S" % (lm, lm + 267))

    text(lm, 612, bul, 9, bullet_marker + " ")
    text(lm + 12, 612, lead, 9, "Built ")
    text(lm + 47, 612, "/F1", 9,
         "internal tooling used by hundreds of engineers daily")
    text(lm, 594, bul, 9, bullet_marker + " ")
    text(lm + 12, 594, lead, 9, "Shipped ")
    text(lm + 57, 594, "/F1", 9,
         "the migration to the new runtime on schedule and under budget")

    text(lm, 560, "/F1", 10, "SENIOR DEVELOPER")
    text(lm + 167, 542, "/F1", 9, "Feb 2024 - Present")
    text(lm, 524, bul, 9, bullet_marker + " ")
    text(lm + 12, 524, lead, 9, "Led ")
    text(lm + 37, 524, "/F1", 9,
         "the rewrite of the legacy billing system to the new stack")
    text(lm, 506, bul, 9, bullet_marker + " ")
    text(lm + 12, 506, lead, 9, "Mentored ")
    text(lm + 62, 506, "/F1", 9,
         "four junior engineers across two teams and three projects")

    text(lm, 480, hdr, 11, "PROJECTS")
    text(lm, 460, "/F1", 9,
         "Built a full featured classroom platform with live video chat")
    text(lm, 210, hdr, 11, "EDUCATION")
    text(lm, 190, "/F1", 9,
         "Institute of Technology, B.Tech in Computer Science Engineering")
    text(lm, 170, hdr, 11, "SKILLS")
    text(lm, 150, "/F1", 9,
         "Golang, PHP, JavaScript, TypeScript, Rust, SQL and more skills")

    content = ("\n".join(parts) + "\n").encode("cp1252")
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = pdf.make_stream(content)
    fonts = {
        # note: pikepdf auto-prefixes kwarg keys with '/', so no leading slash
        "F1": pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf)),
    }
    if not single_font:
        fonts["F2"] = pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica-Bold"),
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf))
        fonts["F3"] = pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Courier,
            Encoding=pikepdf.Name.WinAnsiEncoding,
            ToUnicode=_tounicode_map(pdf))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(**fonts))
    pdf.save(path)
    pdf.close()


@pytest.fixture(scope="session")
def synth_resume(tmp_path_factory):
    path = tmp_path_factory.mktemp("synth") / "synth_resume.pdf"
    build_synthetic_resume(str(path))
    return str(path)


@pytest.fixture(scope="session")
def synth_factory(tmp_path_factory):
    """Build synthetic resume variants on demand."""
    counter = [0]

    def _make(**kwargs):
        counter[0] += 1
        path = (tmp_path_factory.mktemp("synthvar") /
                ("synth_%d.pdf" % counter[0]))
        build_synthetic_resume(str(path), **kwargs)
        return str(path)

    return _make
