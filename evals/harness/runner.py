"""Runs a stage's cases against one or more models and writes results.

Output layout (one directory per run):
    cases.jsonl   one line per (model, case, repeat): output, metrics, usage, error
    summary.json  per-model aggregates
    report.md     the same aggregates as a table
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import analyze_eval, personalize_eval, providers, services

EVALS_DIR = Path(__file__).resolve().parents[1]
DATASETS = EVALS_DIR / "datasets"


# --- datasets ---------------------------------------------------------------

def load_listing_cases(tags: set[str] | None = None) -> list[dict]:
    cases = [json.loads(p.read_text()) for p in sorted((DATASETS / "listings").glob("*.json"))]
    return [c for c in cases if not tags or tags & set(c.get("tags", []))]


def load_personalize_cases(tags: set[str] | None = None) -> list[dict]:
    """Every resume x every listing, with the listing's gold values as input."""
    resumes = {p.stem: p.read_text() for p in sorted((DATASETS / "resumes").glob("*.tex"))}
    cases = []
    for listing in load_listing_cases(tags):
        for rname, tex in resumes.items():
            cases.append({
                "id": f"{rname}__{listing['id']}",
                "tags": listing.get("tags", []),
                "resume": tex,
                "values": personalize_eval.values_from_gold(listing),
            })
    return cases


# --- running ----------------------------------------------------------------

async def _run_one(spec: providers.ModelSpec, case: dict, repeat: int,
                   fn: Callable[[dict], Awaitable[dict]]) -> dict[str, Any]:
    calls: list[providers.CallRecord] = []
    providers.CALLS.set(calls)
    start = time.perf_counter()
    row: dict[str, Any] = {"model": spec.name, "case": case["id"], "repeat": repeat, "tags": case.get("tags", [])}
    try:
        row.update(await fn(case))
    except Exception as exc:  # noqa: BLE001 - every failure is a data point
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["error_trace"] = traceback.format_exc(limit=3)
    row["latency_s"] = time.perf_counter() - start
    row["input_tokens"] = sum(c.input_tokens for c in calls)
    row["output_tokens"] = sum(c.output_tokens for c in calls)
    row["stop_reasons"] = [c.stop_reason for c in calls]
    row["cost_usd"] = spec.cost(row["input_tokens"], row["output_tokens"])
    status = "ERR " if "error" in row else ("pass" if row["metrics"].get("pass") else "FAIL")
    print(f"  [{status}] {spec.name:28s} {case['id']:40s} r{repeat} {row['latency_s']:.1f}s", flush=True)
    return row


async def run_stage(stage: str, specs: list[providers.ModelSpec], *, repeats: int = 1,
                    tags: set[str] | None = None, limit: int | None = None,
                    compile_pdf: bool = True, judge_model: str | None = None) -> list[dict]:
    if stage == "analyze":
        cases = load_listing_cases(tags)
        llm = services.analyze_service()
    elif stage == "personalize":
        cases = load_personalize_cases(tags)
        llm = services.personalize_service()
        render = services.personalize_render()
    else:
        raise ValueError(stage)
    cases = cases[:limit] if limit else cases

    judge = None
    if judge_model and stage == "personalize":
        from .judge import Judge
        judge = Judge(judge_model)

    rows: list[dict] = []
    # Models run one after another: install() patches module-level state in
    # the service, so two models can't share it concurrently.
    for spec in specs:
        backend = providers.make_backend(spec)
        if stage == "analyze":
            backend.json_schema = analyze_eval.LISTING_VALUES_SCHEMA
            fn = lambda c: analyze_eval.run_case(llm, c)  # noqa: E731
        else:
            fn = lambda c: personalize_eval.run_case(llm, render, c, compile_pdf)  # noqa: E731
        providers.install(llm, backend)
        print(f"\n== {stage} / {spec.name} ({spec.provider}:{spec.model}) — {len(cases)} cases x {repeats}")

        sem = asyncio.Semaphore(max(spec.concurrency, 1))

        async def bounded(case, r):
            async with sem:
                row = await _run_one(spec, case, r, fn)
                if judge and "output" in row:
                    try:
                        row["judge"] = await judge.grade(case["resume"], row["output"], case["values"])
                    except Exception as exc:  # noqa: BLE001
                        row["judge"] = {"judge_error": str(exc)}
                return row

        rows += await asyncio.gather(*(bounded(c, r) for c in cases for r in range(repeats)))
    return rows


# --- aggregation ------------------------------------------------------------

def _mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(int(q * len(xs)), len(xs) - 1)] if xs else None


def summarize(rows: list[dict]) -> dict[str, dict]:
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    out = {}
    for model, rs in by_model.items():
        ok = [r for r in rs if "metrics" in r]
        s: dict[str, Any] = {
            "n": len(rs),
            "error_rate": 1 - len(ok) / len(rs),
            # Errors count as failures: a 502 is a bad outcome for the user.
            "pass_rate": sum(1 for r in ok if r["metrics"].get("pass")) / len(rs),
            "latency_p50_s": _pct([r["latency_s"] for r in ok], 0.5),
            "latency_p95_s": _pct([r["latency_s"] for r in ok], 0.95),
            "cost_total_usd": sum(r["cost_usd"] for r in rs),
            "cost_per_case_usd": sum(r["cost_usd"] for r in rs) / len(rs),
        }
        metric_keys = {k for r in ok for k, v in r["metrics"].items()
                       if isinstance(v, (int, float, bool, list)) and k != "pass"}
        for k in sorted(metric_keys):
            vals = [r["metrics"].get(k) for r in ok]
            # Lists (e.g. forbidden_hits) aggregate as "rate of non-empty".
            if any(isinstance(v, list) for v in vals):
                s[f"{k}_rate"] = _mean([bool(v) for v in vals])
            else:
                s[k] = _mean(vals)
        judged = [r["judge"] for r in rs if isinstance(r.get("judge"), dict) and "faithfulness" in r["judge"]]
        for k in ("faithfulness", "relevance", "writing"):
            if judged:
                s[f"judge_{k}"] = _mean([j[k] for j in judged])
        # Per-repeat pass agreement: does the model give the same verdict on
        # the same case across repeats? Only meaningful with --repeats > 1.
        by_case: dict[str, list[bool]] = {}
        for r in rs:
            by_case.setdefault(r["case"], []).append(bool(r.get("metrics", {}).get("pass")))
        multi = [v for v in by_case.values() if len(v) > 1]
        if multi:
            s["repeat_consistency"] = _mean([len(set(v)) == 1 for v in multi])
        out[model] = s
    return out


def _fmt(v):
    if v is None:
        return "–"
    if isinstance(v, float):
        if v != 0 and abs(v) < 0.01:
            return f"{v:.2g}"  # per-case costs are fractions of a cent
        return f"{v:.3f}" if abs(v) < 100 else f"{v:.0f}"
    return str(v)


def render_report(stage: str, summary: dict[str, dict]) -> str:
    keys = sorted({k for s in summary.values() for k in s})
    head = ["pass_rate", "error_rate", "cost_per_case_usd", "latency_p50_s", "latency_p95_s"]
    keys = head + [k for k in keys if k not in head]
    lines = [f"# {stage} eval", "", "| metric | " + " | ".join(summary) + " |",
             "|---|" + "---|" * len(summary)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(_fmt(s.get(k)) for s in summary.values()) + " |")
    return "\n".join(lines) + "\n"


def write_results(stage: str, rows: list[dict], out_root: Path) -> Path:
    out = out_root / f"{datetime.now():%Y%m%d-%H%M%S}-{stage}"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps({"stage": stage, "models": summary}, indent=2))
    report = render_report(stage, summary)
    (out / "report.md").write_text(report)
    print("\n" + report)
    print(f"results: {out}")
    return out


def rescore(run_dir: Path, out_root: Path) -> Path:
    """Re-grade a saved run with the current scorers, without calling any
    model. Use after changing a scorer or a dataset's gold labels."""
    stage = json.loads((run_dir / "summary.json").read_text())["stage"]
    rows = [json.loads(l) for l in (run_dir / "cases.jsonl").read_text().splitlines() if l.strip()]
    if stage == "analyze":
        cases = {c["id"]: c for c in load_listing_cases()}
        for r in rows:
            if "output" in r:
                r["metrics"] = analyze_eval.score(cases[r["case"]], r["output"])
    else:
        cases = {c["id"]: c for c in load_personalize_cases()}
        for r in rows:
            if "output" in r:
                c = cases[r["case"]]
                old = r["metrics"]
                r["metrics"] = personalize_eval.score(c["resume"], r["output"], c["values"], old.get("compiles"))
                if "compile_error" in old:
                    r["metrics"]["compile_error"] = old["compile_error"]
    return write_results(stage, rows, out_root)


def compare(a: Path, b: Path) -> str:
    """Side-by-side of two runs' summaries (e.g. before/after a prompt change)."""
    sa = json.loads((a / "summary.json").read_text())["models"]
    sb = json.loads((b / "summary.json").read_text())["models"]
    lines = [f"| model | metric | {a.name} | {b.name} | delta |", "|---|---|---|---|---|"]
    for model in sorted(set(sa) & set(sb)):
        for k in sorted(set(sa[model]) & set(sb[model])):
            x, y = sa[model][k], sb[model][k]
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                lines.append(f"| {model} | {k} | {_fmt(x)} | {_fmt(y)} | {y - x:+.3f} |")
    return "\n".join(lines)
