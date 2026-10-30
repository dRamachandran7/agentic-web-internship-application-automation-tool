"""Loads the real service code in-process so evals exercise the shipped
prompts, parsing, and validation rather than a copy of them.

Both LLM services are packages named `app`, so they can't both be imported
normally. Each is loaded under its own name (`svc_analyze`, `svc_personalize`)
so their relative imports (`from .config import settings`) resolve inside
that service only.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"


def _load_package(alias: str, service_dir: str) -> ModuleType:
    if alias in sys.modules:
        return sys.modules[alias]
    pkg_dir = SERVICES / service_dir / "app"
    spec = importlib.util.spec_from_file_location(
        alias, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def analyze_service() -> ModuleType:
    """The analyze_listing `llm` module (exposes `analyze`, `settings`, `AsyncGroq`)."""
    _load_package("svc_analyze", "analyze_listing")
    return importlib.import_module("svc_analyze.llm")


def personalize_service() -> ModuleType:
    """The personalize_resume `llm` module (exposes `personalize`, `settings`, `AsyncGroq`)."""
    _load_package("svc_personalize", "personalize_resume")
    return importlib.import_module("svc_personalize.llm")


def personalize_render() -> ModuleType:
    """The personalize_resume `render` module (exposes `compile_pdf`, `RenderError`)."""
    _load_package("svc_personalize", "personalize_resume")
    return importlib.import_module("svc_personalize.render")
