"""Integration tests for the verification harness itself."""
import json

import pikepdf
import pytest
from pikepdf import parse_content_stream, unparse_content_stream

from cmap import parse_tounicode
from pdf_editor import edit_pdf
from verify import verify_pdf, VerificationError, VerificationReport


REPS = {"SANKET DAWAR": "SACHIN KUMAR"}


def _f0_cmap(pdf):
    return parse_tounicode(
        pdf.pages[0].Resources.Font["/F0"].ToUnicode.read_bytes()
    )


def test_legit_edit_passes_all_checks(corpus_file, tmp_path):
    src = corpus_file("F1_basic_tounicode.pdf")
    out = str(tmp_path / "out.pdf")
    edit_pdf(src, out, REPS)
    report = verify_pdf(src, out, REPS)
    assert report.ok, report.to_dict()
    names = [c.name for c in report.checks]
    assert names == ["applicability", "structure", "stream_diff",
                     "text_extract", "pixel_diff"]


def test_tampered_non_text_operator_fails_stream_check(corpus_file, tmp_path):
    """Move an unrelated positioning operator -> stream_diff must fail."""
    src = corpus_file("F1_basic_tounicode.pdf")
    out = str(tmp_path / "out.pdf")
    edit_pdf(src, out, REPS)

    pdf = pikepdf.open(out)
    instructions = parse_content_stream(pdf.pages[0])
    for idx, ins in enumerate(instructions):
        operands, operator = ins.operands, ins.operator
        if str(operator) == "Tm":
            new_operands = list(operands)
            new_operands[4] = int(new_operands[4]) + 3
            instructions[idx] = pikepdf.ContentStreamInstruction(
                new_operands, operator
            )
            break
    pdf.pages[0].Contents = pdf.make_stream(unparse_content_stream(instructions))
    tampered = str(tmp_path / "tampered.pdf")
    pdf.save(tampered)

    report = verify_pdf(src, tampered, REPS)
    assert not report.ok
    failed = {c.name for c in report.checks if not c.passed}
    assert "stream_diff" in failed


def test_unrelated_text_change_fails_pixel_check(corpus_file, tmp_path):
    """Change text on a line that had NO replacement -> the change sits
    outside every mask, so pixel_diff must fail."""
    src = corpus_file("F1_basic_tounicode.pdf")
    out = str(tmp_path / "out.pdf")
    edit_pdf(src, out, REPS)

    pdf = pikepdf.open(out)
    cmap = _f0_cmap(pdf)
    rev = cmap.reverse_map()
    instructions = parse_content_stream(pdf.pages[0])
    for idx, ins in enumerate(instructions):
        operands, operator = ins.operands, ins.operator
        if str(operator) != "TJ":
            continue
        new_arr = pikepdf.Array(list(operands[0]))
        changed = False
        for j, e in enumerate(new_arr):
            if isinstance(e, pikepdf.String):
                text = cmap.decode(bytes(e))
                if "quick" in text:
                    new_arr[j] = pikepdf.String(
                        b"".join(rev[c] for c in text.replace("quick", "quack"))
                    )
                    changed = True
        if changed:
            instructions[idx] = pikepdf.ContentStreamInstruction(
                [new_arr], operator
            )
            break
    assert changed
    pdf.pages[0].Contents = pdf.make_stream(unparse_content_stream(instructions))
    tampered = str(tmp_path / "tampered2.pdf")
    pdf.save(tampered)

    report = verify_pdf(src, tampered, REPS)
    assert not report.ok
    failed = {c.name for c in report.checks if not c.passed}
    assert "pixel_diff" in failed


def test_missing_glyph_edit_cannot_pass_verification(corpus_file, tmp_path):
    """A replacement that would silently lose glyphs is refused outright —
    there is no output for the harness to (wrongly) bless."""
    src = corpus_file("F7_limited_tounicode.pdf")
    out = str(tmp_path / "out.pdf")
    with pytest.raises(ValueError):
        edit_pdf(src, out, {"SANKET": "SACHIN"}, verify=True)


def test_edit_pdf_verify_true_embeds_report(corpus_file, tmp_path):
    out = str(tmp_path / "out.pdf")
    result = edit_pdf(
        corpus_file("F6_simple_tounicode.pdf"), str(out),
        {"pending": "done"}, verify=True,
    )
    report = result["verification"]
    assert report["ok"] is True
    json.dumps(report)  # must be JSON-serializable
    assert {c["name"] for c in report["checks"]} == {
        "applicability", "structure", "stream_diff", "text_extract", "pixel_diff",
    }


def test_verify_error_raised_on_failure(corpus_file, tmp_path, monkeypatch):
    """If the harness rejects the output, edit_pdf raises VerificationError."""
    import verify as verify_module

    src = corpus_file("F6_simple_tounicode.pdf")
    out = str(tmp_path / "out.pdf")

    real_verify = verify_module.verify_pdf

    def failing_verify(*args, **kwargs):
        report = real_verify(*args, **kwargs)
        report.checks[-1].passed = False  # sabotage the pixel check
        return report

    monkeypatch.setattr(verify_module, "verify_pdf", failing_verify)
    with pytest.raises(VerificationError) as exc:
        edit_pdf(src, out, {"pending": "done"}, verify=True)
    assert not exc.value.report.ok
    assert exc.value.context["replacements_made"] > 0


def test_verify_without_applied_replacements_fails(tmp_path):
    src = str(tmp_path / "a.pdf")
    out = str(tmp_path / "b.pdf")
    report = verify_pdf(src, out, {})
    assert not report.ok
    assert report.checks[0].name == "applicability"


def test_report_dataclass_basics():
    r = VerificationReport()
    assert r.ok is True
    assert r.to_dict() == {"ok": True, "checks": [], "warnings": []}
