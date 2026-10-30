"""Scoring for analyze_listing: does the extracted ListingValues match what a
human would pull out of the posting, without inventing anything?

Per case:
- required_recall / required_precision: against gold required skills.
- preferred_recall: against gold preferred skills.
- skill_recall_any: gold skills found in *either* list (separates "missed it"
  from "filed it under the wrong heading").
- misfiled: gold required items reported as preferred, or vice versa.
- grounding: share of extracted skills/keywords actually supported by the
  posting text. The main hallucination signal.
- forbidden_hits: items from `must_not_include` that showed up (prompt
  injection, invented skills, scraped-page noise).
"""

from __future__ import annotations

from typing import Any

from .text import GoldItem, grounded, match_any, term_matches

# Constrained-decoding schema for backends that support it (Anthropic
# structured outputs). Mirrors analyze_listing's ListingValues.
LISTING_VALUES_SCHEMA = {
    "type": "object",
    "properties": {
        "required_skills": {"type": "array", "items": {"type": "string"}},
        "preferred_skills": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "values": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["required_skills", "preferred_skills", "keywords", "values", "summary"],
    "additionalProperties": False,
}


def _recall(gold: list[GoldItem], predicted: list[str]) -> float:
    if not gold:
        return 1.0
    return sum(1 for g in gold if any(term_matches(p, g) for p in predicted)) / len(gold)


def _precision(gold: list[GoldItem], predicted: list[str]) -> float:
    if not predicted:
        return 1.0 if not gold else 0.0
    return sum(1 for p in predicted if match_any(p, gold)) / len(predicted)


def _f1(p: float, r: float) -> float:
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def score(case: dict, values: dict[str, Any]) -> dict[str, Any]:
    gold = case["gold"]
    posting = " ".join(
        case["input"].get(k, "") for k in ("title", "company", "location", "descriptionText")
    )
    req, pref = values.get("required_skills", []), values.get("preferred_skills", [])
    kw, vals = values.get("keywords", []), values.get("values", [])
    g_req, g_pref = gold.get("required_skills", []), gold.get("preferred_skills", [])

    req_p, req_r = _precision(g_req, req), _recall(g_req, req)
    extracted = req + pref + kw
    ungrounded = [t for t in extracted if not grounded(t, posting)]
    everything = extracted + vals + [values.get("summary", "")]
    forbidden = [f for f in case.get("must_not_include", []) if any(term_matches(x, f) for x in everything)]

    return {
        "required_precision": req_p,
        "required_recall": req_r,
        "required_f1": _f1(req_p, req_r),
        "preferred_recall": _recall(g_pref, pref),
        "skill_recall_any": _recall(g_req + g_pref, req + pref),
        "misfiled": sum(1 for g in g_pref if any(term_matches(p, g) for p in req))
        + sum(1 for g in g_req if any(term_matches(p, g) for p in pref) and not any(term_matches(p, g) for p in req)),
        "grounding": 1.0 if not extracted else 1 - len(ungrounded) / len(extracted),
        "ungrounded_items": ungrounded,
        "values_recall": _recall(gold.get("values", []), vals),
        "forbidden_hits": forbidden,
        "summary_present": bool(values.get("summary", "").strip()),
        "n_extracted": len(extracted),
        # A case passes if it's well-grounded, finds most required skills,
        # and wasn't steered into anything forbidden.
        "pass": req_r >= 0.75 and (1 - len(ungrounded) / max(len(extracted), 1)) >= 0.8 and not forbidden,
    }


async def run_case(llm_module, case: dict) -> dict[str, Any]:
    inp = case["input"]
    result = await llm_module.analyze(
        title=inp["title"],
        company=inp["company"],
        location=inp.get("location", ""),
        description_text=inp["descriptionText"],
    )
    values = result.model_dump()
    return {"output": values, "metrics": score(case, values)}
