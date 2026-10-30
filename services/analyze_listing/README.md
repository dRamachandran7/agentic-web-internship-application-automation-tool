# analyze_listing

Takes a scraped listing and makes one Groq LLM call (`openai/gpt-oss-120b`,
available on Groq's free tier) to extract the skills and values the employer
is looking for. Second stage of the pipeline
(`search_listings → analyze_listing → personalize_resume`).
Makes no Playwright calls of its own.

## Endpoint

`POST /analyze-listing`

```jsonc
// request
{
  "listingId": "handshake:1234567",   // optional — enables saving the result
  "title": "Software Engineering Intern",
  "company": "Acme Corp",
  "location": "New York, NY",
  "url": "https://app.joinhandshake.com/job-search/1234567",
  "descriptionText": "Full posting text as scraped by search_listings..."
}
```

```jsonc
// response
{
  "values": {
    "required_skills": ["Python", "SQL"],
    "preferred_skills": ["React", "AWS"],
    "keywords": ["Agile", "CI/CD"],
    "values": ["collaborative", "fast-paced"],
    "summary": "A full-stack internship building internal tools and dashboards."
  }
}
```

If `listingId` is provided and matches a row search_listings already saved,
the `values` object is attached to that row in the shared SQLite store
(`data/app.db` at the repo root) instead of only being returned in the
response.

`GET /health` reports liveness and whether `GROQ_API_KEY` is configured.

## Setup

```bash
cd services/analyze_listing
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GROQ_API_KEY
```

Get a free key at https://console.groq.com/keys.

## Run the service

```bash
uvicorn app.main:app --reload --port 8002
```

Then, e.g. against a listing search_listings already saved:

```bash
curl -s localhost:8001/listings | jq '.[0]' > /tmp/listing.json
curl -s localhost:8002/analyze-listing \
  -H 'content-type: application/json' \
  -d "$(jq '{listingId: .listing_id, title, company, location, url, descriptionText: .description_text}' /tmp/listing.json)" | jq
```
