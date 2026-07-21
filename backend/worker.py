"""Background worker that fetches Reddit posts and scores queued scans."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from redis.exceptions import TimeoutError as RedisTimeoutError

from core import fetch_reddit, get_client, score_post
from scan_store import SCAN_QUEUE, get_redis, load_scan, save_scan

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sift.worker")


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
        all_posts = []
        # Fetch subreddits sequentially with a pause between them. Sending all
        # Reddit requests at once would make HTTP 429 rate limits more likely.
        for index, community in enumerate(request["subreddits"]):
            if index:
                time.sleep(1)
            posts, error = fetch_reddit(
                community,
                request["post_limit_per_community"],
            )
            if error:
                scan["warnings"].append(f"Could not read r/{community}: {error}")
            all_posts.extend(posts)

        unique_posts = {}
        for post in all_posts:
            key = post.get("url") or f"{post['community']}:{post['title']}"
            unique_posts.setdefault(key, post)
        all_posts = list(unique_posts.values())

        scan["total_posts"] = len(all_posts)
        save_scan(redis_client, scan)

        client = get_client(api_key)
        # Reddit fetching is paced, but scoring can safely process up to five
        # independent posts concurrently to reduce the total scan duration.
        with ThreadPoolExecutor(max_workers=min(5, len(all_posts) or 1)) as executor:
            futures = {
                executor.submit(
                    score_post,
                    client,
                    request["domain"],
                    request["rubric"],
                    post,
                ): post
                for post in all_posts
            }
            for future in as_completed(futures):
                post = futures[future]
                try:
                    scored_post = future.result()
                except Exception as exc:
                    post["score"] = 0
                    post["reason"] = f"error: {exc}"
                    scored_post = post
                    scan["warnings"].append(
                        f"Could not score a post from r/{post['community']}"
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
                # Save after every completed post so dashboard polling shows
                # incremental progress instead of waiting for the entire scan.
                save_scan(redis_client, scan)

        scan["status"] = "completed"
        save_scan(redis_client, scan)
        logger.info("Completed scan %s with %s posts", scan_id, len(all_posts))
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
