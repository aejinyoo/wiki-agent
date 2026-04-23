"""YouTube fetcher 단위 테스트.

네트워크 의존성 없음 — video_id 추출과 60초 청크 로직만 검증한다.
실제 fetch() 통합 테스트는 라이브 URL 필요 → 별도 스모크로 수동 실행.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("WIKI_REPO_PATH", "/tmp/wiki-agent-youtube-tests")

from lib.fetchers.youtube import _extract_video_id, _group_snippets_by_60s  # noqa: E402


class TestVideoIdExtraction(unittest.TestCase):
    def test_standard_watch_url(self) -> None:
        self.assertEqual(
            _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_watch_url_with_extra_params(self) -> None:
        self.assertEqual(
            _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42&si=abc"),
            "dQw4w9WgXcQ",
        )

    def test_youtu_be_short(self) -> None:
        self.assertEqual(
            _extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_youtu_be_with_query(self) -> None:
        self.assertEqual(
            _extract_video_id("https://youtu.be/dQw4w9WgXcQ?si=abcdef"),
            "dQw4w9WgXcQ",
        )

    def test_shorts(self) -> None:
        self.assertEqual(
            _extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_embed(self) -> None:
        self.assertEqual(
            _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_bare_id(self) -> None:
        self.assertEqual(_extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_id_with_hyphen_and_underscore(self) -> None:
        self.assertEqual(
            _extract_video_id("https://youtu.be/a-_Zx1y2Qw3"),
            "a-_Zx1y2Qw3",
        )

    def test_non_youtube_url_returns_none(self) -> None:
        self.assertIsNone(_extract_video_id("https://example.com/video/abc"))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_extract_video_id(""))

    def test_invalid_id_length_in_bare_returns_none(self) -> None:
        # 11자가 아니면 bare-ID 패턴 실패
        self.assertIsNone(_extract_video_id("short"))
        self.assertIsNone(_extract_video_id("waytoolongvideoidhere"))


class TestSnippetGrouping(unittest.TestCase):
    """60초 단위 문단 묶기 — dict / object 형태 snippet 둘 다 지원."""

    def test_single_chunk_under_60s(self) -> None:
        snippets = [
            {"start": 0.0, "text": "Hello"},
            {"start": 5.0, "text": "world"},
            {"start": 30.0, "text": "everyone"},
        ]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "Hello world everyone")

    def test_splits_at_60s_boundary(self) -> None:
        snippets = [
            {"start": 0.0, "text": "first"},
            {"start": 30.0, "text": "chunk"},
            {"start": 70.0, "text": "second"},  # 70 - 0 > 60
            {"start": 90.0, "text": "chunk"},
        ]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "first chunk\n\nsecond chunk")

    def test_multiple_chunks(self) -> None:
        snippets = [
            {"start": 0.0, "text": "a"},
            {"start": 65.0, "text": "b"},  # > 60 from 0 → new chunk
            {"start": 130.0, "text": "c"},  # > 60 from 65 → new chunk
        ]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "a\n\nb\n\nc")

    def test_empty_text_stripped(self) -> None:
        snippets = [
            {"start": 0.0, "text": "real"},
            {"start": 5.0, "text": "   "},  # 공백만 → 스킵
            {"start": 10.0, "text": ""},    # 빈 문자열 → 스킵
            {"start": 15.0, "text": "words"},
        ]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "real words")

    def test_empty_input(self) -> None:
        self.assertEqual(_group_snippets_by_60s([]), "")

    def test_object_like_snippets(self) -> None:
        """youtube-transcript-api 0.6+ 는 dataclass 객체를 반환할 수 있음."""
        class Snippet:
            def __init__(self, start: float, text: str) -> None:
                self.start = start
                self.text = text

        snippets = [Snippet(0.0, "obj"), Snippet(5.0, "style")]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "obj style")

    def test_text_stripped_of_whitespace(self) -> None:
        snippets = [
            {"start": 0.0, "text": "  hello  "},
            {"start": 5.0, "text": "\tworld\n"},
        ]
        out = _group_snippets_by_60s(snippets)
        self.assertEqual(out, "hello world")


class TestFetchFailureModes(unittest.TestCase):
    """fetch() 의 치명적 실패 케이스 — 네트워크 없이 확인 가능한 것만."""

    def test_bad_url_returns_failed(self) -> None:
        from lib.fetchers.youtube import fetch

        result = fetch("https://example.com/not-youtube")
        self.assertEqual(result.status, "failed")
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
