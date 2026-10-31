"""
Font codec layer: one codec per font, keyed by /Subtype and /Encoding.

Replaces the old hard-coded "2-byte CID + ToUnicode or nothing" assumption.
Supported:
- Type0/CID fonts (incl. mis-typed bare CIDFontType0/2) via ToUnicode CMap,
  with variable code lengths from codespace ranges
- Simple fonts (Type1/TrueType/MMType1/Type3) via ToUnicode (preferred),
  else /Encoding base name + /Differences (WinAnsi/Standard/MacRoman)
- Multi-codepoint destinations (ligatures) encode back greedily (longest match)

Missing glyphs are NEVER silently dropped: encode() returns the bytes plus
the list of unencodable characters; callers must refuse to mutate when the
list is non-empty.
"""
import pikepdf

from cmap import parse_tounicode
from fontencodings import BASE_ENCODING_NAMES, build_simple_encoding

_SIMPLE_SUBTYPES = ("Type1", "TrueType", "Type3", "MMType1")
_CID_SUBTYPES = ("Type0", "CIDFontType0", "CIDFontType2")


class SimpleFontCodec:
    """1-byte codes mapped through a code->unicode table."""

    def __init__(self, code2uni, label):
        self.label = label
        self.code2uni = code2uni
        self.uni2code = {}
        for code in sorted(code2uni):
            self.uni2code.setdefault(code2uni[code], code)

    def decode(self, data: bytes) -> str:
        return "".join(self.code2uni.get(b, "") for b in data)

    def encode(self, text: str):
        out = bytearray()
        missing = []
        for ch in text:
            code = self.uni2code.get(ch)
            if code is None:
                missing.append(ch)
            else:
                out.append(code)
        return bytes(out), missing

    def can_encode(self, text: str):
        return [ch for ch in text if ch not in self.uni2code]


class CMapFontCodec:
    """Codes split by CMap codespace ranges (1-4 bytes), via ToUnicode."""

    def __init__(self, cmap, label):
        self.cmap = cmap
        self.label = label
        self._rev = cmap.reverse_map()
        # Group reverse keys by first char, longest-first for greedy encode.
        self._by_first = {}
        for key in self._rev:
            self._by_first.setdefault(key[0], []).append(key)
        for keys in self._by_first.values():
            keys.sort(key=len, reverse=True)

    def decode(self, data: bytes) -> str:
        return self.cmap.decode(data)

    def encode(self, text: str):
        out = bytearray()
        missing = []
        i, n = 0, len(text)
        while i < n:
            for key in self._by_first.get(text[i], ()):
                if text.startswith(key, i):
                    out += self._rev[key]
                    i += len(key)
                    break
            else:
                missing.append(text[i])
                i += 1
        return bytes(out), missing

    def can_encode(self, text: str):
        missing = []
        i, n = 0, len(text)
        while i < n:
            for key in self._by_first.get(text[i], ()):
                if text.startswith(key, i):
                    i += len(key)
                    break
            else:
                missing.append(text[i])
                i += 1
        return missing


def build_codec(font) -> tuple:
    """Build a codec for a font dict. Returns (codec, reason).

    codec is None when the font cannot be decoded; reason explains why
    (surfaced to the caller as fonts_skipped — never a silent skip).
    """
    subtype = str(font.get("/Subtype", "")).lstrip("/")
    label = str(font.get("/BaseFont", "")).lstrip("/") or subtype
    to_unicode = font.get("/ToUnicode")

    if subtype in _CID_SUBTYPES:
        if to_unicode is None:
            return None, "%s (%s): Type0 font without /ToUnicode" % (label, subtype)
        cmap = parse_tounicode(to_unicode.read_bytes())
        if not cmap.cid2uni:
            return None, "%s (%s): ToUnicode CMap has no usable entries" % (label, subtype)
        return CMapFontCodec(cmap, label), None

    if subtype in _SIMPLE_SUBTYPES:
        if to_unicode is not None:
            cmap = parse_tounicode(to_unicode.read_bytes())
            if cmap.cid2uni:
                return CMapFontCodec(cmap, label), None
        enc = font.get("/Encoding")
        base = None
        differences = None
        if isinstance(enc, pikepdf.Name):
            base = str(enc).lstrip("/")
        elif enc is not None:
            base_name = enc.get("/BaseEncoding")
            if base_name is not None:
                base = str(base_name).lstrip("/")
            diffs = enc.get("/Differences")
            if diffs is not None:
                differences = [el for el in diffs]
        if base is None and differences is None:
            return None, "%s (%s): no /ToUnicode and no /Encoding" % (label, subtype)
        if base is not None and base not in BASE_ENCODING_NAMES:
            return None, "%s (%s): unsupported /Encoding %s" % (label, subtype, base)
        code2uni, _uni2code = build_simple_encoding(base, differences)
        if not code2uni:
            return None, "%s (%s): empty encoding table" % (label, subtype)
        return SimpleFontCodec(code2uni, label), None

    return None, "%s: unsupported font subtype /%s" % (label, subtype)
