import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from core import normalize_subreddit, score_post


class NormalizeSubredditTests(unittest.TestCase):
    def test_accepts_plain_prefixed_and_url_forms(self):
        self.assertEqual(normalize_subreddit("relationships"), "relationships")
        self.assertEqual(normalize_subreddit("r/relationships"), "relationships")
        self.assertEqual(
            normalize_subreddit("https://www.reddit.com/r/relationships/"),
            "relationships",
        )


class ScorePostTests(unittest.TestCase):
    @patch("core._extract_json", return_value={"score": 130, "reason": "Strong fit"})
    def test_clamps_score_to_valid_range(self, _extract_json):
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [MagicMock()]
        post = {"title": "Example", "body": "Example body"}

        result = score_post(client, "relationships", "judge relevance", post)

        self.assertEqual(result["score"], 100)
        self.assertEqual(result["reason"], "Strong fit")


class FetchRedditTests(unittest.TestCase):
    @patch("core.time.sleep")
    @patch("core.urlopen")
    def test_retries_rate_limit_before_succeeding(self, urlopen, sleep):
        from core import fetch_reddit

        rate_limit = HTTPError(
            url="https://reddit.example",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "1"},
            fp=None,
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<feed></feed>"
        urlopen.side_effect = [rate_limit, response]

        posts, error = fetch_reddit("relationships")

        self.assertEqual(posts, [])
        self.assertIsNone(error)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
