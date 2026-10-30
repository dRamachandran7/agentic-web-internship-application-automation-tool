"""Runtime configuration for the analyze_listing microservice.

Values are read from environment variables (a local `.env` file is loaded
automatically).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Same SQLite file search_listings writes to, so analysis results can be
# attached to the rows it already saved there.
_DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "app.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Groq ---
    groq_api_key: str = ""
    # openai/gpt-oss-120b: best of the Groq models in evals/ (2026-10-30);
    # llama-3.3-70b-versatile was retired.
    groq_model: str = "openai/gpt-oss-120b"

    # --- Storage ---
    db_path: Path = _DEFAULT_DB


settings = Settings()
