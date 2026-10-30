"""Stand-ins for the backend services, for testing the frontend orchestrator.

Two uses:
1. Real-LLM end-to-end without Handshake: point only SEARCH_LISTINGS_URL here
   and the dataset listings flow through the real analyze/personalize services.
2. Contract tests with no LLM at all: point all three URLs here. analyze
   returns gold values, personalize echoes the resume, and magic strings
   trigger failures:
     keywords "__auth_fail__"          -> search returns 401
     a listing title with "[fail]"     -> analyze returns 502 for that listing

    python -m evals.e2e.fake_services --port 8100
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
_LISTINGS = [json.loads(p.read_text()) for p in sorted((DATASETS / "listings").glob("*.json"))]
_BY_ID = {c["input"]["listingId"]: c for c in _LISTINGS}

# A tiny valid PDF so the frontend's download path has real bytes to handle.
_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

app = FastAPI(title="fake pipeline services")


@app.get("/health")
def health():
    return {"status": "ok", "storage_state_present": True, "groq_api_key_present": True}


class _Search(BaseModel):
    params: dict


@app.post("/search-listings")
def search(req: _Search):
    kw = (req.params.get("keywords") or "").lower()
    if "__auth_fail__" in kw:
        raise HTTPException(401, "Handshake session expired (fake)")
    listings = [c["input"] for c in _LISTINGS]
    if "__with_failure__" in kw:
        bad = dict(listings[0], listingId="eval-fail", title=listings[0]["title"] + " [fail]")
        listings = [bad] + listings
    return {"listings": listings}


@app.post("/analyze-listing")
def analyze(req: dict):
    if "[fail]" in req.get("title", ""):
        raise HTTPException(502, "Groq request failed: fake upstream error")
    case = _BY_ID.get(req.get("listingId"))
    g = case["gold"] if case else {}
    first = lambda items: [i if isinstance(i, str) else i[0] for i in items]  # noqa: E731
    return {"values": {
        "required_skills": first(g.get("required_skills", [])),
        "preferred_skills": first(g.get("preferred_skills", [])),
        "keywords": [],
        "values": g.get("values", []),
        "summary": g.get("summary", ""),
    }}


@app.post("/personalize-resume")
def personalize(req: dict):
    return {
        "resumeFile": base64.b64encode(_PDF).decode(),
        "filename": "resume.pdf",
        "resumeLatex": req["resumeLatex"],
        "latexFilename": "resume.tex",
    }


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    uvicorn.run(app, host="127.0.0.1", port=ap.parse_args().port)
