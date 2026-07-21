import unittest
from unittest.mock import MagicMock, patch

from scan_store import create_scan, load_scan, public_scan
from worker import process_scan


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.queue = []

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def lpush(self, key, value):
        self.queue.insert(0, (key, value))


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
        self.assertEqual(stored["processed_posts"], 1)
        self.assertEqual(stored["results"][0]["score"], 91)


if __name__ == "__main__":
    unittest.main()
