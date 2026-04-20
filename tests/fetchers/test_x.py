"""X(Twitter) fetcher 라이브 테스트.

oEmbed는 공개 엔드포인트라 네트워크만 있으면 돈다. rate limit은 느슨하지만
매너로 각 케이스 사이에 1초 쉼.
"""

from __future__ import annotations

import time
import unittest

from lib.fetchers import x

# @Interior의 2014년 노을 트윗 — Twitter oEmbed 공식 예시로 수년째 살아있음
PUBLIC_TWEET_URL = "https://twitter.com/Interior/status/463440424141459456"

# 트윗 ID=1 은 스노우플레이크 할당 전이라 실존하지 않음 → 404
MISSING_TWEET_URL = "https://twitter.com/jack/status/1"


class TestXFetcher(unittest.TestCase):
    def tearDown(self) -> None:
        time.sleep(1)

    def test_public_tweet_parses(self) -> None:
        result = x.fetch(PUBLIC_TWEET_URL)
        self.assertEqual(result.status, "ok", msg=f"error={result.error}")
        self.assertTrue(result.text, "text가 비어있으면 안 됨")
        self.assertTrue(
            result.metadata.get("author"),
            "author_name 이 metadata 에 있어야 함",
        )
        self.assertEqual(result.metadata.get("tweet_url"), PUBLIC_TWEET_URL)

    def test_missing_tweet_login_required(self) -> None:
        result = x.fetch(MISSING_TWEET_URL)
        self.assertEqual(result.status, "login_required")
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
