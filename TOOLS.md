## search_listings

The purpose of search_listings is to go through various job posting platforms such as linkedin, indeed, etc using the user's credentials, and amass a list of postings that match the user's provided parameters. For now the domains that we will use are:

Handshake

To facilitate this, we will use Playwright API.

search_listings is its own microservice, exposing a single endpoint (e.g. `POST /search-listings`) that the orchestrator calls with the user's parsed parameters (location, pay, hours, time of year). It does not call the LLM or any other tool directly — it only scrapes and returns raw data.

- Input: `{ params: {...} }` — the user's plain-text search parameters, already parsed
- Playwright logs into Handshake using a saved `storageState` (cookies/localStorage) rather than re-authenticating each run, to avoid repeated-login bot signals and speed up subsequent searches
- Output: JSON array of listings, one entry per posting: `{ listingId, title, company, location, url, descriptionText }` — the full description text is what `analyze_listing` needs, so it should be scraped in full here rather than re-fetched later
- If LinkedIn/Indeed are added later, they should be gated behind rate limiting and session reuse — both run active, enforced bot detection (LinkedIn flags automated sessions within ~48 hours as of 2026) and automating them with the user's real account risks suspension

## analyze_listing

The purpose of this function is to take a listing, and extract the key skills and values that are being prioritized by the listing. It makes an LLM call to determine the skills, given the listing and company name.

- Input: `{url, descriptionText, title, company, location}`. These are all things to be considered when editing the user's resume in `personalize_resume`.
- An LLM call is made with the Groq API, which recieves all the parameters above. It is given a prompt to identify the skills and other values (such as location) that are benificial to the listing.
- Output: `{ values: {...} }`: a list of values that are later passed to personalize_resume to refer to when editing the user resume.

## personalize_resume

The purpose of this tool is, given a user's resume and the values that the listing is interested in, to edit the user's resume to highlight the relevant skills. The tool should not make up new information, but instead elaborate on the information already provided in the user's existing resume.

- Input: `{ resumeLatex: "...", values: {...} }` — the user's base resume as its original, full LaTeX source, plus the `values` output from `analyze_listing`. A fixed structured schema (bullet points, skills list, summary as separate fields) was tried first and dropped: real resumes don't reliably conform to one template, so parsing them into rigid fields breaks or silently drops content whenever a resume deviates from what the schema expects.
- An LLM call is made with the Groq API, which receives the full LaTeX source and the listing's values. It is given a prompt to rephrase and reorder existing bullets/skills within the document to foreground what the listing is looking for, without inventing new experience, and to return the complete modified LaTeX document — preamble and structure untouched, only content edited — with no commentary or code fences around it. The LLM is trusted to keep the LaTeX syntactically valid (correct escaping of any special characters it introduces) since it's editing LaTeX source directly rather than filling in a template.
- The returned LaTeX is compiled to PDF with Tectonic, run as a subprocess so the service has no external LaTeX-install dependency. If compilation fails (e.g. the model broke the LaTeX), the request fails rather than silently returning a broken PDF.
- Output: `{ resumeFile: <PDF buffer>, resumeLatex: "..." }`: the compiled, personalized resume in both PDF and LaTeX form.

This is the last stage of the pipeline. There is no automated submission step: per-listing apply forms vary too much (different fields, some redirect to an entirely different ATS) for reliable automation, so the frontend presents each listing alongside its personalized resume — as both a LaTeX download and a PDF download — and the user submits the application themselves.