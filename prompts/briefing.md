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
