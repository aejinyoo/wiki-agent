"""Instagram fetcher 테스트.

핵심 invariant: **어떤 URL이 와도 status='ok' 이고 error=None** 이어야 한다.
og 메타 확보 여부는 best-effort 이므로 assert 하지 않는다.
"""

from __future__ import annotations

import time
import unittest

from lib import fetchers
from lib.fetchers import instagram

PUBLIC_REEL_URL = "https://www.instagram.com/reel/DDLvZhmTjZn/"
# 12자 shortcode 자리에 XXX 로 채운 URL — 실존하지 않을 가능성 매우 높음
MISSING_POST_URL = "https://www.instagram.com/p/XXXXXXXXXXX/"
# DNS 해석이 불가능한 호스트. 네트워크 오류 경로 커버
UNRESOLVABLE_URL = "https://www.instagram.invalid/reel/ABC/"


class TestInstagramFetcher(unittest.TestCase):
    def tearDown(self) -> None:
        time.sleep(1)

    def test_public_reel_returns_ok(self) -> None:
        result = instagram.fetch(PUBLIC_REEL_URL)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error)
        self.assertEqual(result.metadata.get("post_type"), "reel")
        self.assertEqual(result.metadata.get("url"), PUBLIC_REEL_URL)
        self.assertTrue(result.title)
        self.assertTrue(result.text)

    def test_missing_post_returns_ok(self) -> None:
        result = instagram.fetch(MISSING_POST_URL)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error)
        self.assertEqual(result.metadata.get("post_type"), "p")

    def test_unresolvable_host_returns_ok(self) -> None:
        result = instagram.fetch(UNRESOLVABLE_URL)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error)
        # og 를 얻을 수 없으므로 placeholder 본문
        self.assertIn("Instagram 원본", result.text)
        self.assertFalse(result.metadata.get("og_found"))
        self.assertTrue(result.metadata.get("fetch_attempted"))


class TestIngesterNeverFailsOnInstagram(unittest.TestCase):
    """ingester.extract_content 의 실패 분기는 dict 에 'error' 키가 있는지로 정해진다.
    IG 는 dispatch 결과의 error가 항상 None 이므로 key 자체가 들어가지 않아야 한다.
    """

    def tearDown(self) -> None:
        time.sleep(1)

    def _extracted_dict(self, url: str) -> dict:
        # ingester.extract_content 와 동일한 shape 변환
        result = fetchers.dispatch(url, "Instagram")
        out: dict = {"title": result.title, "text": result.text, **result.metadata}
        if result.error:
            out["error"] = result.error
        return out

    def test_no_error_key_for_any_ig_url(self) -> None:
        for url in (PUBLIC_REEL_URL, MISSING_POST_URL, UNRESOLVABLE_URL):
            with self.subTest(url=url):
                d = self._extracted_dict(url)
                self.assertNotIn("error", d, msg=f"url={url} dict={d}")
                self.assertTrue(d.get("title"))
                self.assertTrue(d.get("text"))


if __name__ == "__main__":
    unittest.main()
