"""Optional LLM-as-judge for personalize_resume.

The deterministic checks in personalize_eval catch structural breakage and
listing skills that appear from nowhere. They can't tell whether a reworded
bullet overstates what the candidate did ("helped with" -> "led"), or whether
the tailoring is any good. That needs a reader; this is that reader.

Use a judge model at least as capable as the strongest model under test, and
spot-check its verdicts by hand before trusting score deltas between models.
"""

from __future__ import annotations

import json
from typing import Any

JUDGE_SYSTEM = """You audit resume edits. A tool rewrote a candidate's LaTeX \
resume to target a job listing. It was allowed to rephrase and reorder \
existing content only — never to add experience, skills, tools, metrics, \
scope, or seniority the original does not support.

Compare ORIGINAL and EDITED bullet by bullet. Score:
- faithfulness (1-5): 5 = every edited claim is supported by the original; \
1 = clear fabrication or inflated scope/impact.
- relevance (1-5): how much better the edited resume foregrounds what the \
listing asks for, using only true content. 1 = no improvement.
- writing (1-5): clarity and concision of the edited bullets.
List each unsupported or inflated claim verbatim in fabricated_claims."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {"type": "integer"},
        "relevance": {"type": "integer"},
        "writing": {"type": "integer"},
        "fabricated_claims": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["faithfulness", "relevance", "writing", "fabricated_claims", "rationale"],
    "additionalProperties": False,
}


class Judge:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high"):
        import anthropic

        self.model, self.effort = model, effort
        self._client = anthropic.AsyncAnthropic(max_retries=5)

    async def grade(self, original: str, edited: str, values: dict[str, Any]) -> dict[str, Any]:
        user = (
            f"LISTING VALUES:\n{json.dumps(values, indent=2)}\n\n"
            f"ORIGINAL:\n{original}\n\nEDITED:\n{edited}"
        )
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
            },
        ) as stream:
            msg = await stream.get_final_message()
        if msg.stop_reason == "refusal":
            return {"judge_error": "refusal"}
        text = next(b.text for b in msg.content if b.type == "text")
        out = json.loads(text)
        out["judge_input_tokens"] = msg.usage.input_tokens
        out["judge_output_tokens"] = msg.usage.output_tokens
        return out
