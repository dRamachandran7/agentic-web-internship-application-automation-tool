"""Scoring for personalize_resume: did the edit stay inside the rules in the
service's system prompt, and did it actually tailor anything?

Hard constraints (any failure fails the case) map one-to-one to the prompt:
- compiles: Tectonic builds a PDF (via the service's own compile_pdf).
- preamble_preserved: nothing before \\begin{document} changed.
- sections_preserved: same \\section titles in the same order.
- headings_preserved: employers/titles/dates/locations in \\resumeSubheading
  and \\resumeProjectHeading are untouched.
- dates_preserved: same multiset of dates in the body.
- bullet_count_delta == 0: bullets reworded/reordered, not added or dropped.
- unsupported_terms_added: listing skills the original resume never mentions
  must not appear in the output. The main fabrication signal.
- summary_added: no new Summary/Objective section.

Soft signals (reported, not gated):
- edit_rate: share of original bullets reworded. ~0 means the model did nothing.
- coverage_delta: change in share of listing terms the resume mentions.
- prominence_delta: how much earlier listing terms appear (positive = moved up).
- length_ratio: output body length / original.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import latex
from .text import contains_term, grounded, norm


def listing_terms(values: dict[str, Any]) -> list[str]:
    seen, out = set(), []
    for t in values.get("required_skills", []) + values.get("preferred_skills", []) + values.get("keywords", []):
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _squash_tex(tex: str) -> str:
    """Comments and whitespace don't change the compiled document."""
    return " ".join(latex.strip_comments(tex).split())


def _first_position(plain: str, term: str) -> float | None:
    n, t = norm(plain), norm(term)
    i = n.find(t)
    return None if i < 0 or not n else i / len(n)


def score(original: str, output: str, values: dict[str, Any], compiled: bool | None) -> dict[str, Any]:
    orig_pre, orig_body = latex.split(original)
    out_pre, out_body = latex.split(output)
    orig_plain, out_plain = latex.to_plain(orig_body), latex.to_plain(out_body)

    terms = listing_terms(values)
    # "Supported" uses the same word-form-tolerant check as analyze's
    # grounding: a "data structures course" supports "data structures and
    # algorithms"; nothing in the resume supports "linear algebra".
    in_orig = [t for t in terms if grounded(t, orig_plain)]
    unsupported_added = [t for t in terms if t not in in_orig and contains_term(out_plain, t)]

    orig_bullets, out_bullets = latex.bullets(original), latex.bullets(output)
    out_bullet_set = set(out_bullets)
    unchanged = sum(1 for b in orig_bullets if b in out_bullet_set)

    def coverage(plain: str) -> float:
        return sum(1 for t in terms if contains_term(plain, t)) / len(terms) if terms else 0.0

    deltas = []
    for t in in_orig:
        a, b = _first_position(orig_plain, t), _first_position(out_plain, t)
        if a is not None and b is not None:
            deltas.append(a - b)

    m = {
        "compiles": compiled,
        "preamble_preserved": _squash_tex(orig_pre) == _squash_tex(out_pre),
        "sections_preserved": latex.sections(original) == latex.sections(output),
        "headings_preserved": latex.headings(original) == latex.headings(output),
        "dates_preserved": latex.dates(original) == latex.dates(output),
        "bullet_count_delta": len(out_bullets) - len(orig_bullets),
        "unsupported_terms_added": unsupported_added,
        "summary_added": not latex.has_summary_section(original) and latex.has_summary_section(output),
        "edit_rate": 0.0 if not orig_bullets else 1 - unchanged / len(orig_bullets),
        "coverage_delta": coverage(out_plain) - coverage(orig_plain),
        "prominence_delta": sum(deltas) / len(deltas) if deltas else 0.0,
        "length_ratio": len(out_plain) / max(len(orig_plain), 1),
    }
    m["pass"] = (
        m["compiles"] is not False
        and m["preamble_preserved"]
        and m["sections_preserved"]
        and m["headings_preserved"]
        and m["dates_preserved"]
        and m["bullet_count_delta"] == 0
        and not m["unsupported_terms_added"]
        and not m["summary_added"]
    )
    return m


def values_from_gold(listing_case: dict) -> dict[str, Any]:
    """Build personalize_resume's `values` input from a listing's gold labels,
    so this stage is scored independently of analyze_listing's accuracy."""
    g = listing_case["gold"]
    first = lambda items: [i if isinstance(i, str) else i[0] for i in items]  # noqa: E731
    return {
        "required_skills": first(g.get("required_skills", [])),
        "preferred_skills": first(g.get("preferred_skills", [])),
        "keywords": first(g.get("keywords", [])),
        "values": g.get("values", []),
        "summary": g.get("summary", ""),
    }


async def run_case(llm_module, render_module, case: dict, compile_pdf: bool) -> dict[str, Any]:
    values_model = llm_module.ListingValues.model_validate(case["values"])
    output = await llm_module.personalize(case["resume"], values_model)

    compiled: bool | None = None
    compile_error = None
    if compile_pdf:
        try:
            await asyncio.to_thread(render_module.compile_pdf, output)
            compiled = True
        except render_module.RenderError as exc:
            compiled, compile_error = False, str(exc)[-2000:]

    metrics = score(case["resume"], output, case["values"], compiled)
    if compile_error:
        metrics["compile_error"] = compile_error
    return {"output": output, "metrics": metrics}
