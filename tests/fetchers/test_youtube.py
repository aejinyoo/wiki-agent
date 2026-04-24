"""YouTube fetcher 단위 테스트.

네트워크 의존성 없음 — video_id 추출과 60초 청크 로직, 재시도·강등 분기까지
monkey-patch 로 검증한다. 실제 fetch() 통합 테스트는 라이브 URL 필요 →
별도 스모크로 수동 실행.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("WIKI_REPO_PATH", "/tmp/wiki-agent-youtube-tests")

from lib.fetchers import youtube as yt_mod  # noqa: E402
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


class TestTransientRetryAndDegrade(unittest.TestCase):
    """transient 실패 재시도·빈 payload 강등 회귀 방지 (2026-04-24 오염 사건).

    monkey-patch 로 `_fetch_metadata_once` / `_fetch_transcript_once` 를 갈아끼워
    네트워크 없이 분기 검증.
    """

    _YT_URL = "https://www.youtube.com/watch?v=abcdefghijk"
    _META_OK = {
        "title": "Test Title",
        "description": "desc",
        "channel": "c",
        "duration": 60,
    }

    def setUp(self) -> None:
        # sleep(_RETRY_BACKOFF_SEC) 을 막아 테스트 속도 유지
        self._sleep_patch = patch.object(yt_mod.time, "sleep", lambda *_: None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()

    def test_metadata_transient_succeeds_on_retry(self) -> None:
        """메타데이터 1차 실패 → 2차 성공이면 최종 ok 경로."""
        calls = {"n": 0}

        def flaky_meta(url):
            calls["n"] += 1
            if calls["n"] == 1:
                return {}, "boom"
            return self._META_OK, None

        def good_transcript(video_id):
            return (("hello world", "en"), None)

        with patch.object(yt_mod, "_fetch_metadata_once", side_effect=flaky_meta), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=good_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.title, "Test Title")
        self.assertEqual(result.text, "hello world")

    def test_transcript_transient_succeeds_on_retry(self) -> None:
        """자막 1차 실패(transient) → 2차 성공이면 최종 ok."""
        calls = {"n": 0}

        def flaky_transcript(video_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None, "list() 실패: ConnectionError: boom"
            return (("transcript text", "ko"), None)

        with patch.object(yt_mod, "_fetch_metadata_once",
                          return_value=(self._META_OK, None)), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=flaky_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.text, "transcript text")
        self.assertEqual(result.metadata["language"], "ko")

    def test_transcript_transient_fails_twice_downgrades_to_failed(self) -> None:
        """메타는 성공이라도 자막이 transient 로 2회 연속 실패하면 failed 강등."""
        def bad_transcript(video_id):
            return None, "list() 실패: ConnectionError: boom"

        with patch.object(yt_mod, "_fetch_metadata_once",
                          return_value=(self._META_OK, None)), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=bad_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "failed")
        self.assertIn("transient", result.error)
        # description 을 폴백으로 쓰지 않음 (transient 를 no_transcript 로 저장 금지)
        self.assertEqual(result.text, "")

    def test_empty_meta_and_no_transcript_downgrades_to_failed(self) -> None:
        """메타도 실패하고 자막도 실제로 없으면 failed (4/23 오염 사건 재현 차단)."""
        def empty_meta(url):
            return {}, "yt_dlp boom"

        def no_transcript(video_id):
            return None, "no_transcript"

        with patch.object(yt_mod, "_fetch_metadata_once", side_effect=empty_meta), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "failed")
        self.assertIn("metadata", result.error)
        self.assertIn("transcript", result.error)
        self.assertEqual(result.title, "")
        self.assertEqual(result.text, "")

    def test_no_transcript_with_meta_keeps_no_transcript_status(self) -> None:
        """자막 진짜 없음 + 메타 있음 = description 폴백 + no_transcript 유지."""
        def no_transcript(video_id):
            return None, "no_transcript"

        with patch.object(yt_mod, "_fetch_metadata_once",
                          return_value=(self._META_OK, None)), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "no_transcript")
        self.assertEqual(result.title, "Test Title")
        self.assertEqual(result.text, "desc")
        self.assertFalse(result.metadata["has_transcript"])


if __name__ == "__main__":
    unittest.main()
