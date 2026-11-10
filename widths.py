"""
Glyph advance measurement (Phase 3, item 14).

Width of a glyph = advance/1000 em. Em = font size (modified by Tz
horizontal scaling at the text-state level, handled by callers).

Fallback chain per font:
1. Simple fonts: /Widths + /FirstChar (the standard, required-ish array)
2. Type0 fonts: /W array (cff 1: single c [w ...]; cff 2: c [w1 w2 ...])
3. Embedded font program (FontFile2 TrueType hmtx, FontFile3 CFF) via
   fontTools, with /CIDToGIDMap (Identity or stream)
4. Standard-14 base fonts without an embed: MuPDF's built-in AFM metrics
5. Unknown -> measuring returns None; the caller must treat the line as
   unmeasurable and refuse to shift/reflow it (refusal is first-class).
"""
import io

import pikepdf

_BASE14 = {
    "Helvetica": "helv", "Helvetica-Bold": "hebo", "Helvetica-Oblique": "heit",
    "Helvetica-BoldOblique": "hebi", "Courier": "cour", "Courier-Bold": "cobo",
    "Courier-Oblique": "coit", "Courier-BoldOblique": "cobi",
    "Times-Roman": "tiro", "Times-Bold": "tibo", "Times-Italic": "tiit",
    "Times-BoldItalic": "tibi", "Symbol": "symb", "ZapfDingbats": "zadb",
}


class FontWidths:
    """advance(code) -> glyph advance in 1/1000 em, or None if unknown."""

    def __init__(self, lookup):
        self._lookup = lookup

    def advance(self, code):
        try:
            return self._lookup(code)
        except Exception:
            return None

    def text_width(self, text, codec, size, tc=0.0, tw=0.0):
        """Width in text-space units of `text` (decoded string) at `size`.

        Uses the codec's greedy code mapping (ligature-aware). Returns None
        if any glyph's advance is unknown.
        """
        total = 0.0
        spaces = sum(1 for ch in text if ch == " ")
        for nchars, code in codec.iter_codes(text):
            if code is None:
                return None
            adv = self.advance(code)
            if adv is None:
                return None
            total += adv + nchars * tc
        total += tw * spaces
        return total * size / 1000.0


def _simple_widths_from_array(font):
    # malformed numerics degrade to None (unmeasured), never raise: one bad
    # /Widths entry must not kill the whole edit at build time
    try:
        widths = font.get("/Widths")
        if widths is None:
            return None
        first = int(font.get("/FirstChar", 0))
    except (TypeError, ValueError):
        return None
    arr = []
    for w in widths:
        try:
            arr.append(float(w) if w is not None else None)
        except (TypeError, ValueError):
            arr.append(None)

    def lookup(code):
        idx = code - first
        if 0 <= idx < len(arr) and arr[idx] is not None:
            return arr[idx]
        return None

    return lookup


def _parse_w_array(wobj):
    """Parse /W -> {cid: advance} (1/1000 em). Malformed entries are
    skipped, not fatal."""
    out = {}
    i = 0
    n = len(wobj)
    while i < n:
        try:
            c = int(wobj[i])
            if i + 1 < n and isinstance(wobj[i + 1], (list, pikepdf.Array)):
                for j, w in enumerate(wobj[i + 1]):
                    if w is not None:
                        out[c + j] = float(w)
                i += 2
            elif i + 2 < n:
                c2 = int(wobj[i + 1])
                w = float(wobj[i + 2])
                if c2 - c < 65536:  # bomb guard
                    for cid in range(c, c2 + 1):
                        out[cid] = w
                i += 3
            else:
                break
        except (TypeError, ValueError):
            i += 1  # skip the unparsable element, keep scanning
    return out


def _cidfont_of_type0(font):
    desc = font.get("/DescendantFonts")
    if desc is not None and len(desc) > 0:
        return desc[0]
    return font


def _cid_widths_from_w(font):
    cidfont = _cidfont_of_type0(font)
    wobj = cidfont.get("/W")
    if wobj is None:
        return None
    table = _parse_w_array(wobj)
    # CIDs not covered by /W use the font's default width (spec: 1000)
    try:
        dw = float(cidfont.get("/DW", 1000))
    except (TypeError, ValueError):
        dw = 1000.0

    def lookup(code):
        return table.get(code, dw)

    return lookup


def _cid_widths_from_embedded(font):
    """fontTools over FontFile2 (TrueType hmtx) / FontFile3 (CFF)."""
    from fontTools.ttLib import TTFont

    cidfont = _cidfont_of_type0(font)
    fd = cidfont.get("/FontDescriptor")
    if fd is None:
        return None

    code_to_gid = {}
    gid_map = cidfont.get("/CIDToGIDMap")
    if gid_map is None or isinstance(gid_map, pikepdf.Name):
        if gid_map is not None and str(gid_map) != "/Identity":
            return None
        identity = True
    else:
        identity = False
        data = gid_map.read_bytes()
        for i in range(0, len(data) - 1, 2):
            code_to_gid[i // 2] = (data[i] << 8) | data[i + 1]

    program = None
    for key in ("/FontFile2", "/FontFile3", "/FontFile"):
        obj = fd.get(key)
        if obj is not None:
            program = obj.read_bytes()
            break
    if not program:
        return None

    try:
        tt = TTFont(io.BytesIO(program), fontNumber=0, lazy=True)
        if "hmtx" in tt:
            metrics = tt["hmtx"].metrics

            def lookup(code):
                gid = code if identity else code_to_gid.get(code)
                if gid is None:
                    return None
                m = metrics.get(gid)
                return m[0] if m else None

            return lookup
    except Exception:
        return None
    return None


def _base14_widths(font):
    name = str(font.get("/BaseFont", "")).lstrip("/")
    base = name.split("+")[-1]  # strip subset prefix
    if base not in _BASE14:
        return None
    import fitz

    mupdf_name = _BASE14[base]
    mfont = fitz.Font(mupdf_name)
    cache = {}

    def lookup(code):
        if code not in cache:
            try:
                cache[code] = mfont.glyph_advance(code) * 1000.0
            except Exception:
                cache[code] = None
        return cache[code]

    return lookup


def build_widths(font) -> FontWidths:
    """Build the best available measurement for a font dict."""
    subtype = str(font.get("/Subtype", "")).lstrip("/")
    lookups = []

    if subtype in ("Type0", "CIDFontType0", "CIDFontType2"):
        lookups += [_cid_widths_from_w(font), _cid_widths_from_embedded(font)]
    else:
        lookups.append(_simple_widths_from_array(font))
        # base14 metric fallback maps a LATIN character code to a width.
        # Codes here are font bytes/CIDs, not Unicode: for Symbol and
        # ZapfDingbats (custom encodings) and for Type0 CIDs the lookup
        # measured the wrong glyph — gate the fallback to Latin-encoded
        # simple fonts only.
        base = str(font.get("/BaseFont", "")).lstrip("/").split("+")[-1]
        if base not in ("Symbol", "ZapfDingbats"):
            base14 = _base14_widths(font)
            if base14 is not None:
                lookups.append(base14)

    def combined(code):
        for lookup in lookups:
            if lookup is None:
                continue
            w = lookup(code)
            if w is not None:
                return w
        return None

    return FontWidths(combined)
