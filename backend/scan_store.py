"""Redis-backed scan queue and state storage shared by the API and worker."""

import json
import os
from datetime import datetime, timezone

from redis import Redis

# The queue stores only scan IDs. The full request and its progress are kept
# under a separate key so the API and worker can read the same scan state.
SCAN_QUEUE = "sift:scans:queued"
# Scan data is temporary: every save renews its lifetime for another 24 hours.
SCAN_TTL_SECONDS = 24 * 60 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis() -> Redis:
    return Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def scan_key(scan_id: str) -> str:
    return f"sift:scan:{scan_id}"


def save_scan(redis_client: Redis, scan: dict) -> None:
    """Persist the latest scan snapshot and renew its expiry time."""
    scan["updated_at"] = utc_now()
    redis_client.set(
        scan_key(scan["scan_id"]),
        json.dumps(scan),
        ex=SCAN_TTL_SECONDS,
    )


def load_scan(redis_client: Redis, scan_id: str) -> dict | None:
    value = redis_client.get(scan_key(scan_id))
    return json.loads(value) if value else None


def create_scan(redis_client: Redis, scan_id: str, request: dict) -> dict:
    """Create the initial state, then make the scan available to the worker."""
    now = utc_now()
    scan = {
        "scan_id": scan_id,
        "status": "queued",
        "request": request,
        "total_communities": len(request["subreddits"]),
        "checked_communities": 0,
        "communities_with_posts": 0,
        "total_posts": 0,
        "processed_posts": 0,
        "results": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    # Save before queueing so a fast worker can always find the request after
    # it removes the scan ID from the queue.
    save_scan(redis_client, scan)
    redis_client.lpush(SCAN_QUEUE, scan_id)
    return scan


def public_scan(scan: dict) -> dict:
    """Return scan state without echoing the internal scoring request."""
    return {key: value for key, value in scan.items() if key != "request"}
