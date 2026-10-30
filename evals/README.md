# Evals

Measures whether the pipeline does its job, and how that changes when the
model behind it changes. There are three layers, from cheapest to most expensive:

| Layer | What it tests | Needs | Cost |
|---|---|---|---|
| `tests/` | The scorers themselves, and the harness wiring | nothing | free, <1s |
| `e2e contract` | The frontend orchestrator (`/api/run`): event order, `maxListings`, per-listing error isolation, fatal on search 401, input validation | Next.js + `fake_services` | free |
| `analyze` / `personalize` | Each LLM stage, scored against labeled fixtures, per model | API key(s) | real tokens |
| `e2e pipeline` | The whole flow with the real LLM services; only Handshake is faked | full stack | real tokens |

The stage evals load the **real service code** in-process (`harness/services.py`)
and swap only the `AsyncGroq` client (`harness/providers.py`). The shipped
system prompts, JSON parsing, fence-stripping and validation all run
unmodified, so a score measures this prompt with this model.

## Setup

```bash
python3 -m venv evals/.venv
evals/.venv/bin/pip install -r evals/requirements.txt
export GROQ_API_KEY=...            # for groq-* models
export ANTHROPIC_API_KEY=...       # for claude-* models (or `ant auth login`)
```

`tectonic` must be on PATH for the compile check (or pass `--no-compile`).

## Running

```bash
# Unit tests for the harness (run these after changing a scorer)
evals/.venv/bin/python -m pytest evals/tests -q

# Stage evals. Results go to evals/results/<timestamp>-<stage>/ (gitignored)
evals/.venv/bin/python -m evals models
evals/.venv/bin/python -m evals analyze     --models groq-gpt-oss-120b
evals/.venv/bin/python -m evals personalize --models groq-gpt-oss-120b,claude-haiku-5-5
evals/.venv/bin/python -m evals personalize --models groq-gpt-oss-120b --judge claude-opus-5-5
evals/.venv/bin/python -m evals analyze     --models groq-gpt-oss-120b --repeats 3 --tags adversarial
evals/.venv/bin/python -m evals compare evals/results/<before> evals/results/<after>
evals/.venv/bin/python -m evals rescore evals/results/<run>   # re-grade after a scorer change, no API calls

# Orchestrator contract tests (no LLM, no Handshake)
evals/.venv/bin/python -m evals.e2e.fake_services --port 8100 &
(cd services/frontend && SEARCH_LISTINGS_URL=http://127.0.0.1:8100 \
  ANALYZE_LISTING_URL=http://127.0.0.1:8100 PERSONALIZE_RESUME_URL=http://127.0.0.1:8100 \
  npx next dev -p 3100) &
evals/.venv/bin/python -m evals.e2e.run_e2e contract --frontend http://127.0.0.1:3100

# Full pipeline: real analyze/personalize, fake search (set SEARCH_LISTINGS_URL only)
evals/.venv/bin/python -m evals.e2e.run_e2e pipeline --frontend http://127.0.0.1:3100 --max-listings 3
```

Start with `--limit 2` when trying a new model. The default Groq concurrency
is 1 because the free tier's tokens-per-minute cap is low and a resume
rewrite is several thousand tokens each way.

## What's scored

### analyze_listing (7 labeled listings)

| Metric | Meaning |
|---|---|
| `required_recall` / `required_precision` | vs. hand-labeled required skills (fuzzy, alias-aware match) |
| `preferred_recall`, `skill_recall_any` | the second one tells "missed it" apart from "put it under the wrong heading" |
| `misfiled` | required ↔ preferred confusion |
| `grounding` | share of extracted skills/keywords actually supported by the posting text. **The main hallucination signal** |
| `forbidden_hits_rate` | something from `must_not_include` showed up (prompt injection, invented skills, scraped "Similar jobs" noise) |
| `pass` | recall ≥ 0.75, grounding ≥ 0.8, no forbidden hits |

Fixtures cover typical postings (SWE, ML, frontend, data), plus edge cases:
a near-empty posting (`sparse`), an embedded prompt injection
(`prompt_injection`), and a page with Handshake navigation chrome and EEO
boilerplate (`noisy_scrape`).

### personalize_resume (2 resumes × 7 listings = 14 cases)

The input `values` come from the listing's **gold labels**, not from
analyze's output, so a weak analyzer can't drag down this stage's score.
Each hard check corresponds to a rule in the service's system prompt:

| Check (any failure → case fails) | Prompt rule |
|---|---|
| `compiles` | output must remain valid LaTeX |
| `preamble_preserved` | don't touch `\documentclass`, `\usepackage`, `\newcommand` |
| `sections_preserved` | don't reorder/move sections |
| `headings_preserved`, `dates_preserved` | no new employers, titles, dates |
| `bullet_count_delta == 0` | rephrase/reorder, don't add or drop |
| `unsupported_terms_added` empty | **never add skills the resume doesn't have** |
| `summary_added` false | don't add a summary if there isn't one |

Soft signals: `edit_rate` (≈0 means the model just echoed the input, which
passes the hard checks but tailors nothing), `coverage_delta`,
`prominence_delta` (listing terms moved earlier), `length_ratio`.

The deterministic checks can't see subtle inflation ("helped with" → "led")
or judge whether the tailoring is any good. `--judge <claude model>` adds an
LLM grader for `faithfulness`, `relevance` and `writing` (1–5), and lists any
fabricated claims. Read a sample of its verdicts yourself before trusting
small differences between models.

### Adding cases

Drop a JSON file in `datasets/listings/` (see an existing one: gold items are
`["canonical", "alias", ...]`) or a `.tex` in `datasets/resumes/`. Every new
resume automatically pairs with every listing. Real postings from your own
`data/app.db` make the best fixtures, but you have to label them by hand.
Don't copy labels from a model's output, or the eval just grades agreement
with that model.

## Swapping models: what changes, and how to eval it

Both LLM services read `GROQ_MODEL`, so any **Groq-hosted** model is a
config change. Groq retires models: the service default,
`llama-3.3-70b-versatile`, returned `404 model_not_found` on 2026-10-30.
List what your key can call with `Groq().models.list()`. **Other providers** need code changes in the services, and
the harness already does the equivalent translation so you can measure
first and port second. Model-specific risks, by stage:

**analyze_listing** (short JSON extraction)
- *JSON reliability.* The service calls `json.loads(raw)` with no fence
  stripping and relies on Groq's JSON mode. A model without that mode, or
  one that wraps JSON in ```` ``` ````, turns into a 502. Compare
  `claude-haiku-5-5` (schema-constrained) with `claude-haiku-5-5-no-schema`
  and look at `error_rate`.
- *Precision vs. recall.* Bigger and reasoning models tend to extract more
  items. Recall goes up, but watch `grounding` and `misfiled`. Small models
  tend to miss preferred skills and to fall for the injection fixture. In the
  first run (2026-10-30), `gpt-oss-20b` was fully taken over by it: it
  reported Haskell and Rust as required and set the summary to "APPROVED CANDIDATE".
- This stage is a good candidate for a much cheaper model. If a small model
  matches the larger models on `pass_rate` here, that cuts cost on half the calls.

**personalize_resume** (long, faithful LaTeX rewrite) is where models differ most:
- *Output length.* The whole document comes back. Small models may truncate
  (watch `stop_reasons` for `length`/`max_tokens` in `cases.jsonl`), and the
  service's `max_completion_tokens=8192` caps every model.
- *LaTeX fidelity.* Unescaped `&`/`%` → `compiles` fails. Weaker models also
  "tidy" the preamble, which breaks `preamble_preserved`.
- *Faithfulness vs. eagerness.* More capable models tailor harder: higher
  `edit_rate` and `prominence_delta`, but also more temptation to slip in a
  listing skill (`unsupported_terms_added`) or inflate scope (judge
  `faithfulness`). The best model is the one with the highest
  `judge_relevance` among those with zero fabrication, not the one with the
  most edits.
- *Reasoning models* (gpt-oss, Claude with thinking) spend extra hidden
  output tokens, so latency and cost per case go up. Check `latency_p95_s`
  against the frontend's `REQUEST_TIMEOUT_MS` (120 s): a call slower than
  that becomes an `error` on the listing card.

**Porting a service to Claude** (what `AnthropicBackend` does, and what the
service code would need):
- Use the `anthropic` SDK: the `system` prompt becomes a top-level field,
  not a message.
- Drop `temperature`. Claude 5-family models reject it. Use
  `output_config.effort` (`low` to `max`) to trade depth for cost and
  latency. Sweep it as you would a model: run the same model at
  `effort = "low"` and at `"medium"` as two entries in `models.toml`.
- Replace `response_format={"type": "json_object"}` with
  `output_config.format` (JSON schema). That makes analyze's output
  schema-valid by construction.
- Handle `stop_reason == "refusal"`; the harness records it as an error.
  Server-side `fallbacks` are off in `models.toml` so each score reflects one
  model. In production you'd likely turn them on.
- Thinking tokens count toward `max_tokens`, so give the personalize call
  headroom; the adapter uses ≥16k and streams.

**Local models** (Ollama, vLLM): use `provider = "openai_compatible"`. Expect
JSON-mode support to vary, and LaTeX fidelity to drop off
sharply in smaller models. Worth it only if keeping resumes off third-party APIs matters.

### A model-comparison recipe

1. Baseline: `analyze` and `personalize` on `groq-gpt-oss-120b` with
   `--repeats 3` (temperature is 0.2–0.3, so one run is noisy). Check
   `repeat_consistency`.
2. Candidates, same flags. Add `--judge claude-opus-5-5` for personalize.
3. Decide per stage: the cheapest model whose hard-check `pass_rate` and
   `forbidden_hits_rate` match the baseline, with `judge_faithfulness` no
   lower and p95 latency under the frontend timeout.
4. Confirm the chosen pair with `e2e pipeline` before changing `GROQ_MODEL`
   (or porting a service).

With 7 listings and 14 resume pairs, a difference of one or two cases is
noise. Add fixtures before reading much into small gaps.
