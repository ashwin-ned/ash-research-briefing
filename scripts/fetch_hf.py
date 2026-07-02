#!/usr/bin/env python3
"""Fetch candidate papers from the Hugging Face Daily Papers API."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

HF_API = "https://huggingface.co/api/daily_papers"
USER_AGENT = "ash-research-briefing/1.0 (+https://github.com/ashwin-ned/ash-research-briefing)"


def load_topics(path="topics.yml"):
    with open(path) as f:
        return yaml.safe_load(f)


def extract_code_url(paper):
    for key in ("githubRepo", "github", "projectPage", "project_page", "codeUrl", "code_url"):
        val = paper.get(key)
        if val:
            return val
    return None


def extract_authors(paper):
    names = []
    for a in paper.get("authors") or []:
        if isinstance(a, dict):
            name = a.get("name") or a.get("fullname")
            if not name and isinstance(a.get("user"), dict):
                name = a["user"].get("fullname")
            if name:
                names.append(name)
        elif isinstance(a, str):
            names.append(a)
    return names


def fetch_date(date_str):
    """Return a list of raw daily-paper items for a date; [] on any failure."""
    try:
        resp = requests.get(
            HF_API,
            params={"date": date_str},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"fetch_hf: {date_str} request failed ({e}); skipping", file=sys.stderr)
        return []

    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        print(f"fetch_hf: {date_str} returned HTTP {resp.status_code}; skipping", file=sys.stderr)
        return []

    try:
        data = resp.json()
    except ValueError:
        print(f"fetch_hf: {date_str} returned non-JSON body; skipping", file=sys.stderr)
        return []

    return data if isinstance(data, list) else []


def main():
    config = load_topics()
    window_days = config.get("window_days", 7)

    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(window_days)]

    candidates = {}
    for d in dates:
        date_str = d.isoformat()
        for item in fetch_date(date_str):
            if not isinstance(item, dict):
                continue
            paper = item.get("paper", item)
            if not isinstance(paper, dict):
                continue

            raw_id = paper.get("id")
            if not raw_id:
                continue
            arxiv_id = raw_id.split("v")[0]  # strip version suffix defensively

            title = paper.get("title") or ""
            summary = paper.get("summary") or paper.get("abstract") or ""
            published = paper.get("publishedAt") or item.get("publishedAt") or date_str
            published = published[:10] if published else date_str

            candidates[arxiv_id] = {
                "arxiv_id": arxiv_id,
                "title": " ".join(title.split()),
                "authors": extract_authors(paper),
                "published": published,
                "abstract": " ".join(summary.split()),
                "links": {
                    "hf_page": f"https://huggingface.co/papers/{arxiv_id}",
                    "code": extract_code_url(paper),
                },
                "hf_upvotes": paper.get("upvotes", 0) or 0,
                "source": "hf_daily",
            }

    result = list(candidates.values())
    os.makedirs("work", exist_ok=True)
    with open("work/candidates_hf.json", "w") as f:
        json.dump(result, f, indent=2)

    print(f"fetch_hf: {len(result)} candidates from HF Daily Papers over {window_days} days")


if __name__ == "__main__":
    main()
