"""url_hash 정규화 테스트.

네트워크 의존성 없음. lib.wiki_io 는 import 시점에 WIKI_REPO_PATH 를 읽으므로
테스트에서는 실존하지 않아도 되는 더미 경로로 채워둔다.
"""

from __future__ import annotations

import hashlib
import os
import unittest

os.environ.setdefault("WIKI_REPO_PATH", "/tmp/wiki-agent-url-hash-tests")

from lib.wiki_io import url_hash, url_hash_legacy, url_hashes  # noqa: E402


class TestUrlHashNormalization(unittest.TestCase):
    def test_instagram_igsh_stripped(self) -> None:
        a = "https://www.instagram.com/reel/DDLvZhmTjZn/"
        b = "https://www.instagram.com/reel/DDLvZhmTjZn/?igsh=abcdef123"
        c = "https://www.instagram.com/reel/DDLvZhmTjZn/?igshid=qwe789"
        self.assertEqual(url_hash(a), url_hash(b))
        self.assertEqual(url_hash(a), url_hash(c))

    def test_youtube_si_feature_stripped(self) -> None:
        a = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        b = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abcdef"
        c = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
        self.assertEqual(url_hash(a), url_hash(b))
        self.assertEqual(url_hash(a), url_hash(c))

    def test_x_s_t_ref_stripped(self) -> None:
        a = "https://twitter.com/jack/status/20"
        b = "https://twitter.com/jack/status/20?s=20&t=abcdefgh"
        c = "https://twitter.com/jack/status/20?ref_src=twsrc%5Etfw&ref_url=https%3A%2F%2Fe.com"
        self.assertEqual(url_hash(a), url_hash(b))
        self.assertEqual(url_hash(a), url_hash(c))

    def test_utm_params_stripped(self) -> None:
        a = "https://example.com/post"
        b = "https://example.com/post?utm_source=newsletter&utm_medium=email"
        c = "https://example.com/post?utm_campaign=launch&utm_term=ai&utm_content=v2"
        self.assertEqual(url_hash(a), url_hash(b))
        self.assertEqual(url_hash(a), url_hash(c))

    def test_ad_click_trackers_stripped(self) -> None:
        a = "https://example.com/post"
        self.assertEqual(url_hash(a), url_hash("https://example.com/post?fbclid=xxx"))
        self.assertEqual(url_hash(a), url_hash("https://example.com/post?gclid=yyy"))

    def test_host_case_and_trailing_slash(self) -> None:
        a = "https://example.com/post"
        self.assertEqual(url_hash(a), url_hash("https://EXAMPLE.com/post/"))
        self.assertEqual(url_hash(a), url_hash("HTTPS://Example.COM/post"))

    def test_fragment_stripped(self) -> None:
        a = "https://example.com/post"
        self.assertEqual(url_hash(a), url_hash("https://example.com/post#comments"))

    def test_query_order_doesnt_matter(self) -> None:
        a = "https://example.com/search?q=foo&page=2"
        b = "https://example.com/search?page=2&q=foo"
        self.assertEqual(url_hash(a), url_hash(b))

    def test_meaningful_query_preserved(self) -> None:
        # 컨텐츠 식별자는 정규화 후에도 달라야 함
        a = "https://example.com/post?id=123"
        b = "https://example.com/post?id=456"
        self.assertNotEqual(url_hash(a), url_hash(b))

    def test_youtube_v_param_preserved(self) -> None:
        # si/feature 는 제거되지만 v= 는 남아야 함
        a = "https://www.youtube.com/watch?v=AAAAAA"
        b = "https://www.youtube.com/watch?v=BBBBBB"
        self.assertNotEqual(url_hash(a), url_hash(b))

    def test_path_case_is_preserved(self) -> None:
        # IG/YouTube 셧코드는 case-sensitive → path 대소문자 보존돼야 함
        self.assertNotEqual(
            url_hash("https://www.instagram.com/reel/abcDEF/"),
            url_hash("https://www.instagram.com/reel/ABCdef/"),
        )

    def test_mixed_tracking_and_real_params(self) -> None:
        a = "https://example.com/search?q=hello&utm_source=x&fbclid=abc"
        b = "https://example.com/search?q=hello"
        self.assertEqual(url_hash(a), url_hash(b))


class TestLegacyHashStable(unittest.TestCase):
    """url_hash_legacy 는 기존 저장 데이터 호환 목적이므로 로직이 바뀌면 안 된다."""

    def test_legacy_matches_original_algorithm(self) -> None:
        url = "https://example.com/POST/"
        expected = hashlib.sha1(
            url.strip().lower().rstrip("/").encode("utf-8")
        ).hexdigest()[:12]
        self.assertEqual(url_hash_legacy(url), expected)

    def test_legacy_differs_from_new_when_tracking_present(self) -> None:
        # 과도기 호환의 존재 이유 자체 — 두 해시가 다름을 보증
        url_with_tracking = "https://example.com/post?utm_source=x"
        self.assertNotEqual(
            url_hash(url_with_tracking), url_hash_legacy(url_with_tracking)
        )


class TestUrlHashes(unittest.TestCase):
    def test_returns_single_hash_when_identical(self) -> None:
        url = "https://example.com/post"  # 추적 파라미터 없고 path 소문자
        hashes = url_hashes(url)
        self.assertEqual(len(hashes), 1)
        self.assertEqual(hashes[0], url_hash(url))

    def test_returns_both_hashes_when_diverge(self) -> None:
        url = "https://example.com/post?utm_source=x"
        hashes = url_hashes(url)
        self.assertEqual(len(hashes), 2)
        self.assertIn(url_hash(url), hashes)
        self.assertIn(url_hash_legacy(url), hashes)


if __name__ == "__main__":
    unittest.main()
