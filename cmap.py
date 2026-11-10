"""
ToUnicode CMap parser.

A real tokenizer for the PostScript-like CMap format, replacing the old
regex approach. Handles everything the old parser silently dropped:

- codespace ranges (variable code lengths, 1-4 bytes)
- bfchar entries with multi-codepoint destinations (ligatures: one CID -> "fi")
- bfchar destinations written as UTF-16BE, including surrogate pairs
  (astral chars: one CID -> U+1F600)
- bfrange with array destinations [<d1> <d2> ...]
- bfrange base form with increment
- comments (%) and arbitrary whitespace

Decoded text is a str; codes are keyed by raw bytes so variable-length
code fonts work. Unknown codes decode to '' (recorded by callers that
need diagnostics).
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field

_MAX_RANGE_EXPANSION = 65535  # refuse insane bfrange spans (bomb protection)

_TOKEN_RE = re.compile(
    r"""
      %[^\n]*                        # comment (skipped)
    | <\s*([0-9A-Fa-f\s]*?)\s*>      # hex string
    | ([\[\]])                       # bracket
    | /([^\s\[\]<>{}()/]+)           # name
    | (-?\d+)                        # number
    | ([^\s\[\]<>%/]+)               # keyword / word
    """,
    re.VERBOSE,
)


def _tokens(text):
    """Yield (kind, value) tokens. kind in {hex, bracket, name, num, word}."""
    for m in _TOKEN_RE.finditer(text):
        hexs, brk, name, num, word = m.groups()
        if hexs is not None:
            raw = re.sub(r"\s+", "", hexs)
            if len(raw) % 2:
                raw += "0"  # PDF rule: odd digit count pads final digit with 0
            try:
                yield ("hex", bytes.fromhex(raw))
            except ValueError:
                pass
        elif brk is not None:
            yield ("bracket", brk)
        elif name is not None:
            yield ("name", name)
        elif num is not None:
            yield ("num", int(num))
        elif word is not None:
            yield ("word", word)


def _utf16be(raw, malformed):
    """Decode a CMap destination hex string (UTF-16BE) -> unicode str.

    Handles multi-codepoint destinations (ligatures) and surrogate pairs
    natively. Returns None for empty/malformed entries (recorded).
    """
    if not raw:
        return None
    if len(raw) % 2 != 0:
        malformed.append(raw.hex())
        return None
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        malformed.append(raw.hex())
        return None


@dataclass
class ToUnicodeCMap:
    codespaces: list = field(default_factory=list)  # [(min_bytes, max_bytes)]
    cid2uni: dict = field(default_factory=dict)     # code bytes -> unicode str
    malformed: list = field(default_factory=list)   # skipped entries (hex)

    @property
    def code_lengths(self):
        return sorted({len(lo) for lo, _hi in self.codespaces})

    def decode(self, data: bytes) -> str:
        """Decode code bytes -> unicode, splitting codes by codespace ranges."""
        if not self.codespaces:
            # No codespacerange: assume 2-byte codes (Identity-H convention).
            out = []
            for i in range(0, len(data) - 1, 2):
                out.append(self.cid2uni.get(data[i : i + 2], ""))
            return "".join(out)

        lengths = self.code_lengths
        out = []
        i = 0
        n = len(data)
        while i < n:
            code = None
            for length in lengths:
                if i + length <= n:
                    cand = data[i : i + length]
                    if any(
                        len(lo) == length and lo <= cand <= hi
                        for lo, hi in self.codespaces
                    ):
                        code = cand
                        break
            if code is None:
                i += 1  # no codespace matches; resync by one byte
                continue
            out.append(self.cid2uni.get(code, ""))
            i += len(code)
        return "".join(out)

    def reverse_map(self) -> dict:
        """Build unicode str -> code bytes. Multi-codepoint values (ligature
        decompositions) are included; encoding resolves them greedily."""
        rev = {}
        for code, uni in self.cid2uni.items():
            rev.setdefault(uni, code)
        return rev


def parse_tounicode(data: bytes) -> ToUnicodeCMap:
    """Parse a ToUnicode CMap stream -> ToUnicodeCMap."""
    text = data.decode("latin-1", errors="replace")
    toks = list(_tokens(text))
    cmap = ToUnicodeCMap()
    i, n = 0, len(toks)

    def hex_at(j):
        return toks[j][1] if j < n and toks[j][0] == "hex" else None

    while i < n:
        kind, val = toks[i]
        if kind == "word" and val == "begincodespacerange":
            i += 1
            while i < n and toks[i] != ("word", "endcodespacerange"):
                lo = hex_at(i)
                hi = hex_at(i + 1)
                if lo is not None and hi is not None and len(lo) == len(hi):
                    cmap.codespaces.append((lo, hi))
                    i += 2
                    continue
                i += 1
            i += 1

        elif kind == "word" and val == "beginbfchar":
            i += 1
            while i < n and toks[i] != ("word", "endbfchar"):
                src = hex_at(i)
                if src is not None:
                    dst = hex_at(i + 1)
                    if dst is not None:
                        uni = _utf16be(dst, cmap.malformed)
                        if uni is not None:
                            cmap.cid2uni[src] = uni
                        i += 2
                        continue
                i += 1
            i += 1

        elif kind == "word" and val == "beginbfrange":
            i += 1
            while i < n and toks[i] != ("word", "endbfrange"):
                lo, hi = hex_at(i), hex_at(i + 1)
                if lo is not None and hi is not None and len(lo) == len(hi):
                    nxt = toks[i + 2] if i + 2 < n else None
                    lo_i = int.from_bytes(lo, "big")
                    hi_i = int.from_bytes(hi, "big")
                    if nxt is not None and nxt == ("bracket", "["):
                        # Array destination form: <lo> <hi> [<d1> <d2> ...]
                        j = i + 3
                        dests = []
                        while j < n and toks[j] != ("bracket", "]"):
                            if toks[j][0] == "hex":
                                dests.append(_utf16be(toks[j][1], cmap.malformed))
                            j += 1
                        for k, code in enumerate(range(lo_i, hi_i + 1)):
                            if k < len(dests) and dests[k] is not None:
                                cmap.cid2uni[code.to_bytes(len(lo), "big")] = dests[k]
                        i = j + 1
                        continue
                    if nxt is not None and nxt[0] == "hex":
                        # Base form: <lo> <hi> <dst> — increment per code.
                        base = _utf16be(nxt[1], cmap.malformed)
                        if (
                            base is not None
                            and len(base) == 1
                            and hi_i >= lo_i
                            and hi_i - lo_i <= _MAX_RANGE_EXPANSION
                        ):
                            o = ord(base)
                            for c in range(lo_i, hi_i + 1):
                                u = o + (c - lo_i)
                                if u < 0x110000:
                                    cmap.cid2uni[c.to_bytes(len(lo), "big")] = chr(u)
                        i += 3
                        continue
                i += 1
            i += 1
        else:
            i += 1
    return cmap
