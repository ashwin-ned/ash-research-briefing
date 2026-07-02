#!/usr/bin/env python3
"""Hard gate: validates the newest generated briefing post before commit/deploy.

Exits non-zero on any failure so the workflow fails before the bot commits or
Pages redeploys — last week's site stays live.
"""
import glob
import json
import os
import re
import subprocess
import sys

import frontmatter
import yaml

LEAK_STRINGS = ["the user", "topics.yml", "candidates.json", "research profile"]
ARXIV_ABS_RE = re.compile(r"https://arxiv\.org/abs/\d{4}\.\d{4,5}")
ARXIV_ID_ANYWHERE_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")
SECTION_HEADING_RE = re.compile(r"^##\s+\d+\.\s+\[.+?\]\(.+?\)\s*$", re.MULTILINE)


def fail(msg):
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_max_papers(path="topics.yml"):
    with open(path) as f:
        config = yaml.safe_load(f)
    return config.get("max_papers", 8)


def newest_post_path():
    posts = sorted(glob.glob("_posts/*.md"))
    if not posts:
        fail("no post found in _posts/")
    return posts[-1]


def seen_count_before():
    """Count of entries in the last-committed state/seen_ids.json (pre-run baseline)."""
    try:
        result = subprocess.run(
            ["git", "show", "HEAD:state/seen_ids.json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return len(data.get("seen", []))
    except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
        return 0


def split_sections(body):
    matches = list(SECTION_HEADING_RE.finditer(body))
    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append(body[start:end])
    return sections


def validate_section(section, index):
    required_markers = ["**Authors:**", "**Date:**", "**Venue:**", "**TL;DR:**"]
    positions = []
    for marker in required_markers:
        pos = section.find(marker)
        if pos == -1:
            fail(f"paper section {index}: missing {marker!r}")
        positions.append(pos)
    if positions != sorted(positions):
        fail(f"paper section {index}: required fields out of order")

    if "<details>" not in section or "</details>" not in section:
        fail(f"paper section {index}: missing <details> abstract block")

    links_idx = section.find("**Links:**")
    if links_idx == -1:
        fail(f"paper section {index}: missing **Links:** line")
    links_line = section[links_idx:].splitlines()[0]
    link_urls = re.findall(r"\((https?://[^\s)]+)\)", links_line)
    if not link_urls or not ARXIV_ABS_RE.fullmatch(link_urls[0]):
        got = link_urls[0] if link_urls else "none"
        fail(f"paper section {index}: first Links entry must be an arXiv abs URL, got {got}")


def main():
    post_path = newest_post_path()
    with open(post_path) as f:
        raw_text = f.read()
    post = frontmatter.loads(raw_text)

    for field in ("layout", "title", "date"):
        if not post.get(field):
            fail(f"front matter missing required field: {field}")

    tags = post.get("tags")
    if not isinstance(tags, list) or len(tags) == 0:
        fail("front matter 'tags' must be a non-empty list")

    filename_date_match = re.match(r".*/(\d{4}-\d{2}-\d{2})-", post_path)
    if not filename_date_match:
        fail(f"post filename does not start with a date: {post_path}")
    filename_date = filename_date_match.group(1)
    fm_date = str(post["date"])[:10]
    if fm_date != filename_date:
        fail(f"front matter date {fm_date} does not match filename date {filename_date}")

    body = post.content
    max_papers = load_max_papers()
    sections = split_sections(body)

    is_quiet_week = len(sections) == 0
    if is_quiet_week:
        if "quiet week" not in body.lower():
            fail("no paper sections found and post does not read as a quiet week")
    else:
        if len(sections) > max_papers:
            fail(f"post has {len(sections)} paper sections, exceeds max_papers={max_papers}")
        for i, section in enumerate(sections, start=1):
            validate_section(section, i)

    candidates = []
    if os.path.exists("work/candidates.json"):
        with open("work/candidates.json") as f:
            candidates = json.load(f)
    known_ids = {c["arxiv_id"] for c in candidates}

    mentioned_ids = set(ARXIV_ID_ANYWHERE_RE.findall(body))
    hallucinated = mentioned_ids - known_ids
    if hallucinated:
        fail(f"post references arXiv IDs not present in candidates.json: {sorted(hallucinated)}")

    seen_path = "state/seen_ids.json"
    try:
        with open(seen_path) as f:
            seen_state = json.load(f)
    except (OSError, ValueError) as e:
        fail(f"state/seen_ids.json does not parse as JSON: {e}")

    seen_list = seen_state.get("seen")
    if not isinstance(seen_list, list):
        fail("state/seen_ids.json 'seen' key must be a list")

    growth = len(seen_list) - seen_count_before()
    if growth != len(sections):
        fail(
            f"state/seen_ids.json grew by {growth}, expected exactly "
            f"{len(sections)} (number of selected papers)"
        )

    lowered = raw_text.lower()
    for leak in LEAK_STRINGS:
        if leak in lowered:
            fail(f"leak guard triggered: post contains {leak!r}")

    print(f"validate: OK — {post_path} ({len(sections)} papers, seen_ids grew by {growth})")


if __name__ == "__main__":
    main()
