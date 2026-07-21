"""Shared RSS cache and rate-limit coordination for all Sift workers."""

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable

from core import normalize_subreddit

CACHE_FRESH_SECONDS = int(os.environ.get("REDDIT_CACHE_FRESH_SECONDS", "900"))
CACHE_TTL_SECONDS = int(os.environ.get("REDDIT_CACHE_TTL_SECONDS", "3600"))
COOLDOWN_SECONDS = int(os.environ.get("REDDIT_COOLDOWN_SECONDS", "600"))
REQUEST_INTERVAL_SECONDS = int(
    os.environ.get("REDDIT_REQUEST_INTERVAL_SECONDS", "60")
)
CANDIDATE_LIMIT = int(os.environ.get("REDDIT_RSS_CANDIDATE_LIMIT", "25"))

COOLDOWN_KEY = "sift:reddit:cooldown"
REQUEST_SLOT_KEY = "sift:reddit:request-slot"


def _feed_key(community: str) -> str:
    return f"sift:reddit:feed:{normalize_subreddit(community).lower()}"


def _load_feed(redis_client, community: str) -> tuple[list[dict], bool]:
    """Return cached posts and whether they are still considered fresh."""
    value = redis_client.get(_feed_key(community))
    if not value:
        return [], False

    cached = json.loads(value)
    fetched_at = datetime.fromisoformat(cached["fetched_at"])
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return cached["posts"], age <= CACHE_FRESH_SECONDS


def _save_feed(redis_client, community: str, posts: list[dict]) -> None:
    redis_client.set(
        _feed_key(community),
        json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "posts": posts,
        }),
        ex=CACHE_TTL_SECONDS,
    )


def _cooldown_remaining(redis_client) -> int:
    remaining = redis_client.ttl(COOLDOWN_KEY)
    return max(0, remaining)


def _wait_for_request_slot(redis_client) -> None:
    """Allow only one Reddit RSS request globally during each interval."""
    while not redis_client.set(
        REQUEST_SLOT_KEY,
        "1",
        nx=True,
        ex=max(1, REQUEST_INTERVAL_SECONDS),
    ):
        time.sleep(1)


def fetch_cached_reddit(
    redis_client,
    community: str,
    requested_limit: int,
    fetcher: Callable,
) -> tuple[list[dict], str | None]:
    """Use fresh cache first, then RSS, with stale fallback during failures."""
    community = normalize_subreddit(community)
    cached_posts, cache_is_fresh = _load_feed(redis_client, community)
    if cache_is_fresh:
        return cached_posts[:requested_limit], None

    cooldown = _cooldown_remaining(redis_client)
    if cooldown:
        if cached_posts:
            return (
                cached_posts[:requested_limit],
                f"Using cached posts for r/{community}; Reddit RSS is cooling down "
                f"for about {cooldown} more seconds.",
            )
        return (
            [],
            f"Could not read r/{community}; Reddit RSS is cooling down for about "
            f"{cooldown} more seconds.",
        )

    _wait_for_request_slot(redis_client)
    posts, error = fetcher(
        community,
        max(requested_limit, CANDIDATE_LIMIT),
        max_attempts=1,
    )
    if not error:
        _save_feed(redis_client, community, posts)
        return posts[:requested_limit], None

    if "429" in error:
        # A 429 is generally shared by requests from the same public IP. Stop
        # every worker from making the restriction worse with immediate retries.
        redis_client.set(COOLDOWN_KEY, "1", ex=COOLDOWN_SECONDS)

    if cached_posts:
        return (
            cached_posts[:requested_limit],
            f"Using cached posts for r/{community} because Reddit RSS failed: {error}",
        )
    return [], f"Could not read r/{community}: {error}"
