"""Request/response schemas for the personalize_resume service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListingValues(BaseModel):
    """Mirrors analyze_listing's ListingValues — the `values` field this
    service receives as input. Duplicated rather than imported since each
    service is an independent process communicating over JSON."""

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    summary: str = ""


class PersonalizeRequest(BaseModel):
    """`resumeLatex` is the user's resume as its original, full LaTeX
    source — not parsed into structured fields. Resumes don't reliably
    conform to one template, so a fixed schema breaks or silently drops
    content whenever a resume deviates from what it expects. Editing the
    LaTeX source directly sidesteps that."""

    resumeLatex: str
    values: ListingValues


class PersonalizeResponse(BaseModel):
    """`resumeFile` is the compiled PDF, base64-encoded for JSON transport.
    `resumeLatex` is the same personalized content as raw LaTeX source, so the
    user can download either format or hand-edit the LaTeX themselves before
    submitting the application manually."""

    resumeFile: str
    filename: str = "resume.pdf"
    resumeLatex: str
    latexFilename: str = "resume.tex"
