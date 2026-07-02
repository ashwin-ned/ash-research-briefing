# ash-research-briefing

A zero-maintenance weekly research briefing. Every Friday at ~04:00
Europe/Berlin, a GitHub Actions pipeline:

1. Fetches new papers from arXiv and Hugging Face Daily Papers.
2. Filters and ranks them against [`topics.yml`](topics.yml) using Claude.
3. Publishes a Jekyll post with title, authors, date, venue, full abstract,
   a relevance-framed TL;DR, and source links — live on this repo's GitHub
   Pages site and via [RSS](../../feed.xml).

## Tuning what you see

Edit [`topics.yml`](topics.yml) — no code changes needed:

- `topics` / `negative_topics` — add, remove, or reweight research areas.
  `keywords` drive the arXiv search query (recall); `note` guides Claude's
  semantic judgment (precision).
- `min_score` / `max_papers` — how selective the briefing is and how long it
  gets.
- `window_days` / `arxiv_categories` / `max_arxiv_results` — retrieval
  settings.

Changes take effect on the next scheduled run — nothing else to redeploy.

## One-time setup

1. **Repo secret** — Settings → Secrets and variables → Actions → New
   repository secret → `ANTHROPIC_API_KEY` (a Claude Platform API key; this
   authenticates CI runs against pay-per-token billing and does **not**
   touch a Claude Pro/Max subscription).
2. **Pages** — Settings → Pages → Source: **GitHub Actions**.
3. **Test it** — Actions tab → "Weekly research briefing" → Run workflow
   (`workflow_dispatch`), then check the deployed Pages URL and `/feed.xml`.

After that it runs itself every Friday. Pause anytime via Actions →
"Weekly research briefing" → "..." → Disable workflow.

## How it stays alive

GitHub auto-disables scheduled workflows in repos with 60 days of no
activity. The bot's own weekly commit (the new post + updated
`state/seen_ids.json`) counts as activity, so the pipeline is self-sustaining
as long as it keeps finding at least one week with papers to post. If you
pause it manually, re-enabling it from the Actions tab is all that's needed.

## Architecture

```
GitHub Actions (cron, Fri 02:17 UTC)
  ├─ scripts/fetch_arxiv.py    ──► work/candidates_arxiv.json
  ├─ scripts/fetch_hf.py       ──► work/candidates_hf.json
  ├─ scripts/merge_enrich.py   ──► work/candidates.json (dedup + venue enrichment)
  ├─ claude -p (judgment)      ──► _posts/YYYY-MM-DD-research-briefing.md
  │                                work/scores.jsonl, state/seen_ids.json
  ├─ scripts/validate.py       ──► hard gate: malformed post fails the run
  ├─ git commit + push
  └─ Jekyll build → deploy to GitHub Pages
```

Fetching and merging are deterministic Python against public APIs (arXiv
Atom API, Hugging Face Daily Papers, Semantic Scholar Graph API for venue
enrichment). Claude is used only for relevance judgment and TL;DR writing —
never for fetching. A run with no new candidates skips the Claude step
entirely (no cost). Expected cost with Sonnet is single-digit cents/week;
`total_cost_usd` is logged in every workflow run.

Full design spec: [`research-briefing-spec.md`](research-briefing-spec.md).
