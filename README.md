# PDF Content Stream Editor

Edit text in PDFs with **zero format change** — edits raw PDF content stream operators (Tj/TJ) directly, never redraws anything.

## Philosophy: tools, not guarantees

This is a **library for PDF editing, not a layout engine**. We provide
primitives and diagnostics; **you decide what to do with them**.

- We do **not** promise an edit won't cause overlaps, reflow artifacts, or
  layout breakage — PDFs are final-form drawing programs and real documents
  use coordinate transforms (flips, scales) this library does not hide.
- We **do** promise honest behavior: no silent data loss (missing glyphs are
  reported, never dropped), structured machine-readable diagnostics for
  everything, and refusal only where the alternative is corruption.
- When an edit *does* break something, the toolkit gives you the primitives
  to **detect and fix it yourself**:
  - `find_overlaps(path)` — text collisions after an edit
  - `shift_region(...)` — move positioned runs by a delta (the fix for
    overprints)
  - `inspect_pdf(path)` — geometry, coordinate space, fonts, line map
  - `verify_pdf(...)` — render/text/structure report (a tool, not a gate)
  - `insert_experience_block(...)` — formatted block insertion (role title,
    dates, bullets) with measured layout, section keep-together, page
    overflow, and embedded fallback fonts for glyphs the subsets lack
  - after any edit, chain the tools: `find_overlaps` → `shift_region` →
    `find_overlaps` again — detect, fix, re-detect

Worked example (real resume, xetex 0.75-scaled flipped space): inserting a
new role left the old role overprinted; `find_overlaps` reported 30
collisions; `shift_region(text_any=[...], dy=-94.63)` moved the old block
down; `find_overlaps` then reported 0, and the section that no longer fit
had been moved to page 2.

Fit policies (`fit=refuse|shift|reflow`) exist so you can pick how
aggressive an edit should be per call — including "just do it and let me
inspect".

## How it works

PDFs store text as drawing instructions: the `Tj` and `TJ` operators in each page's content stream. Text is encoded as glyph IDs (CIDs) that map to shapes in embedded subset fonts. Each font has a **ToUnicode CMap** that maps CIDs back to Unicode.

This tool:
1. Builds a **codec per font** — Type0/CID fonts via ToUnicode CMaps (variable code lengths, ligature and surrogate destinations), simple fonts via `/Encoding` (WinAnsi/Standard/MacRoman) + `/Differences`, or the font's own ToUnicode
2. Walks the content stream tracking the active font (Tf operator)
3. Decodes text to Unicode for comparison
4. Checks glyph coverage **before** mutating — replacements needing characters the font lacks are refused and reported, never silently truncated
5. Re-encodes the replacement and writes the modified stream back

**No redrawing. No font substitution. No reflow.** The output is rendering-identical to the original except for the edited text. (Untouched PDF *objects* are preserved by qpdf; content streams are re-serialized, so byte-level identity is not claimed.)

## Verification harness

Every edit can be verified (`edit_pdf(..., verify=True)`) with four complementary checks — each catches a failure class the others are blind to:

| Check | Catches |
|---|---|
| `structure` — reopen + pages/boxes/fonts/annots | corrupt or restructured output |
| `stream_diff` — parsed operators identical except text-show strings | accidental operator corruption |
| `text_extract` — extraction equals original + replacements | **silent glyph loss**, mojibake |
| `pixel_diff` — zero-threshold diff outside edited-line masks, at 150+300 dpi | anything else changed on the page |

The report is machine-readable JSON (`result["verification"]`); failed verification raises `VerificationError` and the HTTP API returns **422 with the report** instead of serving the output.

```python
from pdf_editor import edit_pdf

result = edit_pdf("in.pdf", "out.pdf", {"SANKET": "Sachin"}, verify=True)
print(result["verification"]["ok"])       # True
print(result["missing_glyphs"])           # refusals, with the missing characters
```

Standalone: `from verify import verify_pdf; verify_pdf(original, edited, applied_replacements)`.

## Golden corpus

`corpus/` contains deterministic fixtures (per-character TJ kerning arrays, cross-operator splits, multi-font pages, `/Differences` overrides, simple-font ToUnicode, limited-glyph fonts) plus **golden verification reports** in `corpus/goldens/`. `pytest` replays every fixture and compares reports byte-for-byte — any behavior change shows up in review. `F3_cross_operator` documents a known Phase 3 limitation: text split across two operators is reported as not-found, honestly, instead of half-edited.

Rebuild fixtures (needs Windows system fonts; tests do not): `python corpus/build_corpus.py`. Regenerate goldens after intentional behavior changes: `python corpus/make_goldens.py`.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Python library

```python
from pdf_editor import edit_pdf

edit_pdf("resume.pdf", "edited.pdf", {
    "SANKET": "Sachin",
    "sanketdkumar@gmail.com": "sachindkumar@gmail.com",
})
```

Result dict: `replacements_made`, `applied`, `not_found`, `missing_glyphs`, `fonts_skipped`, `page_errors`, and `verification` when `verify=True`.

### Web server

```bash
python server.py
# Open http://localhost:8000
```

### API

```
POST /api/edit[?verify=0|1|json]
Content-Type: multipart/form-data
Fields: file (PDF, max 25 MB), replacements (JSON string)

Response: application/pdf (or the verification JSON with ?verify=json)
Headers: X-Replacements-Made, X-Verification, X-Missing-Glyphs
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Phase 3: text model, fit policy, limited reflow

Edits run through a positioned token model (`textmodel.py`): text is
decoded across operator boundaries into lines with real x/y positions
(full state tracking: CTM, Tm/Td/TD/T*, Tc/Tw/Tz/Ts, Tf).

- **Cross-operator matching** — a target fragmented across separate
  operators on one line is matched and replaced (occurrence identity via
  `find_pdf(path, query)`).
- **Width measurement + run shifting** (`widths.py`) — glyph advances from
  /Widths, /W (+/DW default), embedded hmtx/CFF via fontTools, or MuPDF's
  base-14 metrics. When a replacement widens the line, same-flow
  positioned runs after the edit shift by exactly the measured delta.
- **Fit policy with limited reflow** — soft boundary = the page's original
  text extent; a widening edit first moves trailing words to the
  continuation line (a real re-wrap); growth up to the hard page edge is
  allowed; beyond it the edit is **refused with DOES_NOT_FIT** — refusal
  is a first-class outcome, never silent overflow.
- **Form XObjects and annotation /AP streams** are traversed and editable.

## Limitations

- **Subset fonts** — replacement characters must exist in the font's glyph set; refusals name the absent characters in `missing_glyphs`.
- **Word-order verification** — reflowed edits are verified by word multiset (order changes across line wraps by design); non-reflowed edits verify exact extraction.
- **Reflow is line-scoped** — moved words go to the immediate continuation line only; multi-line cascading reflow is out of scope.
- **Non-standard encodings** — fonts with no ToUnicode and no supported `/Encoding` are refused and listed in `fonts_skipped`.
- **MacRomanEncoding** is decoded via the classic Mac OS Roman table; a few high slots may differ from the PDF spec variant.

## License

MIT
