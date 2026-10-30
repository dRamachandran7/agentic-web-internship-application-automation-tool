"""Model backends that stand in for the services' `AsyncGroq` client.

Both LLM services build `AsyncGroq(api_key=...)` and call
`client.chat.completions.create(...)`. The harness swaps that class for
`ChatShim`, which routes the same call to whichever backend a model spec
names. Everything else — the system prompt, response parsing, and the
services' own validation — runs unmodified, so a score reflects
"this prompt + this model", not a reimplementation.

Each call is recorded (latency, tokens, stop reason) into a per-task list held
in a contextvar, so concurrent cases don't mix up their usage.
"""

from __future__ import annotations

import contextvars
import os
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional

# One list per eval case; the runner sets it before calling the service.
CALLS: contextvars.ContextVar[list["CallRecord"]] = contextvars.ContextVar("CALLS")


@dataclass
class CallRecord:
    provider: str
    model: str
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: Optional[str] = None


@dataclass
class ModelSpec:
    """One `[models.<name>]` table from models.toml."""

    name: str
    provider: str  # "groq" | "anthropic" | "openai_compatible"
    model: str
    price_in: float = 0.0  # USD per 1M input tokens
    price_out: float = 0.0  # USD per 1M output tokens
    concurrency: int = 2
    # anthropic-only
    effort: Optional[str] = None
    structured_output: bool = True
    fallbacks: Optional[str] = None
    # openai_compatible-only
    base_url: Optional[str] = None
    api_key_env: str = "OPENAI_API_KEY"
    drop_temperature: bool = False
    max_tokens_param: str = "max_tokens"
    # Extra request fields merged into every openai_compatible call.
    request_extra: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000


def _completion(text: str) -> SimpleNamespace:
    """Minimal object shaped like an OpenAI/Groq chat completion."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _record(rec: CallRecord) -> None:
    calls = CALLS.get(None)
    if calls is not None:
        calls.append(rec)


class Backend:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        # Set by the stage before running: JSON schema for json_object calls.
        # Only backends with native schema-constrained decoding use it.
        self.json_schema: Optional[dict] = None

    async def create(self, **kwargs: Any) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


class GroqBackend(Backend):
    """Pass-through to the real Groq client — what production runs."""

    def __init__(self, spec: ModelSpec):
        super().__init__(spec)
        from groq import AsyncGroq

        self._client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""), max_retries=5)

    async def create(self, **kwargs: Any) -> Any:
        kwargs["model"] = self.spec.model
        start = time.perf_counter()
        resp = await self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        _record(CallRecord(
            "groq", self.spec.model, time.perf_counter() - start,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            resp.choices[0].finish_reason,
        ))
        return resp


class OpenAICompatibleBackend(Backend):
    """Any OpenAI-compatible endpoint: OpenAI, Ollama, vLLM, Together, etc."""

    def __init__(self, spec: ModelSpec):
        super().__init__(spec)
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            base_url=spec.base_url,
            api_key=os.environ.get(spec.api_key_env, "not-needed"),
            max_retries=5,
        )

    async def create(self, **kwargs: Any) -> Any:
        kwargs["model"] = self.spec.model
        if self.spec.drop_temperature:
            kwargs.pop("temperature", None)
        if "max_tokens" in kwargs and self.spec.max_tokens_param != "max_tokens":
            kwargs[self.spec.max_tokens_param] = kwargs.pop("max_tokens")
        kwargs.update(self.spec.request_extra)
        start = time.perf_counter()
        resp = await self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        _record(CallRecord(
            "openai_compatible", self.spec.model, time.perf_counter() - start,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            resp.choices[0].finish_reason,
        ))
        return resp


class AnthropicBackend(Backend):
    """Translates the services' chat-completions call to the Messages API.

    Differences that matter for this pipeline, handled here rather than in
    the services:
    - `system` is a top-level field, not a message.
    - `temperature` is rejected (400) on the Claude 5 family, so it's dropped.
      Depth/cost is controlled with `output_config.effort` instead.
    - There is no `response_format={"type": "json_object"}`. With
      `structured_output = true` the call uses `output_config.format`
      (JSON-schema-constrained decoding), which is stricter than Groq's JSON
      mode. With it off, the model is trusted to follow the prompt — useful
      for measuring how a bare model-name swap would behave.
    """

    def __init__(self, spec: ModelSpec):
        super().__init__(spec)
        import anthropic

        self._client = anthropic.AsyncAnthropic(max_retries=5)

    async def create(
        self,
        *,
        messages: list[dict],
        model: str = "",
        response_format: Optional[dict] = None,
        temperature: Optional[float] = None,  # dropped, see class docstring
        max_tokens: Optional[int] = None,
        **_: Any,
    ) -> Any:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]

        output_config: dict[str, Any] = {}
        if self.spec.effort:
            output_config["effort"] = self.spec.effort
        if response_format and response_format.get("type") == "json_object":
            if self.spec.structured_output and self.json_schema:
                output_config["format"] = {"type": "json_schema", "schema": self.json_schema}

        params: dict[str, Any] = {
            "model": self.spec.model,
            # Room for adaptive thinking on top of the visible answer.
            "max_tokens": max(16000, (max_tokens or 0) * 2),
            "system": system,
            "messages": convo,
        }
        if output_config:
            params["output_config"] = output_config

        start = time.perf_counter()
        if self.spec.fallbacks:
            # Off by default: a fallback answers with a *different* model,
            # which would silently mix two models into one score.
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = self.spec.fallbacks
            stream_ctx = self._client.beta.messages.stream(**params)
        else:
            stream_ctx = self._client.messages.stream(**params)
        async with stream_ctx as stream:
            msg = await stream.get_final_message()

        _record(CallRecord(
            "anthropic", self.spec.model, time.perf_counter() - start,
            msg.usage.input_tokens, msg.usage.output_tokens, msg.stop_reason,
        ))
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"model refused (stop_details={getattr(msg, 'stop_details', None)})")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return _completion(text)


_BACKENDS = {
    "groq": GroqBackend,
    "anthropic": AnthropicBackend,
    "openai_compatible": OpenAICompatibleBackend,
}


def make_backend(spec: ModelSpec) -> Backend:
    try:
        return _BACKENDS[spec.provider](spec)
    except KeyError:
        raise ValueError(f"unknown provider {spec.provider!r} for model {spec.name!r}") from None


class ChatShim:
    """Drop-in for `AsyncGroq(...)` inside the service modules."""

    def __init__(self, backend: Backend):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=backend.create))


def install(service_llm_module, backend: Backend) -> None:
    """Point a service's `llm` module at `backend` for the rest of the run."""
    service_llm_module.AsyncGroq = lambda *a, **kw: ChatShim(backend)
    service_llm_module.settings.groq_model = backend.spec.model
    # The services refuse to run without a Groq key; any non-empty value
    # gets past that check when another provider is doing the work.
    if not service_llm_module.settings.groq_api_key:
        service_llm_module.settings.groq_api_key = os.environ.get("GROQ_API_KEY") or "eval-placeholder"


def load_models(path) -> dict[str, ModelSpec]:
    import tomllib

    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = set(ModelSpec.__dataclass_fields__) - {"name"}
    specs = {}
    for name, table in raw.get("models", {}).items():
        unknown = set(table) - known
        if unknown:
            raise ValueError(f"models.{name}: unknown keys {sorted(unknown)}")
        specs[name] = ModelSpec(name=name, **table)
    return specs
