"""Structural views of a LaTeX resume, for checking what an edit changed.

Deliberately approximate: no LaTeX parser, just brace-balanced argument
extraction for the handful of commands resumes actually use. Good enough to
tell "reworded a bullet" from "moved a job" or "changed a date".
"""

from __future__ import annotations

import re
from collections import Counter

_BEGIN_DOC = "\\begin{document}"
_SECTION_RE = re.compile(r"\\section\*?\s*\{")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE_RE = re.compile(rf"(?:{_MONTHS}\s+\d{{4}}|(?:Spring|Summer|Fall|Winter)\s+\d{{4}}|\b(?:19|20)\d{{2}}\b|\bPresent\b)")
_SUMMARY_RE = re.compile(r"^(summary|objective|profile|about( me)?)$", re.IGNORECASE)

# Commands whose arguments are facts (employer, title, dates, location) that
# personalization must not change.
HEADING_COMMANDS = {"resumeSubheading": 4, "resumeProjectHeading": 2, "resumeSubSubheading": 2}
BULLET_COMMANDS = ("resumeItem", "resumeSubItem")


def strip_comments(tex: str) -> str:
    return _COMMENT_RE.sub("", tex)


def split(tex: str) -> tuple[str, str]:
    """(preamble, body). Body is everything from \\begin{document} on."""
    i = tex.find(_BEGIN_DOC)
    return (tex, "") if i < 0 else (tex[:i], tex[i:])


def _read_group(s: str, i: int) -> tuple[str, int]:
    """Read a {...} group starting at s[i] == '{'. Returns (inner, end_index)."""
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return s[i + 1 :], len(s)


def command_args(tex: str, name: str, nargs: int) -> list[tuple[str, ...]]:
    out = []
    for m in re.finditer(rf"\\{name}(?![A-Za-z])", tex):
        j, args = m.end(), []
        for _ in range(nargs):
            while j < len(tex) and tex[j].isspace():
                j += 1
            if j >= len(tex) or tex[j] != "{":
                break
            arg, j = _read_group(tex, j)
            args.append(arg)
        if len(args) == nargs:
            out.append(tuple(args))
    return out


def sections(tex: str) -> list[str]:
    body = strip_comments(split(tex)[1])
    out = []
    for m in _SECTION_RE.finditer(body):
        title, _ = _read_group(body, m.end() - 1)
        out.append(title.strip())
    return out


def has_summary_section(tex: str) -> bool:
    return any(_SUMMARY_RE.match(to_plain(s)) for s in sections(tex))


def headings(tex: str) -> Counter:
    body = strip_comments(split(tex)[1])
    c: Counter = Counter()
    for name, n in HEADING_COMMANDS.items():
        for args in command_args(body, name, n):
            c[(name, *(_squash(a) for a in args))] += 1
    return c


def bullets(tex: str) -> list[str]:
    """Bullet text: \\resumeItem{...} args if the template uses them,
    otherwise the text after each bare \\item."""
    body = strip_comments(split(tex)[1])
    found = [a[0] for name in BULLET_COMMANDS for a in command_args(body, name, 1)]
    if found:
        return [_squash(b) for b in found]
    items = re.split(r"\\item(?![A-Za-z])", body)[1:]
    return [_squash(re.split(r"\\end\{itemize\}|\\begin\{itemize\}", it)[0]) for it in items if it.strip()]


def dates(tex: str) -> Counter:
    return Counter(_DATE_RE.findall(to_plain(split(tex)[1])))


def to_plain(tex: str) -> str:
    """Rough human-readable text, for keyword searches."""
    s = strip_comments(tex)
    s = re.sub(r"\\href\{[^}]*\}", "", s)
    s = re.sub(r"\\([&%$#_{}])", r"\1", s)
    s = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?", " ", s)
    s = s.replace("{", " ").replace("}", " ").replace("$|$", " ").replace("--", "-")
    return re.sub(r"\s+", " ", s).strip()


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
