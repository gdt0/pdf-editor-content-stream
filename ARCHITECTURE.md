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

Consecutive text-show operators (`Tj`/`TJ`) belonging to the same text block are
accumulated into a single buffer, so a target string may be matched even when it
is split across several operators. Search the concatenated decoded Unicode for
target strings. When found:
- Encode the replacement text back to CID bytes using `uni2cid`
- Place the entire replacement in the operand that holds the match start
- Clear every intermediate operand inside the match range
- Preserve the tail of the operand that holds the match end
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

This is now handled by a **text-block accumulator** (see §9.4). The editor
collects consecutive text-show operators that belong to the same text block,
concatenates their decoded Unicode, applies replacements across the combined
string, and writes the modified CID bytes back into the original operands while
leaving every untouched operator byte-for-byte identical.

**Remaining edge cases:** matching is intentionally conservative and still
cannot span an arbitrary layout change. A block is flushed — ending the matchable
region — whenever the font changes, a non-text operator appears, or text
positioning moves to a new line/paragraph. So a target string that is broken
across a line wrap, a font switch, an XObject boundary (§10.5), or any
intervening graphics operator will not match.

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

## 9. What was built on top of pikepdf

`pikepdf` provides two critical primitives:
- `parse_content_stream(page)` — parses a page's binary content stream into a Python list of `(operands, operator)` tuples
- `unparse_content_stream(instructions)` — serializes the list back to binary

These are the **only** pikepdf functions used. Everything else in this project is custom-built to handle real-world PDF complexity:

### 9.1 ToUnicode CMap parser (`_parse_tounicode_cmap`)

pikepdf gives access to the raw ToUnicode stream bytes. But parsing the CMap format is entirely custom code. The CMap is a PostScript-like text format with two relevant sections:

**bfchar blocks** — one-to-one mappings:
```
39 beginbfchar
<0027> <0044>
<0055> <0072>
...
endbfchar
```

**bfrange blocks** — range mappings:
```
2 beginbfrange
<0000> <0040> <0041>
<0100> <0120> <0200>
endbfrange
```

The custom parser uses regex to extract CID→Unicode pairs from both block types. Range blocks are expanded into individual mappings. Invalid Unicode code points (`>= 0x110000`) are skipped — a real-world edge case found in production PDFs that would crash naive implementations.

### 9.2 Font tracking across the content stream

PDF content streams are stateful — a `Tf` operator sets the "current font" and all subsequent `Tj`/`TJ` operators use that font until the next `Tf`. The code walks instructions sequentially, tracking `current_font` by watching for `Tf` operators:

```python
if op == "Tf" and len(operands) >= 1:
    current_font = str(operands[0]).lstrip("/")
```

This is critical because different sections of a page use different fonts (heading font vs body font), and each font has its own ToUnicode CMap.

### 9.3 CID string decode/encode (`_decode_cid`, `_encode_cid`)

CIDs are stored as 2-byte big-endian integers. The decode function reads 2 bytes at a time, looks up the CID in the font's `cid2uni` dictionary, and builds a Unicode string:

```python
cid = (raw_bytes[i] << 8) | raw_bytes[i + 1]
result.append(cid2uni.get(cid, ""))
```

The encode function does the reverse — looks up each Unicode character in `uni2cid` and writes the 2-byte CID. Characters not available in the subset font are silently skipped.

### 9.4 Text-block accumulation and array reconstruction

This is the most complex piece. Text to be matched can be spread across both the
elements of a single `TJ` array *and* across several separate `Tj`/`TJ`
operators. A `TJ` array contains interleaved string elements and numeric kerning
offsets:

```
[(S) 5 (A) 3 (N) 2 (K) 4 (E) 7 (T)] TJ   ← "SANKET" with per-character spacing
```

…and the same word might instead be split as `(SAN) Tj` followed by `(KET) Tj`.

To handle both, the editor walks the content stream and maintains a **text-block
accumulator**. Every string operand it can decode — whether a plain `Tj` or an
individual element of a `TJ` array — becomes a *slot*: a unit of text bound to
the exact operand it came from. Consecutive slots are buffered into one block:

1. **Accumulate** consecutive `Tj`/`TJ` slots while the font and text-line
   position are unchanged, tracking each slot's position in the concatenated text
2. **Flush** the block — i.e. search and rewrite it — when the font changes
   (`Tf`), a non-text operator appears, or positioning (`Tm`, `Td`/`TD`, `T*`)
   moves to a new line/paragraph
3. **Find the target** in the concatenated text; identify the slot holding the
   match start and the slot holding the match end (these may be different
   operators)
4. **Place the entire replacement** into the start slot's operand (after its
   preserved prefix)
5. **Clear** every intermediate slot inside the match range
6. **Preserve the tail** of the end slot (the text after the match)
7. **Rewrite only modified operands** — each `Tj` becomes a new
   `ContentStreamInstruction`; each touched `TJ` array is rebuilt keeping its
   numeric kerning elements and untouched strings intact. Operators with no
   change are left byte-for-byte identical.

The tricky part: the replacement text may be a different length than the original. After replacement, the kerning offsets for following text will apply slightly differently, but this is a negligible visual change (<1 pixel for most pairs).

### 9.5 Content stream write-back

After modifying the instruction list, two steps write it back:

```python
new_stream = unparse_content_stream(instructions)
page.Contents = pdf.make_stream(new_stream)
```

`unparse_content_stream` serializes the Python objects back to binary PDF operators. `pdf.make_stream()` creates a proper PDF stream object from the binary data. Finally, `pdf.save()` writes the complete file, preserving all untouched objects byte-for-byte.

---

## 10. Detailed limitations

### 10.1 Subset font glyph availability (CRITICAL)

**What:** PDF fonts are subsetted — they only contain glyphs actually used in the original document.

**Impact:** If you try to replace "SANKET" with "Zack" but the font doesn't have a 'Z' glyph, the replacement partially fails or is silently corrupted.

**Detection:** The code checks `uni2cid` for every character in the replacement text before encoding. Missing glyphs are skipped, which means the replacement character simply won't appear in the output.

**Real-world frequency:** Common in professionally designed PDFs where fonts are aggressively subsetted. A typical resume font has 60-70 glyphs — enough for most edits but will fail on unusual characters or symbols not in the original.

**Workaround:** None within the content stream approach. Adding glyphs to an existing subset font requires font tools like `fonttools`, which is a fundamentally different problem.

### 10.2 Text split across operators (COMMON — handled)

**What:** PDF generators often split text across multiple `Tj`/`TJ` operators. A single visible paragraph might be 20 separate drawing commands.

**Impact:** If "SANKET" spans two separate `Tj`/`TJ` operators, it is still matched: a text-block accumulator (§9.4) concatenates consecutive text-show operators in the same block before searching, then writes the replacement back across the original operands.

**Detection:** The function returns `ValueError("No replacements made")` only when no match is found anywhere — for example because the target text genuinely crosses a block boundary (see below) or uses an unparseable encoding.

**Real-world frequency:** Very common in PDFs from Microsoft Word, Adobe InDesign, and most professional tools. LaTeX PDFs are less fragmented.

**How it works:** Walk the stream tracking the active font and the text-line position. Accumulate consecutive `Tj`/`TJ` text into one buffer and flush (search + rewrite) the buffer when the font changes (`Tf`), when a non-text operator appears, or when positioning (`Tm`, `Td`/`TD`, `T*`) moves to a new line. A purely horizontal `Td` keeps the same block; a vertical move starts a new one.

**Remaining limitation:** Matching is conservative and does not span an arbitrary layout change — text broken across a line wrap, a font switch, an XObject boundary, or any intervening graphics operator still will not match. Spanning those would require modeling the full page layout rather than the linear operator stream.

### 10.3 Missing ToUnicode CMaps (RARE)

**What:** Some PDFs use fonts without a `/ToUnicode` stream, especially older PDFs or those using standard 14 fonts (Times, Helvetica, Courier).

**Impact:** Without a CMap, there is no way to determine what Unicode character a given CID represents. These PDFs cannot be edited.

**Real-world frequency:** Rare in modern PDFs. The PDF/A standard requires ToUnicode CMaps. Standard 14 fonts can sometimes be decoded using their built-in encoding tables, but this is not yet implemented.

### 10.4 Kerning offset drift after replacement

**What:** When the replacement text has a different length than the original, the kerning offsets between the replacement area and following text apply at the wrong character positions.

**Impact:** The text after the replacement may shift by 1-3 pixels. This is usually invisible to the naked eye but measurable in pixel-diff comparisons.

**Real-world frequency:** Every replacement where `len(new) != len(old)`. The effect is proportional to the length difference and the kerning values — larger differences cause more visible shifts.

### 10.5 Text in XObject Forms

**What:** PDFs can store reusable content in Form XObjects (similar to "symbols" or "components"). Text inside a Form XObject is not in the page's main content stream.

**Impact:** Text stored in Form XObjects will not be found or replaced. This is common in PDFs with headers/footers, watermarks, or repeated elements.

**Fix (not yet implemented):** Recursively walk the `/Resources` dictionary for Form XObjects and process their content streams the same way as page streams.

### 10.6 Encrypted / password-protected PDFs

**What:** PDFs with owner or user passwords.

**Impact:** pikepdf can open some encrypted PDFs but may fail if the encryption is strong. PDFs with edit restrictions (owner password) can be read but not saved.

### 10.7 No parallel / batch processing

**What:** The library processes one PDF at a time, single-threaded.

**Impact:** Processing 1000 PDFs takes 1000x the time of one. Each file goes through CMap parsing, content stream parsing, replacement, and save independently.

**Fix (not yet implemented):** Multiprocessing wrapper using `concurrent.futures.ProcessPoolExecutor`.

---

## 11. What this enables

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
