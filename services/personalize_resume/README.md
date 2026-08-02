# personalize_resume

Takes the user's resume as raw LaTeX source and the `values` a listing is
looking for (analyze_listing's output), makes one Groq call
(`llama-3.3-70b-versatile`, available on Groq's free tier) to edit the LaTeX
directly — rephrasing and reordering existing bullets/skills to foreground
what the listing cares about, without inventing new experience — then
compiles the result to PDF with Tectonic. Third and final stage of the
pipeline (`search_listings → analyze_listing → personalize_resume`). Makes no
Playwright calls of its own, and there is no automated submission stage —
apply forms vary too much across listings (different fields, some redirect
to a different ATS entirely) to automate reliably, so this service just
hands back the personalized resume in both formats for the user to download
and submit themselves.

An earlier version of this service parsed resumes into a fixed structured
schema (contact/education/experience/... as separate JSON fields) and
rendered that into a Jinja2 LaTeX template. That was dropped: real resumes
don't reliably conform to one template, so parsing into rigid fields breaks
or silently drops content whenever a resume's structure deviates from what
the schema expects. Editing the LaTeX source directly sidesteps that — the
model works with whatever structure the resume already has, touching only
wording and ordering, not markup.

The tradeoff: the old approach could validate its output structurally
(bullet counts matched, skills came from a fixed pool). This one can't — the
model is trusted to follow the prompt's constraints (no invented experience,
preamble left untouched, valid LaTeX escaping). The one hard backstop that
remains is Tectonic itself: if the model's edits break the LaTeX, compiling
it fails loudly (500) rather than silently shipping a broken PDF.

## Endpoint

`POST /personalize-resume`

```jsonc
// request
{
  "resumeLatex": "\\documentclass[letterpaper,11pt]{article}\n...\\end{document}",
  "values": {
    "required_skills": ["Python", "SQL"],
    "preferred_skills": ["AWS"],
    "keywords": ["Agile", "CI/CD"],
    "values": ["collaborative", "fast-paced"],
    "summary": "A full-stack internship building internal tools and dashboards."
  }
}
```

```jsonc
// response
{
  "resumeFile": "<base64-encoded PDF bytes>",
  "filename": "resume.pdf",
  "resumeLatex": "\\documentclass[letterpaper,11pt]{article}\n...\\end{document}",
  "latexFilename": "resume.tex"
}
```

`resumeFile` is base64-encoded for JSON transport. The frontend offers both
`resumeFile` (as a PDF download) and `resumeLatex` (as a `.tex` download) so
the user can either submit the PDF as-is or hand-edit the LaTeX first.

`GET /health` reports liveness and whether `GROQ_API_KEY` is configured.

## Setup

```bash
cd services/personalize_resume
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GROQ_API_KEY
```

Get a free Groq key at https://console.groq.com/keys.

You also need [Tectonic](https://tectonic-typesetting.github.io/) on your
`PATH` (it self-fetches whatever LaTeX packages it needs, so no separate
TeX Live install is required):

```bash
brew install tectonic   # macOS
```

## Run the service

```bash
uvicorn app.main:app --reload --port 8003
```

```bash
python3 -c "
import json
json.dump({
    'resumeLatex': open('/path/to/resume.tex').read(),
    'values': {
        'required_skills': ['Python', 'SQL'],
        'preferred_skills': ['AWS'],
        'keywords': ['Agile', 'CI/CD'],
        'values': ['collaborative', 'fast-paced'],
        'summary': 'Whatever the target listing is about.',
    },
}, open('/tmp/request.json', 'w'))
"

curl -s localhost:8003/personalize-resume \
  -H 'content-type: application/json' \
  -d @/tmp/request.json | jq -r '.resumeFile' | base64 -d > /tmp/resume.pdf
```
