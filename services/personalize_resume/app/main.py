"""personalize_resume microservice.

Exposes `POST /personalize-resume`. Takes the user's resume as raw LaTeX
source and the `values` a listing is looking for (analyze_listing's output),
makes one Groq call to edit the LaTeX directly — rephrasing/reordering
existing content around those values without inventing new experience — then
compiles the result to PDF with Tectonic. Returns both the LaTeX and the PDF
so the user can download either and submit the application themselves; this
is the last stage of the pipeline, there is no automated submission step.
"""

from __future__ import annotations

import base64

from fastapi import FastAPI, HTTPException

from .config import settings
from .llm import LLMError, personalize
from .models import PersonalizeRequest, PersonalizeResponse
from .render import RenderError, compile_pdf

app = FastAPI(title="personalize_resume")


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "groq_api_key_present": bool(settings.groq_api_key)}


@app.post("/personalize-resume", response_model=PersonalizeResponse)
async def personalize_resume(request: PersonalizeRequest) -> PersonalizeResponse:
    try:
        personalized_tex = await personalize(request.resumeLatex, request.values)
    except LLMError as exc:
        # 502: the request was fine, the upstream Groq call is what failed.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        pdf_bytes = compile_pdf(personalized_tex)
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return PersonalizeResponse(
        resumeFile=base64.b64encode(pdf_bytes).decode("ascii"),
        resumeLatex=personalized_tex,
    )
