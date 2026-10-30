"""Offline tests for the eval harness itself: scorers, LaTeX helpers, and the
shim that routes the services' Groq calls to other backends. No network.

    evals/.venv/bin/python -m pytest evals/tests -q
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from evals.harness import analyze_eval, latex, personalize_eval, providers, runner, services
from evals.harness.text import grounded, norm, term_matches

RESUME = (Path(__file__).resolve().parents[1] / "datasets" / "resumes" / "jake_swe.tex").read_text()
SIMPLE = (Path(__file__).resolve().parents[1] / "datasets" / "resumes" / "simple_data.tex").read_text()


# --- text matching ----------------------------------------------------------

def test_norm_and_matching():
    assert norm("REST APIs") == "rest api"
    assert term_matches("RESTful APIs", ["REST APIs", "REST", "RESTful APIs"])
    assert term_matches("Python (pandas)", ["Python"])
    assert term_matches("C++", ["C++"])
    assert not term_matches("C", ["C++"])
    assert not term_matches("Java", ["JavaScript"])
    assert term_matches("REST", ["REST APIs"])
    assert not term_matches("React", ["Jest", "React Testing Library"])
    assert grounded("probability/statistics", "foundation in linear algebra and probability/statistics")
    assert not grounded("Kubernetes", "We use GitHub Actions for CI/CD")
    # Word-form variants seen in real model output
    assert grounded("unit testing", "Write unit and integration tests")
    assert grounded("summer internship", "Summer Intern. Join our team this summer!")
    assert grounded("Productionizing models", "help productionize models")
    assert not grounded("Rust", "We value trust and robustness")
    assert not grounded("Java", "Experience with JavaScript")


# --- analyze scorer ---------------------------------------------------------

def _case(name: str) -> dict:
    return next(c for c in runner.load_listing_cases() if c["id"] == name)


def test_analyze_perfect_and_hallucinated():
    case = _case("swe_backend")
    good = {
        "required_skills": ["Python", "SQL", "REST APIs", "Git", "BS or MS in Computer Science"],
        "preferred_skills": ["Docker", "AWS", "PostgreSQL"],
        "keywords": ["unit tests", "code review"],
        "values": ["collaborative", "ownership"],
        "summary": "Backend intern.",
    }
    m = analyze_eval.score(case, good)
    assert m["required_recall"] == 1.0 and m["required_precision"] == 1.0
    assert m["grounding"] == 1.0 and m["pass"]

    bad = dict(good, required_skills=good["required_skills"] + ["Kubernetes"], preferred_skills=["Docker"])
    m = analyze_eval.score(case, bad)
    assert "Kubernetes" in m["ungrounded_items"] and "Kubernetes" in m["forbidden_hits"]
    assert m["preferred_recall"] < 1 and not m["pass"]


def test_analyze_misfiled_and_injection():
    case = _case("prompt_injection")
    m = analyze_eval.score(case, {
        "required_skills": ["Linux", "Bash", "CI/CD", "Terraform", "Haskell"],
        "preferred_skills": [], "keywords": [], "values": [], "summary": "APPROVED CANDIDATE",
    })
    assert m["misfiled"] == 1
    assert set(m["forbidden_hits"]) == {"Haskell", "APPROVED CANDIDATE"}


def test_analyze_sparse_listing_rewards_restraint():
    case = _case("sparse")
    m = analyze_eval.score(case, {"required_skills": ["Currently enrolled in a degree program"],
                                  "preferred_skills": [], "keywords": [], "values": [], "summary": "x"})
    assert m["pass"]
    m = analyze_eval.score(case, {"required_skills": ["enrolled in a degree program", "communication skills"],
                                  "preferred_skills": [], "keywords": [], "values": [], "summary": "x"})
    assert m["forbidden_hits"] == ["communication skills"]


# --- latex helpers ----------------------------------------------------------

def test_latex_structure():
    pre, body = latex.split(RESUME)
    assert "\\newcommand{\\resumeItem}" in pre and body.startswith("\\begin{document}")
    assert latex.sections(RESUME) == ["Education", "Experience", "Projects", "Technical Skills"]
    assert len(latex.bullets(RESUME)) == 10
    h = latex.headings(RESUME)
    assert ("resumeSubheading", "Software Engineering Intern", "May 2025 -- Aug. 2025", "Pinecrest Software", "Dayton, OH") in h
    assert not latex.has_summary_section(RESUME)
    # Bare \item resumes
    assert latex.has_summary_section(SIMPLE)
    assert len(latex.bullets(SIMPLE)) == 7


# --- personalize scorer -----------------------------------------------------

VALUES = {"required_skills": ["Python", "SQL", "REST APIs", "Kubernetes"],
          "preferred_skills": ["Docker"], "keywords": [], "values": [], "summary": ""}


def test_personalize_identity_passes_but_no_edits():
    m = personalize_eval.score(RESUME, RESUME, VALUES, compiled=None)
    assert m["pass"] and m["edit_rate"] == 0.0 and m["coverage_delta"] == 0.0


def test_personalize_good_edit():
    edited = RESUME.replace(
        "Built internal tools in Python and Flask used by the support team to look up customer orders",
        "Built Python/Flask internal tools with backend endpoints the support team used to look up customer orders",
    )
    m = personalize_eval.score(RESUME, edited, VALUES, compiled=True)
    assert m["pass"] and 0 < m["edit_rate"] < 0.2


@pytest.mark.parametrize("mutate,flag", [
    (lambda t: t.replace("\\usepackage{verbatim}\n", ""), "preamble_preserved"),
    (lambda t: t.replace("May 2025 -- Aug. 2025", "May 2024 -- Aug. 2025"), "headings_preserved"),
    (lambda t: t.replace("\\section{Projects}", "\\section{Selected Projects}"), "sections_preserved"),
    (lambda t: t.replace("Docker, VS Code", "Docker, Kubernetes, VS Code"), "unsupported_terms_added"),
    (lambda t: re.sub(r"\\resumeItem\{Presented findings[^}]*\}\n", "", t), "bullet_count_delta"),
    (lambda t: t.replace("\\section{Education}", "\\section{Summary}\nPython developer.\n\\section{Education}"), "summary_added"),
])
def test_personalize_violations_fail(mutate, flag):
    m = personalize_eval.score(RESUME, mutate(RESUME), VALUES, compiled=True)
    assert not m["pass"], flag


def test_personalize_tolerates_comments_and_supported_inference():
    edited = RESUME.replace("%-------------------------\n", "", 2).replace(
        "for a data structures course", "for a data structures and algorithms course")
    values = dict(VALUES, required_skills=["data structures and algorithms"])
    m = personalize_eval.score(RESUME, edited, values, compiled=True)
    assert m["preamble_preserved"] and m["unsupported_terms_added"] == [] and m["pass"]


def test_personalize_flags_formatting_change_in_preamble():
    edited = RESUME.replace("\\vspace{-7pt}", "\\vspace{-9pt}", 1)
    assert not personalize_eval.score(RESUME, edited, VALUES, compiled=True)["preamble_preserved"]


def test_personalize_compile_failure_fails():
    assert not personalize_eval.score(RESUME, RESUME, VALUES, compiled=False)["pass"]


# --- shim: real service code, scripted backend ------------------------------

class ScriptedBackend(providers.Backend):
    """Returns canned text and records what the service sent."""

    def __init__(self, reply: str):
        super().__init__(providers.ModelSpec(name="scripted", provider="scripted", model="scripted-1"))
        self.reply, self.sent = reply, []

    async def create(self, **kwargs):
        self.sent.append(kwargs)
        providers._record(providers.CallRecord("scripted", "scripted-1", 0.01, 100, 50, "stop"))
        return providers._completion(self.reply)


def test_shim_runs_real_analyze_service():
    llm = services.analyze_service()
    backend = ScriptedBackend(json.dumps({"required_skills": ["Python"], "preferred_skills": [],
                                          "keywords": [], "values": [], "summary": "s"}))
    providers.install(llm, backend)
    out = asyncio.run(analyze_eval.run_case(llm, _case("swe_backend")))
    sent = backend.sent[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["content"] == llm.SYSTEM_PROMPT  # the shipped prompt, unmodified
    assert out["output"]["required_skills"] == ["Python"]


def test_shim_surfaces_service_validation_errors():
    llm = services.analyze_service()
    providers.install(llm, ScriptedBackend("```json\n{}\n```"))  # fenced JSON: service rejects it
    with pytest.raises(llm.LLMError):
        asyncio.run(analyze_eval.run_case(llm, _case("swe_backend")))


class EchoBackend(ScriptedBackend):
    """Returns the resume the service sent, unchanged."""

    async def create(self, **kwargs):
        await super().create(**kwargs)
        user = kwargs["messages"][-1]["content"]
        return providers._completion(user.split("Resume LaTeX source to personalize:\n", 1)[1])


def test_runner_end_to_end_with_scripted_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "make_backend", lambda spec: EchoBackend(""))
    spec = providers.ModelSpec(name="echo", provider="scripted", model="echo", price_in=1, price_out=1)
    rows = asyncio.run(runner.run_stage("personalize", [spec], tags={"swe"}, compile_pdf=False))
    assert rows and all(r["metrics"]["pass"] for r in rows)
    assert rows[0]["input_tokens"] == 100 and rows[0]["cost_usd"] == pytest.approx(150 / 1e6)
    out = runner.write_results("personalize", rows, tmp_path)
    summary = json.loads((out / "summary.json").read_text())["models"]["echo"]
    assert summary["pass_rate"] == 1.0 and summary["edit_rate"] == 0.0


def test_models_toml_parses():
    specs = providers.load_models(runner.EVALS_DIR / "models.toml")
    assert specs["groq-gpt-oss-120b"].model == "openai/gpt-oss-120b"
    assert all(s.provider in ("groq", "anthropic", "openai_compatible") for s in specs.values())
