"""Stable identifier and hashing helpers.

Block ids must be stable **within a parse revision** and reproducible across
re-parses of an unchanged document, so they are derived deterministically from
the parse revision id, the page index and the reading-order index rather than
from a random source.  That property is what lets annotations be migrated
between revisions by identity comparison (see ``document_core.revision``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = [
    "new_id",
    "hash_bytes",
    "hash_text",
    "content_hash",
    "block_id",
    "span_id",
    "page_id",
    "slug",
    "normalise_for_hash",
    "normalise_for_match",
    "shingles",
    "jaccard",
]

_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def new_id(prefix: str, length: int = 20) -> str:
    """Random, URL-safe, prefixed identifier (for entities, not for blocks)."""
    import secrets

    raw = secrets.token_bytes(length)
    num = int.from_bytes(raw, "big")
    out = []
    while num:
        num, rem = divmod(num, 36)
        out.append(_ID_ALPHABET[rem])
    token = "".join(reversed(out))[:length].rjust(length, "0")
    return f"{prefix}_{token}"


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


def content_hash(*parts: object) -> str:
    """Hash an ordered tuple of values into a short, stable digest."""
    payload = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def block_id(parse_revision_id: str, page_index: int, order: int) -> str:
    digest = content_hash("block", parse_revision_id, page_index, order)[:24]
    return f"blk_{digest}"


def span_id(block_id_value: str, ordinal: int) -> str:
    digest = content_hash("span", block_id_value, ordinal)[:20]
    return f"spn_{digest}"


def page_id(parse_revision_id: str, page_index: int) -> str:
    digest = content_hash("page", parse_revision_id, page_index)[:20]
    return f"pg_{digest}"


_WS_RE = re.compile(r"\s+")
_HYPHEN_BREAK_RE = re.compile(r"(\w)[\u2010\u2011\-]\s*\n\s*(\w)")
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}


def normalise_for_hash(text: str) -> str:
    """Aggressive normalisation used only for cache keys and equality checks."""
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip()


def normalise_for_match(text: str) -> str:
    """Normalisation for fuzzy block matching between parse revisions."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(_LIGATURES.get(ch, ch) for ch in text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return _WS_RE.sub(" ", text).strip()


def slug(text: str, limit: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return (text[:limit] or "untitled").lower()


def shingles(text: str, n: int = 5) -> set[str]:
    """Word n-grams of the match-normalised text, for similarity scoring."""
    words = normalise_for_match(text).split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)
