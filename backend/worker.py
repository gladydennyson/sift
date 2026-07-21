"""Background worker that fetches Reddit posts and scores queued scans."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from redis.exceptions import TimeoutError as RedisTimeoutError

from core import fetch_reddit, get_client, score_post
from reddit_cache import fetch_cached_reddit
from scan_store import SCAN_QUEUE, get_redis, load_scan, save_scan

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sift.worker")


def _score_and_save_posts(client, redis_client, scan: dict, posts: list[dict]) -> None:
    """Score one subreddit's posts and publish each result immediately."""
    request = scan["request"]
    with ThreadPoolExecutor(max_workers=min(5, len(posts) or 1)) as executor:
        futures = {
            executor.submit(
                score_post,
                client,
                request["domain"],
                request["rubric"],
                post,
            ): post
            for post in posts
        }
        for future in as_completed(futures):
            post = futures[future]
            try:
                scored_post = future.result()
            except Exception as exc:
                post["score"] = 0
                post["reason"] = f"error: {exc}"
                scored_post = post
                logger.warning(
                    "Scan %s could not score a post from r/%s",
                    scan["scan_id"],
                    post["community"],
                )

            scan["results"].append({
                "title": scored_post["title"],
                "url": scored_post["url"],
                "score": scored_post["score"],
                "reason": scored_post["reason"],
                "community": scored_post["community"],
            })
            scan["results"].sort(key=lambda item: item["score"], reverse=True)
            scan["processed_posts"] += 1
            save_scan(redis_client, scan)


def process_scan(redis_client, scan_id: str) -> None:
    """Fetch, score, and persist one scan that was taken from the queue."""
    scan = load_scan(redis_client, scan_id)
    if not scan:
        logger.warning("Discarding missing scan %s", scan_id)
        return

    scan["status"] = "running"
    save_scan(redis_client, scan)

    try:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set on the worker")

        request = scan["request"]
        client = get_client(api_key)
        seen_posts = set()
        # Every worker shares the same Redis feed cache, request pacing, and
        # cooldown. Repeated scans can therefore reuse posts without repeatedly
        # contacting Reddit from the same public IP.
        for community in request["subreddits"]:
            posts, warning = fetch_cached_reddit(
                redis_client,
                community,
                request["post_limit_per_community"],
                fetch_reddit,
            )
            if warning:
                # RSS availability is operational information for developers.
                # It is logged rather than shown above otherwise valid results.
                logger.warning("Scan %s: %s", scan_id, warning)
            scan["checked_communities"] += 1
            if posts:
                scan["communities_with_posts"] += 1

            unique_posts = []
            for post in posts:
                key = post.get("url") or f"{post['community']}:{post['title']}"
                if key not in seen_posts:
                    seen_posts.add(key)
                    unique_posts.append(post)

            scan["total_posts"] += len(unique_posts)
            save_scan(redis_client, scan)
            _score_and_save_posts(client, redis_client, scan, unique_posts)

        scan["status"] = "completed"
        save_scan(redis_client, scan)
        logger.info("Completed scan %s with %s posts", scan_id, scan["total_posts"])
    except Exception as exc:
        logger.exception("Scan %s failed", scan_id)
        scan["status"] = "failed"
        scan["error"] = str(exc)
        save_scan(redis_client, scan)


def run_worker() -> None:
    """Wait continuously for queued scan IDs and process them one at a time."""
    redis_client = get_redis()
    redis_client.ping()
    logger.info("Worker ready; waiting for scans")

    while True:
        try:
            # An idle blocking read can be interrupted by redis-py's internal
            # connection-maintenance timeout. That means "check again", not
            # that the worker has failed.
            item = redis_client.brpop(SCAN_QUEUE, timeout=0)
        except RedisTimeoutError:
            logger.debug("Redis queue read timed out while idle; retrying")
            continue
        if item:
            # BRPOP removes the oldest waiting ID from the queue. With one
            # worker, this means one complete scan is processed at a time.
            _, scan_id = item
            process_scan(redis_client, scan_id)


if __name__ == "__main__":
    run_worker()
