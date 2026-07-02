#!/usr/bin/env python3
"""Fetch candidate papers from the arXiv Atom API, filtered by topics.yml."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml

ARXIV_API = "http://export.arxiv.org/api/query"
USER_AGENT = (
    "ash-research-briefing/1.0 "
    "(+https://github.com/ashwin-ned/ash-research-briefing; "
    "weekly automated research digest; contact via GitHub issues)"
)
PAGE_SIZE = 100
SLEEP_BETWEEN_PAGES = 3.0

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?\s*$")


def load_topics(path="topics.yml"):
    with open(path) as f:
        return yaml.safe_load(f)


def load_seen_ids(path="state/seen_ids.json"):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        data = json.load(f)
    return {entry["id"] for entry in data.get("seen", [])}


def build_query(config):
    categories = config["arxiv_categories"]
    cat_query = "(" + " OR ".join(f"cat:{c}" for c in categories) + ")"

    keywords, seen_kw = [], set()
    for topic in config.get("topics", []):
        for kw in topic.get("keywords", []):
            if kw not in seen_kw:
                seen_kw.add(kw)
                keywords.append(kw)
    kw_query = "(" + " OR ".join(f'abs:"{kw}"' for kw in keywords) + ")"

    return f"{cat_query} AND {kw_query}"


def normalize_id(raw_id):
    """'http://arxiv.org/abs/2506.01234v2' -> '2506.01234'"""
    m = ARXIV_ID_RE.search(raw_id)
    if m:
        return m.group(1)
    return raw_id.rsplit("/", 1)[-1].split("v")[0]


def parse_date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def fetch_page(search_query, start, max_results):
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    resp = requests.get(
        ARXIV_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def pdf_link_for(entry, arxiv_id):
    for link in getattr(entry, "links", []):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            return link.get("href")
    return f"https://arxiv.org/pdf/{arxiv_id}"


def main():
    config = load_topics()
    window_days = config.get("window_days", 7)
    max_arxiv_results = config.get("max_arxiv_results", 300)
    seen_ids = load_seen_ids()

    search_query = build_query(config)
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=window_days)

    candidates = {}
    start = 0
    first_page = True
    while start < max_arxiv_results:
        page_size = min(PAGE_SIZE, max_arxiv_results - start)
        if not first_page:
            time.sleep(SLEEP_BETWEEN_PAGES)
        first_page = False

        feed = fetch_page(search_query, start, page_size)
        entries = feed.entries
        if not entries:
            break

        for entry in entries:
            arxiv_id = normalize_id(entry.id)
            published_date = parse_date(entry.published)
            updated_date = (
                parse_date(entry.updated) if getattr(entry, "updated", None) else published_date
            )

            in_window = published_date >= window_start
            is_fresh_update = (
                not in_window
                and updated_date >= window_start
                and arxiv_id not in seen_ids
            )
            if not (in_window or is_fresh_update):
                continue

            candidates[arxiv_id] = {
                "arxiv_id": arxiv_id,
                "title": " ".join(entry.title.split()),
                "authors": [a.name for a in getattr(entry, "authors", [])],
                "published": published_date.isoformat(),
                "updated": updated_date.isoformat(),
                "abstract": " ".join(entry.summary.split()),
                "categories": [t.term for t in getattr(entry, "tags", [])],
                "links": {
                    "arxiv_abs": f"https://arxiv.org/abs/{arxiv_id}",
                    "arxiv_pdf": pdf_link_for(entry, arxiv_id),
                    "hf_page": None,
                    "code": None,
                    "semantic_scholar": None,
                    "doi": None,
                },
                # raw fields consumed by merge_enrich.py's venue-enrichment
                # step, not part of the final Candidate schema
                "comment": getattr(entry, "arxiv_comment", None),
                "journal_ref": getattr(entry, "arxiv_journal_ref", None),
                "source": "arxiv",
            }

        if len(entries) < page_size:
            break
        start += page_size

    result = list(candidates.values())
    os.makedirs("work", exist_ok=True)
    with open("work/candidates_arxiv.json", "w") as f:
        json.dump(result, f, indent=2)

    print(f"fetch_arxiv: {len(result)} candidates within the {window_days}-day window")


if __name__ == "__main__":
    main()
