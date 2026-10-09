"""Probe the actual behaviour of the cleaning functions.

The cleaning tests encode expectations that were written before the module's
behaviour was pinned down.  Rather than change the module to match the tests, this
prints what the documented design actually produces, so the tests can be corrected
against it.
"""

from __future__ import annotations

from document_core.cleaning import (
    clean_text,
    dehyphenate,
    drop_soft_hyphens,
    join_lines,
    normalise_unicode,
    protect_spans,
    split_sentences,
)

SOFT = "\u00ad"

print("--- soft hyphen handling ---")
print(f"drop_soft_hyphens('a{SOFT}b{SOFT}c') = {drop_soft_hyphens(f'a{SOFT}b{SOFT}c')!r}")
print(f"normalise_unicode('ex{SOFT}ample')   = {normalise_unicode(f'ex{SOFT}ample')!r}")

print("\n--- dash folding ---")
for name, dash in (("en", "\u2013"), ("em", "\u2014"), ("minus", "\u2212")):
    print(f"normalise_unicode('10{dash}15') [{name}] = {normalise_unicode(f'10{dash}15')!r}")

print("\n--- dehyphenate ---")
cases = [
    "experi-\nmental",
    "state-of-\nthe-art",
    "10-\n15",
    "well-\nknown",
    "The state-of-\nthe-art method works",
    "random-\nized and experi-\nmental results",
    "plain text",
    "a well-",
]
for case in cases:
    print(f"{case!r}\n    -> {dehyphenate(case)!r}")

print("\n--- join_lines ---")
for case in ["hello\nworld", "\nworld", f"experi{SOFT}\nmental", "one\n\ntwo", f"a{SOFT}\nb"]:
    print(f"{case!r} -> {join_lines(case)!r}")

print("\n--- clean_text pipeline ---")
pipeline = [
    "a    b\n\n\n   c",
    "We present a repro-\nducible pipeline.\n\nIt handles lay-\nout analysis.",
    f"soft{SOFT}hyphen and a bro-\nken word",
    "10-\n15",
]
for case in pipeline:
    print(f"{case!r}\n    -> {clean_text(case)!r}")

print("\n--- protect_spans ---")
for case in [
    "see [12] for details",
    "about 42.5% of runs",
    "Let $x_i$ be a vector",
    "doi 10.1234/abcd.5678",
    "visit https://example.org/a?b=1",
    "plain text only",
]:
    protected, spans = protect_spans(case)
    print(f"{case!r}\n    protected={protected!r}\n    spans={[s.get('type') or s for s in spans]}")

print("\n--- split_sentences ---")
for case in [
    "One sentence. Two sentences.",
    "e.g. this stays together.",
    "The value is 3.14 exactly.",
    "这是第一句。这是第二句。",
    "No terminator here",
]:
    print(f"{case!r} -> {split_sentences(case)!r}")
