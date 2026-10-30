"""Drives the frontend's /api/run orchestrator and checks the whole flow.

    # Contract tests (no LLM): frontend pointed at fake_services for all three
    python -m evals.e2e.run_e2e contract --frontend http://localhost:3000

    # Full pipeline with real LLM services, fake search only
    python -m evals.e2e.run_e2e pipeline --frontend http://localhost:3000 --max-listings 3

Contract scenarios check orchestration logic the unit stages can't see:
event ordering, maxListings, per-listing error isolation, fatal-on-search-
failure, and input validation. `pipeline` additionally scores every
personalized resume with the same checks as the personalize stage, using
the analysis the pipeline itself produced.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from ..harness import personalize_eval

RESUMES = Path(__file__).resolve().parents[1] / "datasets" / "resumes"


def run(frontend: str, body: dict, timeout: float = 900) -> tuple[int, list[dict]]:
    events = []
    with httpx.stream("POST", f"{frontend}/api/run", json=body, timeout=timeout) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, [{"type": "http_error", "body": r.text}]
        for line in r.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return 200, events


def check_grammar(events: list[dict]) -> list[str]:
    """Problems with the event stream's shape. Empty list = well-formed."""
    errs = []
    if not events:
        return ["no events"]
    if events[-1]["type"] not in ("done", "fatal"):
        errs.append(f"stream ended with {events[-1]['type']!r}, not done/fatal")
    if sum(e["type"] in ("done", "fatal") for e in events) != 1:
        errs.append("expected exactly one terminal event")
    order = {"analyzing": 0, "personalizing": 1, "done": 2, "error": 2}
    last: dict[str, int] = {}
    for e in events:
        if e["type"] != "listing":
            continue
        lid, st = e["listing"]["listingId"], e["listing"]["status"]
        if lid in last and order[st] < last[lid]:
            errs.append(f"{lid}: status went backwards to {st}")
        if lid in last and last[lid] == 2:
            errs.append(f"{lid}: event after terminal status")
        last[lid] = order[st]
        if st == "done":
            l = e["listing"]
            for k in ("resumeFile", "resumeLatex", "values"):
                if not l.get(k):
                    errs.append(f"{lid}: done without {k}")
            if l.get("resumeFile"):
                try:
                    if not base64.b64decode(l["resumeFile"]).startswith(b"%PDF"):
                        errs.append(f"{lid}: resumeFile is not a PDF")
                except Exception:
                    errs.append(f"{lid}: resumeFile is not base64")
    if any(v < 2 for v in last.values()) and events[-1]["type"] == "done":
        errs.append("a listing never reached done/error")
    return errs


def final_listings(events: list[dict]) -> dict[str, dict]:
    out = {}
    for e in events:
        if e["type"] == "listing":
            out[e["listing"]["listingId"]] = e["listing"]
    return out


def contract(frontend: str) -> int:
    resume = (RESUMES / "jake_swe.tex").read_text()
    results: list[tuple[str, bool, str]] = []

    def scenario(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))
        print(f"  [{'pass' if ok else 'FAIL'}] {name} {detail}")

    status, ev = run(frontend, {"resumeLatex": resume, "keywords": "intern", "maxListings": 2})
    errs = check_grammar(ev)
    fl = final_listings(ev)
    scenario("happy path: well-formed stream", status == 200 and not errs, "; ".join(errs))
    scenario("happy path: maxListings respected", len(fl) == 2, f"got {len(fl)}")
    scenario("happy path: all done", all(l["status"] == "done" for l in fl.values()))

    status, ev = run(frontend, {"resumeLatex": resume, "keywords": "intern __with_failure__", "maxListings": 3})
    fl = final_listings(ev)
    statuses = [fl[k]["status"] for k in fl]
    scenario("one listing fails, others continue",
             statuses.count("error") == 1 and statuses.count("done") == 2 and ev[-1]["type"] == "done",
             str(statuses))

    status, ev = run(frontend, {"resumeLatex": resume, "keywords": "__auth_fail__"})
    scenario("search 401 -> single fatal event",
             ev[-1]["type"] == "fatal" and "401" in ev[-1].get("message", "") and not final_listings(ev),
             ev[-1].get("message", "")[:80])

    status, _ = run(frontend, {"resumeLatex": "", "keywords": "intern"})
    scenario("missing resume -> 400", status == 400, f"got {status}")
    status, _ = run(frontend, {"resumeLatex": resume, "keywords": "  "})
    scenario("blank keywords -> 400", status == 400, f"got {status}")

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} contract checks passed")
    return 1 if failed else 0


def pipeline(frontend: str, max_listings: int, resume_name: str, out: Path | None) -> int:
    resume = (RESUMES / f"{resume_name}.tex").read_text()
    t0 = time.perf_counter()
    status, ev = run(frontend, {"resumeLatex": resume, "keywords": "intern", "maxListings": max_listings})
    elapsed = time.perf_counter() - t0
    errs = check_grammar(ev)
    print(f"stream: {'ok' if not errs else errs}  ({elapsed:.0f}s)")

    rows: list[dict[str, Any]] = []
    for lid, l in final_listings(ev).items():
        if l["status"] != "done":
            rows.append({"listing": lid, "status": l["status"], "error": l.get("error")})
            print(f"  [ERR ] {lid}: {l.get('error')}")
            continue
        m = personalize_eval.score(resume, l["resumeLatex"], l["values"], compiled=True)
        rows.append({"listing": lid, "status": "done", "metrics": m})
        print(f"  [{'pass' if m['pass'] else 'FAIL'}] {lid}  edit_rate={m['edit_rate']:.2f} "
              f"added={m['unsupported_terms_added']}")
    if out:
        out.write_text(json.dumps({"elapsed_s": elapsed, "grammar_errors": errs, "rows": rows}, indent=2))
    passed = sum(1 for r in rows if r.get("metrics", {}).get("pass"))
    print(f"\n{passed}/{len(rows)} listings passed end-to-end")
    return 0 if not errs and passed == len(rows) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.e2e.run_e2e")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contract")
    c.add_argument("--frontend", default="http://localhost:3000")
    p = sub.add_parser("pipeline")
    p.add_argument("--frontend", default="http://localhost:3000")
    p.add_argument("--max-listings", type=int, default=3)
    p.add_argument("--resume", default="jake_swe", help="stem of a file in datasets/resumes")
    p.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "contract":
        return contract(args.frontend)
    return pipeline(args.frontend, args.max_listings, args.resume, args.out)


if __name__ == "__main__":
    sys.exit(main())
