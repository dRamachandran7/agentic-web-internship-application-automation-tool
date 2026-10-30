"""Runtime configuration for the personalize_resume microservice.

Values are read from environment variables (a local `.env` file is loaded
automatically).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Groq ---
    groq_api_key: str = ""
    # openai/gpt-oss-120b: best of the Groq models in evals/ (2026-10-30);
    # llama-3.3-70b-versatile was retired.
    groq_model: str = "openai/gpt-oss-120b"
    # Full resume documents in, full resume documents out — needs headroom
    # well beyond a typical chat completion.
    max_completion_tokens: int = 8192

    # --- LaTeX compilation ---
    # Name/path of the Tectonic binary. Run as a subprocess so this service
    # has no external LaTeX-install dependency (Tectonic self-fetches what it
    # needs).
    tectonic_bin: str = "tectonic"
    compile_timeout_s: int = 60


settings = Settings()
