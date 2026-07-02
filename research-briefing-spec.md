# SPEC — Weekly Research Briefing Pipeline

**Purpose of this document:** a complete, buildable specification. Drop it into an empty repository and instruct Claude Code: *"Build this project exactly as specified in research-briefing-spec.md."* Every file contract, schema, and acceptance criterion needed for implementation is defined below. Where the spec includes literal file contents, use them verbatim unless marked as a template.

---

## 1. Overview

A GitHub-hosted pipeline that, **every Friday at ~04:00 Europe/Berlin**, collects new papers from arXiv and Hugging Face Daily Papers, filters and ranks them against a user-editable research-topics file using Claude (headless `claude -p`), and publishes a markdown briefing as a Jekyll post to a GitHub Pages site.

```
GitHub Actions (cron, Fri 02:17 UTC)
  │
  ├─ 1. fetch_arxiv.py ──────► work/candidates_arxiv.json
  ├─ 2. fetch_hf.py ─────────► work/candidates_hf.json
  ├─ 3. merge_enrich.py ─────► work/candidates.json   (dedup + venue enrichment)
  ├─ 4. claude -p (judgment) ► _posts/YYYY-MM-DD-research-briefing.md
  │                            work/scores.jsonl, state/seen_ids.json
  ├─ 5. validate.py ─────────► hard gate: malformed post fails the run
  ├─ 6. git commit + push
  └─ 7. Jekyll build → deploy to GitHub Pages
```

### Goals
- Zero-maintenance weekly briefing, readable Friday morning at a stable URL and via RSS.
- Research interests live in **one editable YAML file** (`topics.yml`); no code changes needed to retune the filter.
- Each briefing entry contains: **title, authors, date, venue (arXiv preprint vs. accepted conference/journal), full abstract, a Claude-written TL;DR framed against the user's research interests, and all source links**.
- Deterministic fetching (plain Python against public APIs); the LLM is used **only** for relevance judgment and TL;DR writing.
- Scheduled runs must **not consume the user's Claude subscription tokens** — runs authenticate with a Claude Platform API key (see §10).

### Non-goals
- No full-text PDF analysis (abstract + metadata only; keeps cost at cents/run).
- No WhatsApp/Telegram/email delivery (the Pages site + RSS feed is the delivery).
- No web scraping — official APIs only (arXiv Atom API, HF daily-papers API, Semantic Scholar Graph API).
- No database; state is flat JSON committed to the repo.

---

## 2. Repository layout

```
research-briefing/
├── .github/workflows/briefing.yml     # the entire schedule + pipeline + deploy
├── topics.yml                         # ★ user-editable research interests
├── prompts/briefing.md                # instructions for the claude -p judgment step
├── scripts/
│   ├── fetch_arxiv.py
│   ├── fetch_hf.py
│   ├── merge_enrich.py
│   └── validate.py
├── state/
│   └── seen_ids.json                  # dedup memory, committed back each run
├── work/                              # gitignored intermediate artifacts
├── _posts/                            # one Jekyll post per weekly run
├── _config.yml
├── index.md                           # Pages landing: reverse-chron list of briefings
├── requirements.txt                   # feedparser, requests, pyyaml, python-frontmatter
└── .gitignore                         # work/, node_modules/
```

---

## 3. `topics.yml` — the editable interest file

This file is the single knob the user turns. It is consumed by **two** components with different semantics:

- `fetch_arxiv.py` uses the union of all `keywords` to build the arXiv boolean query (**recall**).
- The Claude judgment prompt reads the entire file, using `name`, `note`, `weight`, and `negative_topics` for semantic scoring (**precision**).

Ship this initial content (template — the user edits freely):

```yaml
# ── Retrieval settings ────────────────────────────────────────────
arxiv_categories: [cs.CV, cs.RO, cs.LG, cs.AI]
window_days: 7          # look-back window per run
max_papers: 8           # hard cap on briefing length
min_score: 7            # papers scoring below this (0–10) are dropped
max_arxiv_results: 300  # cap on fetched candidates

# ── Positive topics ───────────────────────────────────────────────
# keywords → used literally in the arXiv query (recall)
# note     → semantic guidance for the LLM judge (precision)
# weight   → multiplier on the judge's raw score (0.5–1.5)
topics:
  - name: Egocentric video world models
    keywords: ["world model", "V-JEPA", "JEPA", "egocentric video", "latent prediction"]
    note: >
      Action-conditioned latent-predictive models trained on egocentric video;
      self-supervised representation learning for prediction and planning.
    weight: 1.3

  - name: Gaze and hand signals for perception
    keywords: ["gaze", "hand pose", "hand keypoints", "eye tracking", "attention prediction"]
    note: >
      Human behavioral signals (gaze, hands) as conditioning or supervision
      for video understanding, prediction, or robot policies.
    weight: 1.2

  - name: Humanoid and manipulation learning
    keywords: ["humanoid", "imitation learning", "teleoperation", "whole-body control"]
    note: >
      Imitation learning and visuomotor policies for humanoid robots;
      egocentric observation spaces; sim-to-real.
    weight: 1.0

  - name: Event-based vision
    keywords: ["event camera", "neuromorphic vision", "event-based"]
    note: >
      Event cameras intersecting with self-supervised learning or
      predictive architectures.
    weight: 0.9

  - name: Egocentric benchmarks and datasets
    keywords: ["Ego4D", "EPIC-KITCHENS", "HD-EPIC", "egocentric benchmark", "Aria"]
    note: New datasets, benchmarks, or evaluation protocols for egocentric perception.
    weight: 0.9

# ── Negative topics (explicit rejections; critical for precision) ─
negative_topics:
  - name: Pure video generation
    note: Diffusion/AR video synthesis judged on visual quality, with no representation-learning, control, or robotics angle.
  - name: Autonomous driving
    note: Unless the method is explicitly egocentric-body-worn or transfers to embodied manipulation.
  - name: Medical / remote-sensing imaging
    note: Domain-specific CV outside embodied perception.
```

**Requirement:** all five retrieval settings and both topic lists must be read at runtime — never hard-code categories, window, caps, or thresholds in the scripts or prompt.

---

## 4. Fetch layer

### 4.1 `scripts/fetch_arxiv.py`

- Build the query from `topics.yml`:
  `(cat:A OR cat:B ...) AND (abs:"kw1" OR abs:"kw2" OR ...)` over the union of all topic keywords. Quote multi-word keywords.
- Endpoint: `http://export.arxiv.org/api/query` with `sortBy=submittedDate&sortOrder=descending`, paging via `start`/`max_results` (page size 100, up to `max_arxiv_results`).
- **Politeness:** sleep ≥3 s between pages (arXiv API terms). Set a descriptive `User-Agent`.
- Keep entries with `published` or `updated` within `window_days`. Include **v2+ updates** of older papers only if the update falls in the window and the ID is not in `seen_ids.json`.
- Emit `work/candidates_arxiv.json`: a list of Candidate objects (schema §4.4) with `source: "arxiv"`.

### 4.2 `scripts/fetch_hf.py`

- For each of the last `window_days` dates: `GET https://huggingface.co/api/daily_papers?date=YYYY-MM-DD`.
- Extract per paper: arXiv ID, title, authors, publication date, abstract/summary, upvotes, HF paper-page URL (`https://huggingface.co/papers/<arxiv_id>`), and any linked GitHub/project URL present in the response.
- Skip dates returning 404/empty (weekends may be sparse). Tolerate schema drift defensively (`.get()` everywhere); log and continue on per-date failures.
- Emit `work/candidates_hf.json` with `source: "hf_daily"` and `hf_upvotes` populated.

### 4.3 `scripts/merge_enrich.py`

1. **Merge** both candidate files, keyed by normalized arXiv ID (strip version suffix: `2506.01234v2 → 2506.01234`). On collision, merge fields; HF-only fields (`hf_upvotes`, `hf_url`, `code_url`) attach to the arXiv record. A paper in both sources sets `in_hf_daily: true` (a useful curation prior for the judge).
2. **Dedup** against `state/seen_ids.json` (schema §8). Drop already-seen IDs unless the new record's `venue.status` upgraded from `preprint` to `accepted` — venue upgrades of previously-featured papers are re-eligible and flagged `is_venue_upgrade: true`.
3. **Venue enrichment**, in priority order:
   - **Semantic Scholar batch endpoint** — one request for the whole set: `POST https://api.semanticscholar.org/graph/v1/paper/batch?fields=venue,publicationVenue,externalIds,openAccessPdf,url` with body `{"ids": ["ARXIV:2506.01234", ...]}` (≤500 IDs/request; chunk if larger). Unauthenticated is fine at this volume; on 429, retry once after 30 s, then degrade gracefully to step b.
   - **arXiv `comments` field regex** — patterns like `[Aa]ccepted (at|to|by)\s+(.+?\d{4})`, `[Tt]o appear in`, `(CVPR|ICCV|ECCV|NeurIPS|ICML|ICLR|ICRA|IROS|CoRL|WACV|AAAI|RSS)\s*'?\d{2,4}`.
   - **arXiv `journal_ref`** if present.
   - Default: `{"status": "preprint", "name": "arXiv"}`.
4. Emit `work/candidates.json` (array of Candidate, schema below) sorted by `published` descending. Print a one-line summary: counts fetched / merged / deduped / final.

### 4.4 Candidate schema (contract between fetch layer and judge)

```json
{
  "arxiv_id": "2506.01234",
  "title": "…",
  "authors": ["A. Author", "B. Author"],
  "published": "2026-06-29",
  "updated": "2026-07-01",
  "abstract": "…",
  "categories": ["cs.CV", "cs.RO"],
  "venue": {"status": "preprint | accepted | published", "name": "arXiv | CVPR 2026 | …"},
  "links": {
    "arxiv_abs": "https://arxiv.org/abs/2506.01234",
    "arxiv_pdf": "https://arxiv.org/pdf/2506.01234",
    "hf_page": "https://huggingface.co/papers/2506.01234 | null",
    "code": "https://github.com/… | null",
    "semantic_scholar": "https://www.semanticscholar.org/paper/… | null",
    "doi": "https://doi.org/… | null"
  },
  "hf_upvotes": 42,
  "in_hf_daily": true,
  "is_venue_upgrade": false,
  "source": "arxiv | hf_daily | both"
}
```

---

## 5. Judgment step (`claude -p`)

### 5.1 Invocation (exact)

```bash
claude -p "$(cat prompts/briefing.md)" \
  --allowedTools "Read,Write" \
  --max-turns 20 \
  --model claude-sonnet-4-6 \
  --output-format json > work/claude_run.json
```

Rationale: `--allowedTools "Read,Write"` pre-approves only file I/O so the run never blocks on a permission prompt and cannot execute shell commands or make network calls; `--max-turns` bounds the agent loop; JSON output captures `total_cost_usd` per run into the workflow log for cost tracking.

### 5.2 `prompts/briefing.md` (ship verbatim; `{{…}}` are literal instructions to Claude, not template variables)

```markdown
You are producing a weekly research briefing. Work only from files in this
repository. Do not fetch anything from the network.

## Inputs
1. Read `topics.yml` — research interests, weights, negative topics, and the
   settings `max_papers` and `min_score`.
2. Read `work/candidates.json` — candidate papers (already deduplicated).

## Scoring
For EVERY candidate assign `raw_score` (0–10): centrality to the positive
topics, judged semantically from title + abstract using each topic's `note`
(keywords are retrieval hints, not criteria). Any match to a `negative_topics`
entry without a strong positive angle caps raw_score at 3.
Compute `final_score = raw_score × weight` of the best-matching topic
(clamped to 10). Treat `in_hf_daily: true` and high `hf_upvotes` as a weak
positive prior worth at most +0.5 — never a substitute for topical fit.

Append one JSON line per candidate to `work/scores.jsonl`:
{"arxiv_id": …, "raw_score": …, "final_score": …, "best_topic": …, "reason": "<one line>"}

## Selection
Keep papers with final_score ≥ min_score, sorted descending, capped at
max_papers. If none qualify, still produce the post with a short "quiet week"
note and the three nearest-miss papers under a "Worth a glance" heading.

## Output post
Write `_posts/{{today, YYYY-MM-DD}}-research-briefing.md`:

---
layout: post
title: "Research Briefing — {{today, D Month YYYY}}"
date: {{today}}
tags: [{{kebab-case names of topics that matched selected papers}}]
---

{{2–3 sentence overview: how many candidates were screened, how many selected,
and the dominant theme(s) this week.}}

Then for each selected paper, in score order:

## {{n}}. [{{title}}]({{links.arxiv_abs}})

**Authors:** {{first 6 authors, then "et al." with total count}}
**Date:** {{published}}{{if updated differs: " (updated {{updated}})"}}
**Venue:** {{if accepted/published: venue.name with a ✅; else "arXiv preprint"}}
{{if is_venue_upgrade}}**Note:** previously featured as a preprint — now accepted at {{venue.name}}.{{endif}}

**TL;DR:** {{2–3 sentences YOU write: (1) the paper's core claim or mechanism,
(2) why it matters for research on egocentric perception, predictive world
models, or embodied learning. Reader-facing language only — never reference
"the user", their specific projects, internal notes, or this pipeline.
No hedging filler; be concrete about the method.}}

<details><summary>Abstract</summary>
{{abstract, verbatim}}
</details>

**Links:** [arXiv]({{arxiv_abs}}) · [PDF]({{arxiv_pdf}})
{{· [HF paper page](…) if present}} {{· [Code](…) if present}}
{{· [Semantic Scholar](…) if present}} {{· [DOI](…) if present}}

---

## State update
Add the arxiv_id of every SELECTED paper to `state/seen_ids.json` under key
"seen", recording {"id", "date", "final_score", "venue_status"}. Do not add
rejected papers (they may resurface after a venue upgrade or v2 revision).

## Hard rules
- Every factual field (title, authors, dates, venue, links, abstract) is copied
  from candidates.json — never invented, never reconstructed from memory.
- Only the overview and TL;DRs are your prose.
- Do not quote or paraphrase abstracts inside TL;DRs; the TL;DR is analysis.
- Output must be valid Jekyll front-matter markdown; no HTML outside <details>.
```

---

## 6. `scripts/validate.py` — hard gate before commit

Parse the newest `_posts/*.md` (use `python-frontmatter`) and assert:

- Front matter contains `layout`, `title`, `date` (matching the filename date), `tags` (non-empty list).
- Paper sections between 1 and `max_papers`; each contains, in order: an `##` heading with a markdown link, `**Authors:**`, `**Date:**`, `**Venue:**`, `**TL;DR:**`, a `<details>` block, and a `**Links:**` line whose first link matches `https://arxiv.org/abs/\d{4}\.\d{4,5}`.
- Every `arxiv.org` ID mentioned in the post exists in `work/candidates.json` (anti-hallucination check).
- `state/seen_ids.json` parses as JSON and grew by exactly the number of selected papers.
- Leak guard: post contains none of (case-insensitive): `the user`, `topics.yml`, `candidates.json`, `research profile`.

Exit non-zero on any failure → the workflow fails **before** commit/deploy, and last week's site stays up. GitHub emails the repo owner on scheduled-workflow failure by default.

---

## 7. Publishing — Jekyll on GitHub Pages

`_config.yml` (verbatim):

```yaml
title: "What I'm Reading"
description: "Weekly research briefing — egocentric vision, world models, robot learning. Auto-curated, Claude-summarized."
theme: minima
plugins: [jekyll-feed]
permalink: /:year/:month/:day/:title/
```

`index.md`: front matter `layout: home` plus one intro paragraph explaining the site is an automated weekly briefing, linking to the repo. The `minima` home layout lists posts reverse-chronologically automatically; `jekyll-feed` serves RSS at `/feed.xml` with zero config. Repo **Settings → Pages → Source: GitHub Actions** (not branch deploy).

---

## 8. State

`state/seen_ids.json` initial content:

```json
{"seen": []}
```

Entries: `{"id": "2506.01234", "date": "2026-07-10", "final_score": 8.4, "venue_status": "preprint"}`. No pruning needed (a year ≈ a few hundred entries). `work/` is gitignored; `state/` and `_posts/` are committed by the bot each run.

---

## 9. Workflow — `.github/workflows/briefing.yml` (ship verbatim)

```yaml
name: Weekly research briefing

on:
  schedule:
    - cron: "17 2 * * 5"   # Fri 02:17 UTC = 04:17 Europe/Berlin in summer (03:17 winter). See §11.
  workflow_dispatch:         # manual runs for testing

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: briefing
  cancel-in-progress: false

jobs:
  briefing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt

      - name: Fetch candidates
        run: |
          mkdir -p work
          python scripts/fetch_arxiv.py
          python scripts/fetch_hf.py
          python scripts/merge_enrich.py

      - name: Short-circuit on empty week
        id: gate
        run: |
          n=$(python -c "import json;print(len(json.load(open('work/candidates.json'))))")
          echo "count=$n" >> "$GITHUB_OUTPUT"

      - uses: actions/setup-node@v4
        if: steps.gate.outputs.count != '0'
        with: { node-version: "22" }
      - run: npm install -g @anthropic-ai/claude-code
        if: steps.gate.outputs.count != '0'

      - name: Generate briefing
        if: steps.gate.outputs.count != '0'
        run: |
          claude -p "$(cat prompts/briefing.md)" \
            --allowedTools "Read,Write" \
            --max-turns 20 \
            --model claude-sonnet-4-6 \
            --output-format json > work/claude_run.json
          python -c "import json;d=json.load(open('work/claude_run.json'));print('cost_usd:',d.get('total_cost_usd'))"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Validate
        if: steps.gate.outputs.count != '0'
        run: python scripts/validate.py

      - name: Commit
        if: steps.gate.outputs.count != '0'
        run: |
          git config user.name "briefing-bot"
          git config user.email "actions@users.noreply.github.com"
          git add _posts/ state/seen_ids.json
          git diff --cached --quiet || git commit -m "briefing: $(date -u +%F)"
          git push

  deploy:
    needs: briefing
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
        with: { ref: main }          # MUST re-fetch branch tip: the trigger SHA predates the bot's commit
      - uses: actions/configure-pages@v5
      - uses: actions/jekyll-build-pages@v1
      - uses: actions/upload-pages-artifact@v3
      - id: deployment
        uses: actions/deploy-pages@v4
```

Design notes baked into the YAML:

- **`ref: main` in deploy checkout** — `actions/checkout` defaults to the SHA that triggered the workflow, which predates the commit the briefing job just pushed; without the pin, the site deploys stale.
- **Same-workflow deploy** — pushes made with `GITHUB_TOKEN` intentionally do not trigger other workflows (GitHub's recursion guard), so a separate deploy-on-push workflow would silently never fire.
- **Empty-week gate** — skips the Claude step (and its cost) when nothing new matched.
- **`workflow_dispatch`** — first-run testing and re-runs without waiting for Friday.

---

## 10. Authentication and cost (explicit requirement)

Scheduled runs authenticate with an **API key from the Claude Platform console**, stored as the repository secret `ANTHROPIC_API_KEY`. Consequences:

- Runs are **billed pay-per-token against the API account and do not consume the user's Claude Pro/Max subscription rate limits** — the Friday-morning token budget on claude.ai / Claude Code is untouched regardless of when the job runs.
- It is the unambiguous, supported auth path for CI; subscription OAuth in third-party automation has been subject to policy tightening.
- Expected cost with Sonnet: scoring 50–300 abstracts plus writing ~8 TL;DRs is a single-digit-cents-per-week workload; `total_cost_usd` from the JSON output is logged each run for verification.

The 04:17 schedule is therefore a reading-freshness choice, not a token-budget necessity — the briefing is live before the workday starts.

---

## 11. Operational notes (implementers must respect these)

1. **Cron is UTC and DST-blind.** `17 2 * * 5` = 04:17 CEST (summer) / 03:17 CET (winter) in Rostock. Acceptable drift for this use case; exact-local-time year-round would need two guarded cron lines and is out of scope.
2. **Off-peak offset.** GitHub delays scheduled workflows during load spikes, worst at minute 0 of the hour — hence `:17`.
3. **60-day auto-disable.** GitHub disables scheduled workflows in repos with no activity for 60 days. The bot's weekly commit is repo activity, so the system self-sustains; note it anyway in the README in case the pipeline is ever paused manually.
4. **arXiv API terms:** ≥3 s between requests, descriptive User-Agent, page size ≤100.
5. **Semantic Scholar:** use the batch endpoint (1 request/run); on 429 retry once, then degrade to comments-regex venue detection — never fail the run over enrichment.
6. **Abstracts** are reproduced verbatim from arXiv metadata (arXiv distributes title/abstract metadata for reuse); everything else on the page is generated analysis.
7. **Privacy:** `topics.yml` is public by design — write notes at the level of research *areas*, not unpublished project specifics. If finer-grained steering is wanted later, materialize a private profile from an Actions secret into `work/` at runtime (never committed) and have the prompt read it; the §6 leak guard already covers the output side.

---

## 12. Acceptance criteria

- [ ] `python scripts/fetch_arxiv.py && python scripts/fetch_hf.py && python scripts/merge_enrich.py` runs locally, producing non-empty `work/candidates.json` matching schema §4.4.
- [ ] Editing `topics.yml` keywords changes the arXiv query on the next run without code edits; editing `min_score`/`max_papers` changes selection without code edits.
- [ ] A paper with "Accepted at ICRA 2026" in its arXiv comments, or a Semantic Scholar venue record, renders `**Venue:** ICRA 2026 ✅`; otherwise `**Venue:** arXiv preprint`.
- [ ] Every briefing entry shows title (linked), authors, date, venue, collapsible verbatim abstract, a relevance-framed TL;DR, and at minimum arXiv abs + PDF links, plus HF/code/S2/DOI links when available.
- [ ] `validate.py` rejects a post with a fabricated arXiv ID, a missing required field, or a leak-guard string (test with deliberately corrupted fixtures).
- [ ] A paper selected once never reappears — except flagged as a venue upgrade.
- [ ] `workflow_dispatch` run on a fresh clone completes end-to-end: post committed, Pages deployed, new post visible at the site root and in `/feed.xml`.
- [ ] Workflow log prints per-run `cost_usd`.
- [ ] Two consecutive runs with no intervening new papers: second run short-circuits at the empty-week gate without invoking Claude.

## 13. Build order

1. `topics.yml`, `requirements.txt`, `.gitignore`, `state/seen_ids.json` skeleton.
2. Fetch scripts + merge/enrich; iterate locally until `work/candidates.json` looks right for the current week (expect ~30–150 candidates).
3. `prompts/briefing.md`; test the judgment step locally with `claude -p` against real candidates; inspect `work/scores.jsonl` and tune nothing in code — only `topics.yml`.
4. `validate.py` with corrupted-fixture tests.
5. Jekyll files; verify local build if desired (`bundle exec jekyll serve`) or rely on the Actions build.
6. Workflow YAML; set the `ANTHROPIC_API_KEY` secret; enable Pages (Source: GitHub Actions); run via `workflow_dispatch`; verify the site; done.
