"""
Core Sift logic: fetching, interpreting user requirements, and scoring.

Kept independent of the API layer (FastAPI) on purpose - this module has
no knowledge of HTTP. That means it can later be lifted into a worker
process behind a queue (v2 in the original brief) without changes.
"""

import re
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import feedparser
from openai import OpenAI

REDDIT_RSS_TEMPLATE = "https://www.reddit.com/r/{subreddit}/new/.rss"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"


def get_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def clean_html(raw: str) -> str:
    return re.sub("<[^<]+?>", "", raw or "").strip()


def normalize_subreddit(community: str) -> str:
    """Convert common user-entered subreddit formats to a plain name."""
    community = (community or "").strip()
    community = re.sub(r"^https?://(?:www\.)?reddit\.com/r/", "", community, flags=re.I)
    community = re.sub(r"^/?r/", "", community, flags=re.I)
    return community.strip().strip("/")


def fetch_reddit(
    community: str,
    limit: int = 5,
    timeout: int = 10,
    max_attempts: int = 3,
):
    """Returns (posts, error). error is None on success."""
    community = normalize_subreddit(community)
    url = REDDIT_RSS_TEMPLATE.format(subreddit=community)
    user_agent = os.environ.get(
        "REDDIT_USER_AGENT",
        "python:sift:v1.0 (personal read-only relevance reader)",
    )
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    for attempt in range(max_attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                feed = feedparser.parse(response.read())
            break
        except HTTPError as exc:
            if exc.code != 429 or attempt == max_attempts - 1:
                detail = str(exc)
                if exc.code == 429 and max_attempts > 1:
                    detail += " (Reddit rate limit remained active after retries)"
                return [], detail
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = min(10, max(1, int(retry_after)))
            except (TypeError, ValueError):
                delay = 2 ** (attempt + 1)
            time.sleep(delay)
        except Exception as exc:
            return [], str(exc)

    if feed.bozo and not feed.entries:
        return [], str(feed.bozo_exception)

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
    return posts, None


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```json|^```|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def interpret_requirements(client: OpenAI, user_text: str, num_subreddits: int = 8) -> dict:
    """
    Turns a user's free-form paragraph into a domain, a rubric, and a
    list of candidate subreddits. No fixed categories are imposed - if
    the user's text doesn't imply distinct buckets, the rubric stays a
    single continuous 0-100 relevance scale.
    """
    prompt = f"""A user wants to monitor Reddit for posts relevant to their needs.
Here is what they wrote, in their own words:

\"\"\"{user_text}\"\"\"

Based on this, produce:
1. A concise "domain" description (1-2 sentences) summarizing the topic area to watch for.
2. A "rubric": clear guidance for scoring how relevant a post is to this person's
   stated need, on a 0-100 scale. Derive the scoring logic from what they wrote.
   Do not invent categories or labels unless the user's text clearly implies
   distinct categories themselves - if it doesn't, describe a single continuous
   relevance scale instead.
3. A list of {num_subreddits} candidate subreddits (name only, no "r/" prefix)
   likely to contain posts relevant to this domain.

Respond with ONLY a JSON object, no other text, no markdown fences, in exactly this form:
{{"domain": "...", "rubric": "...", "subreddits": ["...", "..."]}}"""

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"thinking": {"type": "disabled"}},
    )
    return _extract_json(response.choices[0].message.content)


def build_score_prompt(domain: str, rubric: str, post: dict) -> str:
    return f"""You are scoring a single Reddit post for relevance.

Domain: {domain}
Rubric: {rubric}

Post title: {post['title']}
Post body: {post['body'][:1500]}

Respond with ONLY a JSON object, no other text, no markdown fences, in exactly this form:
{{"score": <integer 0-100>, "reason": "<one sentence explaining the score>"}}"""


def score_post(client: OpenAI, domain: str, rubric: str, post: dict) -> dict:
    prompt = build_score_prompt(domain, rubric, post)
    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"thinking": {"type": "disabled"}},
        )
        result = _extract_json(response.choices[0].message.content)
        post["score"] = max(0, min(100, int(result.get("score", 0))))
        reason = result.get("reason", "")
        post["reason"] = reason if isinstance(reason, str) else str(reason)
    except Exception as e:
        post["score"] = 0
        post["reason"] = f"error: {e}"
    return post
