"""Text cleaning for extracted and recognised text.

The cleaner never destroys information: callers keep ``raw_text`` and store the
cleaned result in ``clean_text`` (spec 5.2).  Cleaning is deliberately
conservative -- for example a trailing hyphen is only joined when the evidence
says it is a line-break hyphen, because ``state-of-the-art`` and ``well-``
genuinely differ.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "clean_text",
    "join_lines",
    "dehyphenate",
    "normalise_unicode",
    "strip_control_chars",
    "looks_like_garbage",
    "garbage_ratio",
    "count_words",
    "split_sentences",
    "protect_spans",
    "restore_spans",
    "check_placeholders",
    "HYPHEN_GUARD_PREFIXES",
    "COMPOUND_CONNECTORS",
    "PROTECT_PATTERNS",
    "PLACEHOLDER_OPEN",
    "PLACEHOLDER_CLOSE",
]

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
# The marker left where a genuine compound wrapped across a line.  It is a soft
# hyphen (U+00AD); display turns it back into an ordinary hyphen.
_SOFT_HYPHEN_RE = re.compile("\u00ad")

_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "\u2013",   # en dash preserved: meaningful in ranges
    "\u2014": "\u2014",   # em dash preserved
    "\u00ad": "",         # soft hyphen is invisible: drop
}

_HYPHEN_CHARS = "\u2010\u2011-"

# A line-final hyphen is ambiguous: it may be a soft hyphenation point inserted
# by the typesetter, or a genuine hyphen in a compound word that happens to wrap
# (``state-of-`` / ``the-art``).  Joining a genuine compound corrupts the text,
# so those prefixes are guarded and keep their hyphen.
#
# The guard set is intentionally prefix-based rather than a full word list: it
# is cheap, auditable and only needs to cover the frequent cases.  Callers can
# extend it through :func:`dehyphenate`.
HYPHEN_GUARD_PREFIXES: frozenset[str] = frozenset(
    {
        # compound-forming prefixes that are also genuine standalone words
        "state", "self", "well", "high", "low", "long", "short", "large", "small",
        "real", "end", "user", "task", "data", "cross", "multi", "semi", "non",
        "pre", "post", "co", "re", "de", "anti", "inter", "intra", "sub", "super",
        "meta", "quasi", "pseudo", "two", "three", "four", "single", "double",
        "first", "second", "third", "part", "time", "cost", "rule", "case",
        "evidence", "model", "feature", "graph", "image", "text", "open", "closed",
        "in", "out", "up", "down", "on", "off", "over", "under", "mid", "near",
        "follow", "read", "write", "search", "score", "label", "sample",
        "zero", "one", "few", "many", "all", "any", "no", "so", "as", "by",
        "mixed", "dual", "both", "whole", "full", "half", "hard", "soft",
        "left", "right", "top", "bottom", "front", "back", "inner", "outer",
        "upper", "lower", "triple", "hetero", "homo", "iso", "micro", "macro",
        "nano", "thermo", "hydro", "electro", "photo", "bio", "geo", "chemo",
        "neuro", "psycho", "socio", "techno", "human", "machine", "computer",
        "crowd", "web", "cloud", "edge", "fine", "coarse", "deep", "shallow",
        "wide", "narrow", "broad", "fixed", "free", "static", "dynamic",
        "adaptive", "robust", "sparse", "dense", "class", "domain", "source",
        "target", "input", "output", "action", "reward",
    }
)

# Words that frequently appear as the *second* half of a hyphenated compound.
# If the line after a break starts with one of these, the break is a wrapped
# compound ("mixed-" / "language") rather than a soft hyphenation point
# ("random-" / "ized").
COMPOUND_SECOND_WORDS: frozenset[str] = frozenset(
    {
        "language", "modal", "known", "art", "linear", "variate", "to", "end",
        "time", "task", "scale", "level", "order", "party", "step", "class",
        "label", "point", "wise", "based", "driven", "aware", "specific",
        "dependent", "independent", "related", "stage", "layer", "shot",
        "source", "target", "text", "code", "form", "line", "word", "band",
        "field", "frame", "graph", "group", "head", "host", "item", "list",
        "loop", "map", "matrix", "media", "mode", "node", "path", "phase",
        "place", "plane", "pool", "range", "rate", "rule", "rule", "sample",
        "scale", "score", "set", "side", "signal", "site", "size", "space",
        "speed", "state", "stream", "style", "table", "term", "test", "theme",
        "tree", "type", "unit", "value", "vector", "view", "weight", "width",
        "century", "day", "decade", "hour", "month", "week", "year",
        "free", "open", "closed", "ready", "safe", "sensitive", "tolerant",
    }
)

# Function words that appear *inside* genuine hyphenated compounds.  If either
# side of a line break is one of these, the break is a wrapped compound rather
# than a soft hyphenation point.
COMPOUND_CONNECTORS: frozenset[str] = frozenset(
    {
        "of", "to", "the", "and", "or", "for", "in", "on", "at", "by", "with",
        "vs", "per", "into", "from", "as",
    }
)

# Letter, optional trailing spaces, hyphen, optional spaces, newline, optional
# spaces, lowercase letter.
_LINE_HYPHEN_RE = re.compile(
    rf"([^\W\d_])([{_HYPHEN_CHARS}])[ \t]*\n[ \t]*([^\W\d_])",
    re.UNICODE,
)
# Hyphen at end of line followed by an uppercase letter or digit: keep both,
# those are usually genuine compounds or ranges split across lines.
_LINE_HYPHEN_KEEP_RE = re.compile(rf"[{_HYPHEN_CHARS}][ \t]*\n(?=[A-Z0-9])")
# Trailing fragment before the hyphen, used for the compound guard.
_FRAGMENT_RE = re.compile(r"([^\W\d_]+)$", re.UNICODE)
# A line that ends in a word-final hyphen, i.e. letters before the hyphen and no
# digits adjacent to it.  This is what distinguishes a genuine word break
# ("state-of-", "experi-") from a numeric range split across lines ("10-").
_WORD_FINAL_HYPHEN_RE = re.compile(rf"[^\W\d_][{_HYPHEN_CHARS}][ \t]*$", re.UNICODE)

# Placeholder syntax used to protect formulas, citations, DOIs and URLs.
PLACEHOLDER_OPEN = "\u2e24"   # ⸤
PLACEHOLDER_CLOSE = "\u2e25"  # ⸥
_PLACEHOLDER_RE = re.compile(rf"{PLACEHOLDER_OPEN}([A-Za-z0-9_\-:.]+){PLACEHOLDER_CLOSE}")

# Patterns that must survive translation verbatim (spec 5.5 / 6.2).
#
# The word units are guarded by ``(?![A-Za-z0-9])`` rather than ``\b``.  A
# trailing ``\b`` cannot match after ``%`` or ``µ``, because neither is a word
# character, so ``42.5%`` never matched and a percentage was left unprotected --
# exactly the kind of token whose loss changes a scientific claim.  A lookahead
# expresses the intent ("the unit must not be the start of a longer word") without
# depending on what the final character's category happens to be.
PROTECT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("doi", r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"),
    ("url", r"\bhttps?://[^\s<>\"')]+"),
    ("arxiv", r"\barXiv:\s?\d{4}\.\d{4,5}(v\d+)?(?![A-Za-z0-9])"),
    ("citation", r"\[\s?\d+(?:\s?[,\u2013-]\s?\d+)*\s?\]"),
    ("equation_ref", r"\b(?:Eq\.|Equation|Fig\.|Figure|Tab\.|Table|Sec\.|Section|Ref\.)\s?\(?\d+[a-z]?\)?"),
    ("inline_math", r"\$[^$\n]{1,120}\$"),
    ("number_unit", r"\b\d+(?:\.\d+)?\s?(?:%|mmHg|kg|mg|mL|L|mm|cm|m|km|Hz|kHz|MHz|GHz|ms|min|h|K|Pa|kPa|MPa|eV|keV|MeV|GeV|nm|\u00b5m|mol|mM|\u00b5M|nM|bp|kb|GB|MB|kB|g|s)(?![A-Za-z0-9])"),
    ("p_value", r"\bp\s?[<>=]\s?0?\.\d+(?![0-9])"),
)


def drop_soft_hyphens(text: str) -> str:
    """Turn preserved-wrap markers back into ordinary hyphens.

    :func:`dehyphenate` replaces a line-final hyphen with a soft hyphen when it
    cannot tell whether the hyphen was inserted by the typesetter or belongs to
    a genuine compound.  The soft hyphen is the machine-readable evidence that
    a hyphen was there; for display it becomes an ordinary hyphen again, so a
    wrapped compound reads ``state-of-the-art`` and never ``state-of the-art``.
    """
    return _SOFT_HYPHEN_RE.sub("-", text)


def strip_control_chars(text: str) -> str:
    """Remove control characters and the invisible soft hyphen.

    The soft hyphen is the marker :func:`dehyphenate` leaves where a genuine
    compound wrapped across lines.  It is invisible, so removing it here makes
    the *displayed* text read ``state-of-the-art`` while ``raw_text`` still
    records that the break existed.
    """
    return drop_soft_hyphens(_CONTROL_RE.sub("", text))


def normalise_unicode(text: str) -> str:
    """NFKC-normalise and expand presentation ligatures, keeping dashes.

    The soft hyphen is removed *before* normalisation because NFKC maps it to a
    plain hyphen, which would silently turn a wrapped-compound marker into a
    visible character.
    """
    text = drop_soft_hyphens(text)
    text = unicodedata.normalize("NFKC", text)
    return "".join(_LIGATURES.get(ch, ch) for ch in text)


def dehyphenate(text: str, guard: frozenset[str] | None = None) -> str:
    """Join words broken across a line by a hyphen.

    ``hyphen-\\nation`` becomes ``hyphenation``.  The hyphen is kept when:

    * the fragment before the hyphen is a known compound prefix such as
      ``state`` (in ``state-of-`` / ``the-art``) or ``multi``, or
    * the next line starts with an uppercase letter or a digit, which is
      normally a genuine compound or a numeric range split across lines.

    The two cases are not distinguishable from the line break alone, so the
    guard set errs towards preserving the hyphen.  A wrong join corrupts a word
    silently; a preserved hyphen is visible and cheap to review.
    """
    guard_set = HYPHEN_GUARD_PREFIXES if guard is None else guard
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    if len(lines) < 2:
        return text

    def _joinable(current: str, following: str) -> bool:
        if not current or not following:
            return False
        if current[-1] not in _HYPHEN_CHARS:
            return False
        head = current[:-1].rstrip()
        if not head:
            return False
        last_char = head[-1]
        if not (last_char.isalpha() or last_char.isdigit()):
            return False
        stripped_following = following.lstrip()
        first_char = stripped_following[:1]
        if not first_char:
            return False
        if not first_char.islower():
            return False
        frag_match = _FRAGMENT_RE.search(head)
        fragment = frag_match.group(1).lower() if frag_match else ""
        next_tokens = re.findall(r"[^\W\d_]+", stripped_following, re.UNICODE)
        next_word = next_tokens[0].lower() if next_tokens else ""
        # Deciding signal: is the fragment after the break a word that could
        # stand on its own?  "mixed-" / "language" is a compound, whereas
        # "random-" / "ized" is a single word that the typesetter split.
        if next_word in COMPOUND_SECOND_WORDS:
            return False
        if fragment in COMPOUND_CONNECTORS or next_word in COMPOUND_CONNECTORS:
            return False
        # Secondary guard: a known compound-forming prefix, because
        # "state-of-" / "the-art" wraps around words not in the list above.
        return not (fragment in guard_set or next_word in guard_set)

    out: list[str] = []
    buffer = lines[0]
    for index in range(1, len(lines)):
        following = lines[index]
        if _joinable(buffer, following):
            buffer = buffer.rstrip()[:-1] + following.lstrip()
        else:
            # A preserved alphabetic break replaces the visible line-final
            # hyphen with a soft hyphen, so cleaning turns it back into a hyphen
            # and joins without a space ("state-of-the-art", "multi-modal").
            # A numeric range such as "10-" / "15" or "20-" / "30" is not a word
            # break, so it keeps its visible hyphen and the normal
            # space-separated join; :data:`_WORD_FINAL_HYPHEN_RE` encodes that
            # distinction (a letter must end the line before the hyphen).
            # Do not carry a join across a preserved break: a fused fragment
            # must never be re-examined as if it were a fresh line.
            if _WORD_FINAL_HYPHEN_RE.search(buffer):
                out.append(buffer[:-1] + "\u00ad")
            else:
                out.append(buffer)
            buffer = following
    if buffer:
        out.append(buffer)
    return "\n".join(out)


def join_lines(text: str) -> str:
    """Turn hard line breaks inside a paragraph into single spaces.

    Blank lines (real paragraph separators) become ``\\n\\n``; runs of three or
    more newlines collapse.  Lines that clearly end a sentence keep a newline so
    sentence splitting remains useful downstream.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    paragraphs = re.split(r"\n\s*\n", text)
    out: list[str] = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        buf = lines[0]
        for nxt in lines[1:]:
            if not buf:
                buf = nxt
            elif buf.endswith("\u00ad"):
                # A preserved compound break: fuse without a space, so the
                # reader sees "state-of-the-art" rather than "state-of- the-art".
                buf = buf + nxt
            else:
                buf = buf.rstrip() + " " + nxt
        out.append(buf)
    return "\n\n".join(out)


def clean_text(
    text: str,
    *,
    dehyphenate_lines: bool = True,
    join_wrapped_lines: bool = True,
    collapse_whitespace: bool = True,
) -> str:
    """Full cleaning pipeline for one block's extracted text.

    Order matters:

    1. control characters and soft hyphens are removed,
    2. Unicode is normalised and ligatures expanded,
    3. line-break hyphens are resolved -- this *introduces* a soft hyphen where
       a genuine compound wrapped, so it must run before step 4,
    4. wrapped lines are joined, using that marker to fuse compounds without a
       stray space, and
    5. the marker becomes an ordinary hyphen again and whitespace collapses.
    """
    if not text:
        return ""
    result = strip_control_chars(text)
    result = normalise_unicode(result)
    if dehyphenate_lines:
        result = dehyphenate(result)
    if join_wrapped_lines:
        # The soft hyphen marker must survive until after the join, because the
        # join uses it to fuse a wrapped compound without inserting a space.
        result = join_lines(result)
    result = drop_soft_hyphens(result)
    if collapse_whitespace:
        result = _WS_RE.sub(" ", result)
        result = re.sub(r" *\n *", "\n", result)
    return result.strip()


def protect_spans(text: str) -> tuple[str, list[dict[str, str]]]:
    """Replace non-translatable tokens with placeholders.

    Returns the rewritten text and the list of protected values in the order
    they were replaced, so the caller can verify completeness (spec 5.5).
    """
    protected: list[dict[str, str]] = []
    result = text

    def _stash(kind: str, value: str) -> str:
        idx = len(protected)
        protected.append({"kind": kind, "value": value, "token": f"{kind}_{idx}"})
        return f"{PLACEHOLDER_OPEN}{kind}_{idx}{PLACEHOLDER_CLOSE}"

    # Longest-match-first matters: URLs contain DOIs and citation brackets can
    # sit inside figure references.
    ordered = sorted(PROTECT_PATTERNS, key=lambda kv: -len(kv[1]))
    for kind, pattern in ordered:
        result = re.sub(pattern, lambda m, k=kind: _stash(k, m.group(0)), result)
    return result, protected


def restore_spans(text: str, protected: list[dict[str, str]]) -> str:
    """Substitute placeholders back with their original values."""

    def _restore(match: re.Match[str]) -> str:
        token = match.group(1)
        for item in protected:
            if item["token"] == token:
                return item["value"]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_restore, text)


def check_placeholders(text: str, protected: list[dict[str, str]]) -> dict:
    """Verify every placeholder is present exactly once.

    This is the mechanical part of the "protected content" acceptance metric;
    it never claims that the surrounding translation is good, only that the
    protected tokens survived.
    """
    found = _PLACEHOLDER_RE.findall(text)
    expected = [item["token"] for item in protected]
    missing = [t for t in expected if t not in found]
    duplicated = [t for t in set(found) if found.count(t) > 1]
    extra = [t for t in found if t not in expected]
    return {
        "ok": not missing and not duplicated and not extra,
        "expected": expected,
        "found": found,
        "missing": missing,
        "duplicated": duplicated,
        "extra": extra,
    }


def count_words(text: str) -> int:
    """Word count that treats CJK characters individually.

    Used for cost estimation, so it must not silently return 0 for Chinese
    input the way a naive ``split()`` would.
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]", " ", text)
    latin = len(re.findall(r"[A-Za-z0-9\u00c0-\u024f][A-Za-z0-9\u00c0-\u024f'\u2019\-]*", non_cjk))
    return cjk + latin


# A sentence ends at a terminator followed by whitespace (Latin convention) or at
# a CJK terminator, which is followed directly by the next sentence because CJK
# text does not separate sentences with spaces.  Requiring whitespace after every
# terminator meant "这是第一句。这是第二句。" was never split at all.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f\uff0e])\s*")


def split_sentences(text: str) -> list[str]:
    """Split into sentences, avoiding common abbreviations.

    Deliberately simple: the goal is to bound very long blocks, not to be a
    linguistically perfect sentence segmenter.
    """
    if not text.strip():
        return []
    abbreviations = {
        "e.g.", "i.e.", "et al.", "cf.", "vs.", "Fig.", "Eq.", "Ref.", "No.",
        "approx.", "Dr.", "Prof.", "Mr.", "Ms.", "Sec.", "Tab.", "resp.",
    }
    guarded = text
    stash: list[str] = []
    for abbr in sorted(abbreviations, key=len, reverse=True):
        pattern = re.compile(re.escape(abbr), re.IGNORECASE)
        while True:
            m = pattern.search(guarded)
            if not m:
                break
            stash.append(m.group(0))
            guarded = guarded[: m.start()] + f"\x01{len(stash) - 1}\x01" + guarded[m.end() :]
    parts = _SENTENCE_END_RE.split(guarded)

    def _restore(piece: str) -> str:
        return re.sub(r"\x01(\d+)\x01", lambda m: stash[int(m.group(1))], piece)

    return [_restore(p).strip() for p in parts if _restore(p).strip()]


_GARBAGE_CHARS = set("\ufffd\u25a1\u2588\u2591\u2592\u2593")


def garbage_ratio(text: str) -> float:
    """Fraction of characters that indicate a broken text layer or bad OCR."""
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch in _GARBAGE_CHARS or (ord(ch) < 32 and ch not in "\n\t"))
    return bad / len(text)


def looks_like_garbage(text: str, threshold: float = 0.05, min_chars: int = 20) -> bool:
    """Heuristic used to reject a "text layer" that exists but is unusable.

    Spec 5.2: the presence of a text layer must not by itself be treated as
    usable content.
    """
    stripped = text.strip()
    if len(stripped) < min_chars:
        return True
    if garbage_ratio(stripped) > threshold:
        return True
    # Almost no letters or digits anywhere: probably symbol soup.
    alnum = sum(1 for ch in stripped if ch.isalnum())
    return alnum / len(stripped) < 0.25
