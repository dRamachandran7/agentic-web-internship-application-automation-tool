"""Fuzzy term matching shared by the scorers.

Extracted skills come back in many surface forms ("REST APIs" vs "RESTful
API design", "Python (pandas)" vs "pandas"). Exact matching would score
those as misses, so matching is: normalize, then accept either an alias hit
or whole-word containment in either direction.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence, Union

GoldItem = Union[str, Sequence[str]]  # canonical name, or [canonical, *aliases]

_PUNCT_RE = re.compile(r"[^a-z0-9+#/ ]+")
_SPACE_RE = re.compile(r"\s+")


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    # Cheap singularization so "APIs" matches "API".
    return " ".join(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in s.split())


def aliases(item: GoldItem) -> list[str]:
    return [item] if isinstance(item, str) else list(item)


def canonical(item: GoldItem) -> str:
    return aliases(item)[0]


def contains_term(text: str, term: str) -> bool:
    """Whole-word match of `term` inside `text` (both normalized here)."""
    t, n = norm(text), norm(term)
    if not n:
        return False
    return re.search(rf"(?<![a-z0-9+#]){re.escape(n)}(?![a-z0-9+#])", t) is not None


def term_matches(predicted: str, item: GoldItem) -> bool:
    """A verbose prediction may contain the gold term ("Python (pandas)" ->
    "Python"). A terse prediction only matches inside a longer gold label if
    it covers at least half its words: "REST" matches "REST APIs", but
    "React" must not match "React Testing Library"."""
    p = norm(predicted)
    for a in aliases(item):
        n = norm(a)
        if not n or not p:
            continue
        if p == n or contains_term(p, n):
            return True
        if contains_term(n, p) and len(p.split()) * 2 >= len(n.split()):
            return True
    return False


def match_any(predicted: str, items: Iterable[GoldItem]) -> bool:
    return any(term_matches(predicted, it) for it in items)


_SUFFIXES = ("ship", "ing", "ize", "ise", "ed", "es", "s")


def _stem(t: str) -> str:
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def _same_word(a: str, b: str) -> bool:
    """Word-form variants: tests/testing, intern/internship,
    productionize/productionizing, migrate/migration."""
    a, b = _stem(a), _stem(b)
    if a == b:
        return True
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n >= 5 and n >= min(len(a), len(b)) - 2


def grounded(term: str, source: str, min_token_overlap: float = 0.6) -> bool:
    """Is `term` supported by `source`? Exact containment, or most of its
    content tokens appear in the source, allowing word-form variants
    ("unit testing" is grounded by "unit tests"). Looser than term_matches on
    purpose: this asks "did the posting say this?", not "is this the same item?"."""
    if contains_term(source, term):
        return True
    tokens = [t for t in re.split(r"[ /]", norm(term)) if len(t) > 2]
    if not tokens:
        return False
    src_tokens = set(re.split(r"[ /]", norm(source)))
    hits = sum(1 for t in tokens if any(_same_word(t, s) for s in src_tokens))
    return hits / len(tokens) >= min_token_overlap
