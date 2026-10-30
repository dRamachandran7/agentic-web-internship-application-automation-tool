"""CLI: python -m evals <command> ...

    python -m evals analyze     --models groq-gpt-oss-120b,claude-haiku-5-5
    python -m evals personalize --models groq-gpt-oss-120b --no-compile
    python -m evals personalize --models groq-gpt-oss-120b --judge claude-opus-5-5
    python -m evals rescore evals/results/<run>
    python -m evals compare evals/results/<run-a> evals/results/<run-b>
    python -m evals models
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from .harness import providers, runner

# Same repo-root .env that docker compose reads; real env vars win.
load_dotenv(runner.EVALS_DIR.parent / ".env", override=False)

MODELS_FILE = runner.EVALS_DIR / "models.toml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for stage in ("analyze", "personalize"):
        p = sub.add_parser(stage, help=f"evaluate the {stage} stage")
        p.add_argument("--models", default="groq-gpt-oss-120b",
                       help="comma-separated names from models.toml")
        p.add_argument("--repeats", type=int, default=1, help="runs per case, to measure variance")
        p.add_argument("--tags", default="", help="only cases with any of these tags (comma-separated)")
        p.add_argument("--limit", type=int, help="first N cases only (cheap smoke run)")
        p.add_argument("--out", type=Path, default=runner.EVALS_DIR / "results")
        if stage == "personalize":
            p.add_argument("--no-compile", action="store_true", help="skip the Tectonic compile check")
            p.add_argument("--judge", metavar="CLAUDE_MODEL",
                           help="also grade faithfulness/relevance with this Claude model")

    c = sub.add_parser("compare", help="diff two result directories")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)

    rs = sub.add_parser("rescore", help="re-grade a saved run with the current scorers (no API calls)")
    rs.add_argument("run", type=Path)
    rs.add_argument("--out", type=Path, default=runner.EVALS_DIR / "results")

    sub.add_parser("models", help="list configured models")

    args = ap.parse_args(argv)
    specs = providers.load_models(MODELS_FILE)

    if args.cmd == "models":
        for name, s in specs.items():
            print(f"{name:28s} {s.provider:18s} {s.model:40s} ${s.price_in}/{s.price_out} per 1M  {s.notes}")
        return 0
    if args.cmd == "rescore":
        runner.rescore(args.run, args.out)
        return 0
    if args.cmd == "compare":
        print(runner.compare(args.a, args.b))
        return 0

    names = [n.strip() for n in args.models.split(",") if n.strip()]
    missing = [n for n in names if n not in specs]
    if missing:
        print(f"unknown model(s): {missing}. Run `python -m evals models`.", file=sys.stderr)
        return 2
    tags = {t.strip() for t in args.tags.split(",") if t.strip()} or None

    rows = asyncio.run(runner.run_stage(
        args.cmd, [specs[n] for n in names], repeats=args.repeats, tags=tags, limit=args.limit,
        compile_pdf=not getattr(args, "no_compile", False), judge_model=getattr(args, "judge", None),
    ))
    runner.write_results(args.cmd, rows, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
