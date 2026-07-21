import unittest
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from reddit_cache import COOLDOWN_KEY, fetch_cached_reddit
from scan_store import create_scan, load_scan, public_scan
from worker import process_scan


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.queue = []

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def lpush(self, key, value):
        self.queue.insert(0, (key, value))

    def ttl(self, key):
        return self.expiries.get(key, -2)


class ScanStoreTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.request = {
            "domain": "relationship conversations",
            "rubric": "Score direct relevance",
            "subreddits": ["relationships"],
            "post_limit_per_community": 5,
        }

    def test_create_scan_queues_and_hides_internal_request(self):
        create_scan(self.redis, "scan-1", self.request)

        stored = load_scan(self.redis, "scan-1")

        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["total_communities"], 1)
        self.assertEqual(stored["checked_communities"], 0)
        self.assertEqual(self.redis.queue[0][1], "scan-1")
        self.assertNotIn("request", public_scan(stored))

    @patch("worker.score_post")
    @patch("worker.get_client")
    @patch("worker.fetch_reddit")
    @patch.dict("worker.os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_worker_completes_scan_and_stores_result(
        self,
        fetch_reddit,
        get_client,
        score_post,
    ):
        create_scan(self.redis, "scan-2", self.request)
        fetch_reddit.return_value = ([{
            "title": "A useful conversation",
            "body": "body",
            "url": "https://reddit.example/post",
            "community": "relationships",
        }], None)
        get_client.return_value = MagicMock()
        score_post.side_effect = lambda client, domain, rubric, post: {
            **post,
            "score": 91,
            "reason": "Directly relevant",
        }

        process_scan(self.redis, "scan-2")

        stored = load_scan(self.redis, "scan-2")
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["checked_communities"], 1)
        self.assertEqual(stored["communities_with_posts"], 1)
        self.assertEqual(stored["processed_posts"], 1)
        self.assertEqual(stored["results"][0]["score"], 91)

    def test_reuses_fresh_reddit_cache_without_fetching_again(self):
        fetcher = MagicMock(return_value=([{
            "title": "Cached post",
            "body": "body",
            "url": "https://reddit.example/cached",
            "community": "relationships",
        }], None))

        first_posts, first_warning = fetch_cached_reddit(
            self.redis, "relationships", 5, fetcher
        )
        second_posts, second_warning = fetch_cached_reddit(
            self.redis, "relationships", 5, fetcher
        )

        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(first_posts, second_posts)
        self.assertIsNone(first_warning)
        self.assertIsNone(second_warning)

    def test_429_starts_cooldown_and_stale_cache_can_be_used(self):
        cached_post = {
            "title": "Stale post",
            "body": "body",
            "url": "https://reddit.example/stale",
            "community": "relationships",
        }
        self.redis.set(
            "sift:reddit:feed:relationships",
            json.dumps({
                "fetched_at": datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),
                "posts": [cached_post],
            }),
        )
        fetcher = MagicMock(return_value=([], "HTTP Error 429: Too Many Requests"))

        posts, warning = fetch_cached_reddit(
            self.redis, "relationships", 5, fetcher
        )

        self.assertEqual(posts, [cached_post])
        self.assertIn("Using cached posts", warning)
        self.assertEqual(self.redis.values[COOLDOWN_KEY], "1")


if __name__ == "__main__":
    unittest.main()
