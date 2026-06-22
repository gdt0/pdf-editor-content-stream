# The PDF Text Editing Problem — Gaps, Approaches, and Solutions

## 1. What was wanted

Edit text inside an existing PDF — change a name, fix a typo, update a date — and get back a PDF that looks **identical** to the original except for the changed words. Same fonts, same layout, same spacing, same everything. This sounds simple. It is not.

The fundamental reason: **PDF is a final-form presentation format, not an editable document.** A PDF doesn't store "paragraphs" or "sentences." It stores a program — a sequence of drawing instructions — that tells a renderer exactly where to place each glyph on a page.

This document explains why every obvious approach fails, and how editing the content stream directly solves the problem without touching anything else.

---

## 2. Why this is hard

A PDF page is not a text file. It's a content stream: a binary sequence of operators and operands that describe what to draw and where.

A typical text fragment in a PDF content stream looks like this:

```
/F1 12 Tf          ← select font F1 at 12pt
0 0 0 rg           ← set color to black
72 720 Td          ← move cursor to position (72, 720)
(Hello World) Tj   ← draw the string "Hello World"
```

But the actual text is almost never stored as readable ASCII. Real-world PDFs use **subset fonts** where each character is stored as a glyph ID (CID) — a numeric index into the font's glyph table. The mapping from CID to Unicode is recorded in a separate structure called a **ToUnicode CMap**.

A real PDF might look like:

```
/F1 12 Tf
<0027 0055 0011 0003 0024 004D 0058 0051> Tj
```

Where:
- Font F1 has a ToUnicode CMap mapping `0x0027 → 'D'`, `0x0055 → 'r'`, `0x0011 → '.'`, `0x0003 → ' '`, `0x0024 → 'A'`...
- So the actual text is "Dr. Arjun"

On top of this, text is often split character-by-character across **TJ arrays** with explicit kerning offsets:

```
[(<0027>) 5 (<0055>) 3 (<0011>) 0 (<0003>) 2 (<0024>) ...] TJ
```

Each character is in its own element, separated by kerning values (the numbers). This is common in professionally typeset PDFs.

---

## 3. Approach A: Redaction + Redraw (PyMuPDF / fitz)

### How it works
1. Search for the target text using `page.search_for()` — returns the bounding rectangles
2. Add a white rectangle over the old text: `page.add_redact_annot(rect, fill=(1,1,1))`
3. Apply the redaction: `page.apply_redactions()` — this whites out the area
4. Draw new text in the same position: `page.insert_textbox(rect, new_text)`

### Why it fails
**`insert_textbox()` draws with a system font — usually Helvetica — not the font embedded in the PDF.** The original text was rendered with a custom subset font (e.g., LiberationSans or a corporate font). The replacement uses Helvetica.

Consequences:
- Different font metrics → different character widths → text overflows or underflows the rectangle
- Different font appearance → the replaced text looks visually wrong, even to untrained eyes
- Kerning is lost → character spacing looks off
- Weight mismatch → if the original was bold or light, the replacement won't match

**Result:** The replaced text is readable but visually wrong. The rest of the page is unchanged.

---

## 4. Approach B: Convert → Edit → Convert Back (pdf2docx + LibreOffice)

### How it works
1. PDF → DOCX using `pdf2docx.Converter`
2. Open the DOCX with `python-docx`, find and replace text
3. DOCX → PDF using LibreOffice headless (`soffice --headless --convert-to pdf`)

### Why it fails
The round-trip is lossy at every stage:

**Stage 1 (PDF → DOCX):** `pdf2docx` does an impressive job reconstructing document structure from raw drawing instructions, but it makes guesses. It has to infer paragraph boundaries, table structures, and text flow from absolute position data. These guesses are often wrong for complex layouts — multi-column resumes, forms, certificates.

**Stage 2 (edit in DOCX):** The text replacement itself works if you handle the DOCX "run" fragmentation properly (a document engineering detail — DOCX splits text across `<w:r>` elements unpredictably). But even when the replacement succeeds:

**Stage 3 (DOCX → PDF):** LibreOffice re-renders the entire document from scratch. Its font metrics, line-breaking algorithm, and spacing calculations differ from whatever originally produced the PDF. Tables shift by pixels. Wrapped text breaks at different points. Margins and padding subtly change.

**Result:** The output looks like a "similar" document but every line, every table cell, every wrapped paragraph has been recalculated. The visual change is noticeable across the entire page, not just the changed words.

---

## 5. Approach C: Content Stream Editing (pikepdf)

### How it works

Instead of redrawing or converting, this approach modifies the **original drawing instructions** — the content stream operators — in place.

**Step 1: Parse font ToUnicode CMaps**

For every font used on the page, locate its `/ToUnicode` stream object. This is a CMap (Character Map) file that maps glyph IDs (CIDs) to Unicode code points. Parse it to build two dictionaries:
- `cid2uni`: CID → Unicode character
- `uni2cid`: Unicode character → CID (reverse lookup)

Example CMap fragment:
```
1 begincodespacerange
<0000> <ffff>
endcodespacerange
39 beginbfchar
<0027> <0044>    ← CID 0x0027 = Unicode U+0044 = 'D'
<0055> <0072>    ← CID 0x0055 = Unicode U+0072 = 'r'
...
```

**Step 2: Walk the content stream**

Parse the page's content stream into a list of `(operands, operator)` pairs. Track which font is active by watching for `Tf` (set font) operators.

**Step 3: Decode text**

For each `Tj` (show string) or `TJ` (show string array with kerning) operator, convert the CID bytes to Unicode using the `cid2uni` dictionary.

**Step 4: Find and replace**

Search the decoded Unicode text for target strings. When found:
- Encode the replacement text back to CID bytes using `uni2cid`
- Place the entire replacement in the first string element
- Zero out all subsequent elements that held the old text
- This preserves the kerning offsets for everything after the replacement

**Step 5: Serialize and save**

`unparse_content_stream()` serializes the modified instruction list back to binary. Write the new stream to `page.Contents` and save the PDF.

### Why it works

Every untouched operator — every font selection, every color change, every position command, every line, every image, every table border — is byte-for-byte identical to the original. Only the `Tj`/`TJ` operands containing the replaced text are modified.

The embedded fonts are never touched. The rendering engine uses the same glyphs, the same metrics, the same kerning data. The only difference is which glyph IDs happen to be referenced.

### Measured impact

On a real one-page resume PDF with three replacements (name, email, GitHub username):
- **Pixel difference: 0.55%** — only the changed characters differ
- Same page dimensions
- Same font rendering
- Same line breaks, table alignment, spacing

---

## 6. Remaining gaps and limitations

### 6.1 Subset font glyph availability

PDF fonts are almost always subsetted — they only contain the glyphs actually used in the original document. If the original says "SANKET" and you replace it with a word containing 'z', but the font doesn't have a 'z' glyph, the replacement can't be encoded.

**Mitigation:** Check `uni2cid` before encoding. If any character is missing, skip the replacement and report which glyphs weren't available.

**Real fix:** This is a fundamental PDF limitation. The only workaround is to embed additional glyphs into the subset font, which requires font subsetting tools and is beyond simple content stream editing.

### 6.2 Text split across operators

PDFs often fragment text across multiple `Tj`/`TJ` operators, especially when:
- Different words use different fonts
- Text spans line breaks
- The PDF generator's output engine splits text for optimization

The current implementation handles text within a single `TJ` array but not text split across multiple separate operators.

**Mitigation:** Concatenate text from consecutive operators belonging to the same text block. This requires tracking text matrix (`Tm`) and line position changes.

### 6.3 Character-by-character TJ arrays

Some PDFs store every character as a separate element in a `TJ` array with individual kerning offsets. The replacement logic must:
- Track which elements belong to the match range
- Put replacement text in the first matching element
- Clear all subsequent matching elements

This is implemented and working, but edge cases remain when the replacement text length differs significantly from the original, causing the kerning offsets of following text to apply at the wrong position.

### 6.4 Fonts without ToUnicode CMaps

Some PDFs use fonts that lack a `/ToUnicode` stream entirely. Without it, there is no reliable way to map CIDs back to Unicode. These PDFs cannot be edited with this approach.

### 6.5 Non-text content

Text rendered as paths/curves (common in logos and some design-heavy PDFs), text in images, and text in XObject forms are not accessible via the content stream's `Tj`/`TJ` operators.

### 6.6 No CLI / batch support

The current implementation is a Python library and a web server. A thin CLI wrapper and batch directory processing would make it much more practical for real-world use.

---

## 7. Comparison matrix

| Approach | Font match | Layout preserved | Pixel diff | Handles subset fonts | Handles kerning |
|---|---|---|---|---|---|
| PyMuPDF redaction | **No** (system font) | Yes | ~5-15% | N/A (redraws) | No |
| pdf2docx round-trip | Approximate | **No** (reflow) | ~15-40% | Yes (via LibreOffice) | Approximate |
| Content stream edit | **Yes** (original font) | **Yes** | **<1%** | Yes (limited by glyphs) | Yes |

---

## 8. What this enables

- **Legal documents**: Change party names or dates in executed contracts without invalidating formatting
- **Certificates**: Programmatically fill names into pre-designed certificate PDFs
- **Batch rebranding**: Update company name across thousands of archived PDF reports
- **Invoice correction**: Fix a client name or tax ID in a finalized invoice
- **Form filling**: Many PDFs use placeholder text (e.g., "[NAME]") instead of actual AcroForm fields — this can replace them in-place

---

## 9. Implementation reference

Code: [github.com/gdt0/pdf-editor-content-stream](https://github.com/gdt0/pdf-editor-content-stream)

Core dependencies: `pikepdf` (content stream parsing), Python stdlib `re` (CMap parsing)

Key files:
- `pdf_editor.py` — the core library, single public function `edit_pdf()`
- `server.py` — FastAPI web server
- `static/index.html` — dark-themed web UI
