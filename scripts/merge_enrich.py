#!/usr/bin/env python3
"""Merge arXiv + HF candidates, enrich with venue info, dedup against seen state.

Venue enrichment runs before the seen-state dedup (rather than after, as the
step numbering in the spec might suggest at a glance) because the dedup rule
needs each candidate's freshly computed venue.status to detect preprint ->
accepted upgrades of previously-featured papers.
"""
import json
import os
import re
import time

import requests

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "venue,publicationVenue,externalIds,openAccessPdf,url"
S2_CHUNK_SIZE = 500
USER_AGENT = "ash-research-briefing/1.0 (+https://github.com/ashwin-ned/ash-research-briefing)"

COMMENT_PATTERNS = [
    re.compile(r"[Aa]ccepted (?:at|to|by)\s+(?P<venue>.+?\d{4})"),
    re.compile(r"[Tt]o appear in\s+(?P<venue>.+?\d{4})"),
    re.compile(
        r"(?P<venue>(?:CVPR|ICCV|ECCV|NeurIPS|ICML|ICLR|ICRA|IROS|CoRL|WACV|AAAI|RSS)\s*'?\d{2,4})"
    ),
]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def normalize_id(arxiv_id):
    return arxiv_id.split("v")[0]


def merge_candidates(arxiv_list, hf_list):
    merged = {}
    for c in arxiv_list:
        aid = normalize_id(c["arxiv_id"])
        c["arxiv_id"] = aid
        merged[aid] = dict(c)

    for c in hf_list:
        aid = normalize_id(c["arxiv_id"])
        if aid in merged:
            existing = merged[aid]
            existing["links"]["hf_page"] = c["links"].get("hf_page")
            if c["links"].get("code"):
                existing["links"]["code"] = c["links"]["code"]
            existing["hf_upvotes"] = c.get("hf_upvotes", 0)
            existing["in_hf_daily"] = True
            existing["source"] = "both"
        else:
            record = dict(c)
            record.setdefault("categories", [])
            record["updated"] = record.get("updated") or record.get("published")
            record["links"].setdefault("arxiv_abs", f"https://arxiv.org/abs/{aid}")
            record["links"].setdefault("arxiv_pdf", f"https://arxiv.org/pdf/{aid}")
            record["links"].setdefault("semantic_scholar", None)
            record["links"].setdefault("doi", None)
            record["in_hf_daily"] = True
            record["comment"] = None
            record["journal_ref"] = None
            merged[aid] = record

    for c in merged.values():
        c.setdefault("in_hf_daily", False)
        c.setdefault("hf_upvotes", 0)
        c["links"].setdefault("hf_page", None)
        c["links"].setdefault("code", None)
        c["links"].setdefault("semantic_scholar", None)
        c["links"].setdefault("doi", None)

    return merged


def venue_from_comment(comment):
    if not comment:
        return None
    for pattern in COMMENT_PATTERNS:
        m = pattern.search(comment)
        if m:
            return m.group("venue").strip().rstrip(".,;")
    return None


def fetch_s2_batch(arxiv_ids):
    """Return {arxiv_id: s2_paper_dict}; degrades to {} on any failure."""
    results = {}
    for i in range(0, len(arxiv_ids), S2_CHUNK_SIZE):
        chunk_ids = arxiv_ids[i : i + S2_CHUNK_SIZE]
        payload = {"ids": [f"ARXIV:{aid}" for aid in chunk_ids]}
        try:
            resp = requests.post(
                S2_BATCH_URL,
                params={"fields": S2_FIELDS},
                json=payload,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(30)
                resp = requests.post(
                    S2_BATCH_URL,
                    params={"fields": S2_FIELDS},
                    json=payload,
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(
                f"merge_enrich: Semantic Scholar batch failed ({e}); "
                "degrading to arXiv comments/journal_ref for venue"
            )
            return results

        for aid, paper in zip(chunk_ids, data):
            if paper:
                results[aid] = paper
    return results


def enrich_venue(candidate, s2_paper):
    if s2_paper:
        venue_name = None
        pub_venue = s2_paper.get("publicationVenue")
        if isinstance(pub_venue, dict) and pub_venue.get("name"):
            venue_name = pub_venue["name"]
        elif s2_paper.get("venue"):
            venue_name = s2_paper["venue"]

        ext_ids = s2_paper.get("externalIds") or {}
        if s2_paper.get("url"):
            candidate["links"]["semantic_scholar"] = s2_paper["url"]
        if ext_ids.get("DOI"):
            candidate["links"]["doi"] = f"https://doi.org/{ext_ids['DOI']}"

        if venue_name and venue_name.strip().lower() not in ("arxiv.org", "arxiv", ""):
            candidate["venue"] = {"status": "accepted", "name": venue_name.strip()}
            return

    comment_venue = venue_from_comment(candidate.get("comment"))
    if comment_venue:
        candidate["venue"] = {"status": "accepted", "name": comment_venue}
        return

    journal_ref = candidate.get("journal_ref")
    if journal_ref:
        candidate["venue"] = {"status": "published", "name": journal_ref.strip()}
        return

    candidate["venue"] = {"status": "preprint", "name": "arXiv"}


def dedup_against_seen(candidates, seen_entries):
    seen_by_id = {e["id"]: e for e in seen_entries}
    kept = []
    for c in candidates:
        prior = seen_by_id.get(c["arxiv_id"])
        if prior is None:
            c["is_venue_upgrade"] = False
            kept.append(c)
            continue

        was_preprint = prior.get("venue_status") == "preprint"
        now_upgraded = c["venue"]["status"] in ("accepted", "published")
        if was_preprint and now_upgraded:
            c["is_venue_upgrade"] = True
            kept.append(c)
        # else: already featured, no venue upgrade -> drop (never reappears)
    return kept


def main():
    arxiv_list = load_json("work/candidates_arxiv.json", [])
    hf_list = load_json("work/candidates_hf.json", [])
    seen_state = load_json("state/seen_ids.json", {"seen": []})

    merged = merge_candidates(arxiv_list, hf_list)
    merged_count = len(merged)

    arxiv_ids = list(merged.keys())
    s2_results = fetch_s2_batch(arxiv_ids) if arxiv_ids else {}

    for aid, candidate in merged.items():
        enrich_venue(candidate, s2_results.get(aid))
        candidate.pop("comment", None)
        candidate.pop("journal_ref", None)

    deduped = dedup_against_seen(list(merged.values()), seen_state.get("seen", []))
    deduped.sort(key=lambda c: c["published"], reverse=True)

    os.makedirs("work", exist_ok=True)
    with open("work/candidates.json", "w") as f:
        json.dump(deduped, f, indent=2)

    fetched_total = len(arxiv_list) + len(hf_list)
    dropped = merged_count - len(deduped)
    print(
        f"merge_enrich: fetched={fetched_total} merged={merged_count} "
        f"deduped_out={dropped} final={len(deduped)}"
    )


if __name__ == "__main__":
    main()
