#!/usr/bin/env python3
"""
Sift - a generic signal-detection engine.

Point it at any domain via a config file. It watches sources, scores
each post against a rubric using an LLM, and prints the results that
matter most - ranked, not just listed.

The domain and rubric are never hardcoded here. They live entirely in
the config file. Swap the config, and Sift watches something completely
different, with no changes to this script.

Usage:
    python sift.py --config config.json
    python sift.py --config config-argentina.json --top 10
"""

import argparse
import json
import os
import re
import sys

import feedparser
from anthropic import Anthropic

REDDIT_RSS_TEMPLATE = "https://www.reddit.com/r/{subreddit}/new/.rss"
MODEL = "claude-sonnet-4-6"


def load_config(path):
    with open(path, "r") as f:
        return json.load(f)


def clean_html(raw):
    """Strip HTML tags out of RSS summary fields."""
    return re.sub("<[^<]+?>", "", raw or "").strip()


def fetch_reddit(community, limit):
    """Pull recent posts from a subreddit via its RSS feed. No auth needed."""
    url = REDDIT_RSS_TEMPLATE.format(subreddit=community)
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        print(f"  ! could not read r/{community}: {feed.bozo_exception}", file=sys.stderr)
        return []

    posts = []
    for entry in feed.entries[:limit]:
        posts.append({
            "source": "reddit",
            "community": community,
            "title": entry.get("title", ""),
            "body": clean_html(entry.get("summary", "")),
            "url": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return posts


def fetch_posts(config):
    """
    Pull posts for every source + community listed in the config.

    Adding a new source (Twitter, Substack, ...) means adding a new
    fetch_<source>() function and a branch here - nothing about the
    scoring or ranking logic below needs to change.
    """
    posts = []
    limit = config.get("post_limit_per_community", 25)
    sources = config.get("sources", [])
    communities = config.get("communities", [])

    if "reddit" in sources:
        for community in communities:
            posts.extend(fetch_reddit(community, limit))

    return posts


def build_prompt(domain, rubric, post):
    return f"""You are scoring a single post for relevance to a specific domain.

Domain: {domain}
Rubric: {rubric}

Post title: {post['title']}
Post body: {post['body'][:1500]}

Respond with ONLY a JSON object, no other text, no markdown fences, in exactly this form:
{{"score": <integer 0-100>, "bucket": "<a short label defined by the rubric above>", "reason": "<one sentence>"}}"""


def score_post(client, domain, rubric, post):
    prompt = build_prompt(domain, rubric, post)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        text = re.sub(r"^```json|```$", "", text, flags=re.MULTILINE).strip()
        result = json.loads(text)
        post["score"] = int(result.get("score", 0))
        post["bucket"] = result.get("bucket", "unscored")
        post["reason"] = result.get("reason", "")
    except Exception as e:
        post["score"] = 0
        post["bucket"] = "error"
        post["reason"] = str(e)
    return post


def main():
    parser = argparse.ArgumentParser(description="Sift - a generic signal-detection engine")
    parser.add_argument("--config", default="config.json", help="path to a Sift config file")
    parser.add_argument("--top", type=int, default=20, help="how many results to print")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set ANTHROPIC_API_KEY as an environment variable first.", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    client = Anthropic(api_key=api_key)

    print(f"Domain: {config.get('domain')}")
    print("Fetching posts...")
    posts = fetch_posts(config)
    print(f"  pulled {len(posts)} posts. Scoring against the rubric...\n")

    scored = [score_post(client, config["domain"], config["rubric"], p) for p in posts]
    scored.sort(key=lambda p: p["score"], reverse=True)

    print(f"Top {min(args.top, len(scored))} results:\n")
    for p in scored[:args.top]:
        print(f"[{p['score']:>3}] {p['bucket']:<14} r/{p['community']:<20} {p['title']}")
        print(f"       {p['url']}")
        if p.get("reason"):
            print(f"       -> {p['reason']}")
        print()


if __name__ == "__main__":
    main()
