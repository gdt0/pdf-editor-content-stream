# PDF Content Stream Editor

Edit text in PDFs with **zero format change** — edits raw PDF content stream operators (Tj/TJ) directly, never redraws anything.

## How it works

PDFs store text as drawing instructions: the `Tj` and `TJ` operators in each page's content stream. Text is encoded as glyph IDs (CIDs) that map to shapes in embedded subset fonts. Each font has a **ToUnicode CMap** that maps CIDs back to Unicode.

This tool:
1. Parses each font's ToUnicode CMap → builds CID↔Unicode dictionaries
2. Walks the content stream tracking the active font (Tf operator)
3. Decodes text from CID bytes to Unicode for comparison
4. Finds and replaces target text in Unicode
5. Re-encodes replacement text back to CIDs using the reverse mapping
6. Writes the modified stream back — unchanged bytes stay identical

**No redrawing. No font substitution. No reflow.** Only the changed text bytes differ.

## Installation

```bash
git clone https://github.com/YOUR_USER/pdf-editor-content-stream.git
cd pdf-editor-content-stream
pip install -r requirements.txt
```

## Usage

### Python library

```python
from pdf_editor import edit_pdf

edit_pdf("input.pdf", "output.pdf", {"old text": "new text"})
```

Supports multiple replacements:
```python
edit_pdf("resume.pdf", "edited.pdf", {
    "SANKET": "Sachin",
    "sanketdkumar@gmail.com": "sachindkumar@gmail.com",
})
```

### Web server

```bash
python server.py
# Open http://localhost:8000
```

Dark-themed web UI with drag-and-drop upload and find/replace form.

### API

```
POST /api/edit
Content-Type: multipart/form-data
Fields: file (PDF), replacements (JSON string)

Response: application/pdf
```

Example:
```bash
curl -F "file=@resume.pdf" \
     -F 'replacements={"SANKET":"Rohit"}' \
     http://localhost:8000/api/edit \
     -o edited.pdf
```

## Limitations

- **Subset fonts** — PDF fonts typically only contain glyphs used in the original document. Replacement characters must exist in the font's glyph set. If a replacement character isn't available, it will be omitted.
- **Text fragmentation** — some PDFs split text across multiple Tj/TJ operators or encode it in complex ways. If no replacements are made, the `ValueError` explains why.
- **Non-standard encodings** — fonts without a ToUnicode CMap cannot be decoded.

## Pixel difference in testing

On a real resume PDF with 3 replacements (name, email, GitHub username):
- **0.55% pixel difference** from the original
- Only the changed words differ visually
- Same page dimensions
- Same font rendering everywhere else

## License

MIT
