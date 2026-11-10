"""
Simple-font (1-byte) encoding tables: WinAnsi, Standard, MacRoman + /Differences.

The PDF spec base encodings are expressed as lists of Adobe glyph names.
We build code -> unicode tables from names via an Adobe Glyph List (AGL)
subset, then apply /Differences overrides.

Coverage notes:
- WinAnsiEncoding: full (per PDF Annex D; 0xA0 is U+00A0 and 0xAD is
  U+2010, the two known deviations from cp1252/Latin-1).
- StandardEncoding: full (0x80-0x8C math set, Latin-1 high half,
  quoteleft/quoteright swapped into 0x60/0x27).
- MacRomanEncoding: derived from Python's bundled mac_roman codec
  (authoritative), not hand-typed tables.

Unmapped codes have no entry in code2uni (decode yields '' for them).
"""

_AGL = {
    # ASCII printable
    "space": "\u0020", "exclam": "\u0021", "quotedbl": "\u0022",
    "numbersign": "\u0023", "dollar": "\u0024", "percent": "\u0025",
    "ampersand": "\u0026", "quotesingle": "\u0027", "parenleft": "\u0028",
    "parenright": "\u0029", "asterisk": "\u002a", "plus": "\u002b",
    "comma": "\u002c", "hyphen": "\u002d", "period": "\u002e", "slash": "\u002f",
    "colon": "\u003a", "semicolon": "\u003b", "less": "\u003c",
    "equal": "\u003d", "greater": "\u003e", "question": "\u003f", "at": "\u0040",
    "bracketleft": "\u005b", "backslash": "\u005c", "bracketright": "\u005d",
    "asciicircum": "\u005e", "underscore": "\u005f", "grave": "\u0060",
    "braceleft": "\u007b", "bar": "\u007c", "braceright": "\u007d",
    "asciitilde": "\u007e",
    # WinAnsi 0x80-0x9F
    "Euro": "\u20ac", "quotesinglbase": "\u201a", "florin": "\u0192",
    "quotedblbase": "\u201e", "ellipsis": "\u2026", "dagger": "\u2020",
    "Dagger": "\u2021", "circumflex": "\u02c6", "perthousand": "\u2030",
    "Scaron": "\u0160", "guilsinglleft": "\u2039", "OE": "\u0152",
    "Zcaron": "\u017d", "quoteleft": "\u2018", "quoteright": "\u2019",
    "quotedblleft": "\u201c", "quotedblright": "\u201d", "bullet": "\u2022",
    "endash": "\u2013", "emdash": "\u2014", "tilde": "\u02dc",
    "trademark": "\u2122", "scaron": "\u0161", "guilsinglright": "\u203a",
    "oe": "\u0153", "zcaron": "\u017e", "Ydieresis": "\u0178",
    # Latin-1 supplement names
    "nbspace": "\u00a0", "exclamdown": "\u00a1", "cent": "\u00a2",
    "sterling": "\u00a3", "currency": "\u00a4", "yen": "\u00a5",
    "brokenbar": "\u00a6", "section": "\u00a7", "dieresis": "\u00a8",
    "copyright": "\u00a9", "ordfeminine": "\u00aa", "guillemotleft": "\u00ab",
    "logicalnot": "\u00ac", "registered": "\u00ae", "macron": "\u00af",
    "degree": "\u00b0", "plusminus": "\u00b1", "acute": "\u00b4",
    "mu": "\u00b5", "paragraph": "\u00b6", "periodcentered": "\u00b7",
    "cedilla": "\u00b8", "onesuperior": "\u00b9", "ordmasculine": "\u00ba",
    "guillemotright": "\u00bb", "onequarter": "\u00bc", "onehalf": "\u00bd",
    "threequarters": "\u00be", "questiondown": "\u00bf",
    "twosuperior": "\u00b2", "threesuperior": "\u00b3",
    "germandbls": "\u00df", "multiply": "\u00d7", "divide": "\u00f7",
    "fi": "\ufb01", "fl": "\ufb02",
    # StandardEncoding math block
    "notequal": "\u2260", "infinity": "\u221e", "lessequal": "\u2264",
    "greaterequal": "\u2265", "partialdiff": "\u2202", "summation": "\u2211",
    "product": "\u220f", "pi": "\u03c0", "integral": "\u222b",
    "Omega": "\u03a9", "radical": "\u221a", "approxequal": "\u2248",
    "Delta": "\u2206", "lozenge": "\u25ca",
}

# Latin-1 upper half names, 0xC0-0xDE and 0xE0-0xFE (codepoint = base + i).
_LATIN_UPPER = [
    "Agrave", "Aacute", "Acircumflex", "Atilde", "Adieresis", "Aring",
    "AE",
    "Ccedilla", "Egrave", "Eacute", "Ecircumflex", "Edieresis", "Igrave",
    "Iacute", "Icircumflex", "Idieresis", "Eth", "Ntilde", "Ograve",
    "Oacute", "Ocircumflex", "Otilde", "Odieresis", "multiply", "Oslash",
    "Ugrave", "Uacute", "Ucircumflex", "Udieresis", "Yacute", "Thorn",
]
_LATIN_LOWER = [
    "agrave", "aacute", "acircumflex", "atilde", "adieresis", "aring",
    "ae",
    "ccedilla", "egrave", "eacute", "ecircumflex", "edieresis", "igrave",
    "iacute", "icircumflex", "idieresis", "eth", "ntilde", "ograve",
    "oacute", "ocircumflex", "otilde", "odieresis", "divide", "oslash",
    "ugrave", "uacute", "ucircumflex", "udieresis", "yacute", "thorn",
]
for _i, _n in enumerate(_LATIN_UPPER):
    _AGL[_n] = chr(0xC0 + _i)
for _i, _n in enumerate(_LATIN_LOWER):
    _AGL[_n] = chr(0xE0 + _i)
_AGL["ydieresis"] = "\u00ff"
for _i in range(26):
    _AGL[chr(ord("A") + _i)] = chr(0x41 + _i)
    _AGL[chr(ord("a") + _i)] = chr(0x61 + _i)
for _i in range(10):
    _AGL[str(_i)] = chr(0x30 + _i)


def _ascii_names():
    """Code -> name for ASCII printable range (0x20-0x7E)."""
    names = [
        "space", "exclam", "quotedbl", "numbersign", "dollar", "percent",
        "ampersand", "quotesingle", "parenleft", "parenright", "asterisk",
        "plus", "comma", "hyphen", "period", "slash",
    ]
    names += [chr(ord("0") + i) for i in range(10)]
    names += ["colon", "semicolon", "less", "equal", "greater", "question", "at"]
    names += [chr(ord("A") + i) for i in range(26)]
    names += ["bracketleft", "backslash", "bracketright", "asciicircum", "underscore", "grave"]
    names += [chr(ord("a") + i) for i in range(26)]
    names += ["braceleft", "bar", "braceright", "asciitilde"]
    return {0x20 + i: n for i, n in enumerate(names)}


_WINANSI_HIGH = {
    0x80: "Euro", 0x82: "quotesinglbase", 0x83: "florin", 0x84: "quotedblbase",
    0x85: "ellipsis", 0x86: "dagger", 0x87: "Dagger", 0x88: "circumflex",
    0x89: "perthousand", 0x8A: "Scaron", 0x8B: "guilsinglleft", 0x8C: "OE",
    0x8E: "Zcaron", 0x91: "quoteleft", 0x92: "quoteright", 0x93: "quotedblleft",
    0x94: "quotedblright", 0x95: "bullet", 0x96: "endash", 0x97: "emdash",
    0x98: "tilde", 0x99: "trademark", 0x9A: "scaron", 0x9B: "guilsinglright",
    0x9C: "oe", 0x9E: "zcaron", 0x9F: "Ydieresis",
}

_STANDARD_HIGH_MATH = {
    0x80: "notequal", 0x81: "infinity", 0x82: "lessequal",
    0x83: "greaterequal", 0x84: "partialdiff", 0x85: "summation",
    0x86: "product", 0x87: "pi", 0x88: "integral", 0x89: "Omega",
    0x8A: "radical", 0x8B: "approxequal", 0x8C: "Delta",
}

# 0xA1-0xBF: identical in WinAnsi and Standard (Latin-1 upper half; both
# PDF tables deviate from cp1252 only at 0xAD, overridden below). Shared
# because the WinAnsi/Standard tables were missing this block entirely —
# £ © ° « ¿ etc. decoded to '' and their glyphs were falsely refused.
_LATIN1_MID = {
    0xA1: "exclamdown", 0xA2: "cent", 0xA3: "sterling", 0xA4: "currency",
    0xA5: "yen", 0xA6: "brokenbar", 0xA7: "section", 0xA8: "dieresis",
    0xA9: "copyright", 0xAA: "ordfeminine", 0xAB: "guillemotleft",
    0xAC: "logicalnot", 0xAE: "registered", 0xAF: "macron",
    0xB0: "degree", 0xB1: "plusminus", 0xB2: "twosuperior",
    0xB3: "threesuperior", 0xB4: "acute", 0xB5: "mu", 0xB6: "paragraph",
    0xB7: "periodcentered", 0xB8: "cedilla", 0xB9: "onesuperior",
    0xBA: "ordmasculine", 0xBB: "guillemotright", 0xBC: "onequarter",
    0xBD: "onehalf", 0xBE: "threequarters", 0xBF: "questiondown",
}


def _latin_high(start):
    return {start + i: _AGL[_LATIN_UPPER[i]] for i in range(31)}


def _latin_lower_high(start):
    return {start + i: _AGL[_LATIN_LOWER[i]] for i in range(31)}


def _mac_roman_names():
    """MacRomanEncoding derived from Python's authoritative mac_roman codec."""
    raw = bytes(range(0x100))
    text = raw.decode("mac_roman")
    return {i: ch for i, ch in enumerate(text)}


def build_base_encoding(name):
    """Return {code: unicode} for a standard PDF base encoding."""
    if name not in ("WinAnsiEncoding", "StandardEncoding", "MacRomanEncoding"):
        return {}
    table = {code: _AGL[n] for code, n in _ascii_names().items()}
    table.update({code: _AGL[glyph] for code, glyph in _LATIN1_MID.items()})
    if name == "WinAnsiEncoding":
        for code, glyph in _WINANSI_HIGH.items():
            table[code] = _AGL[glyph]
        table[0xA0] = "\u00a0"  # nbsp, not plain space
        table.update(_latin_high(0xC0))
        table[0xDF] = _AGL["germandbls"]
        table.update(_latin_lower_high(0xE0))
        table[0xFF] = "\u00ff"
        table[0xAD] = "\u2010"  # WinAnsi hyphen, deviates from cp1252 soft hyphen
    elif name == "StandardEncoding":
        table[0x27] = "\u2019"  # quoteright
        table[0x60] = "\u2018"  # quoteleft
        for code, glyph in _STANDARD_HIGH_MATH.items():
            table[code] = _AGL[glyph]
        table.update(_latin_high(0xC0))
        table[0xDF] = _AGL["germandbls"]
        table.update(_latin_lower_high(0xE0))
        table[0xFF] = "\u00ff"
        table[0xAD] = "\u2010"
    else:  # MacRomanEncoding
        table.update(_mac_roman_names())
        table.pop(0x00, None)
    return table


BASE_ENCODING_NAMES = (
    "WinAnsiEncoding",
    "StandardEncoding",
    "MacRomanEncoding",
)


def parse_differences(diffs):
    """Parse a /Differences array -> {code: glyph_name}.

    Supports both [24 /a /b] (incrementing) and [24 /a 26 /c] forms.
    """
    out = {}
    code = None
    for el in diffs:
        s = str(el)
        if s.startswith("/"):
            if code is not None:
                out[code] = s[1:]
                code += 1
        else:
            try:
                code = int(el)
            except (TypeError, ValueError):
                code = None
    return out


def build_simple_encoding(base_name=None, differences=None):
    """Build (code2uni, uni2code) from a base encoding name + /Differences.

    uni2code keeps the first (lowest) code for each character.
    """
    code2uni = build_base_encoding(base_name) if base_name else {}
    if differences:
        for code, glyph in parse_differences(differences).items():
            uni = _AGL.get(glyph)
            if uni is not None:
                code2uni[code] = uni
            elif glyph.startswith("uni") and len(glyph) == 7:
                try:
                    code2uni[code] = chr(int(glyph[3:], 16))
                except ValueError:
                    pass
    uni2code = {}
    for code in sorted(code2uni):
        uni2code.setdefault(code2uni[code], code)
    return code2uni, uni2code
