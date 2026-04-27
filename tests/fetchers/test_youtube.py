"""YouTube fetcher 단위 테스트.

네트워크 의존성 없음 — video_id 추출과 60초 청크 로직, 재시도·강등 분기,
yt_dlp → Data API → oEmbed 폴백 chain 까지 monkey-patch 로 검증한다.
"""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

os.environ.setdefault("WIKI_REPO_PATH", "/tmp/wiki-agent-youtube-tests")

from lib.fetchers import youtube as yt_mod  # noqa: E402
from lib.fetchers.youtube import (  # noqa: E402
    _extract_video_id,
    _fetch_data_api,
    _fetch_oembed,
    _group_snippets_by_60s,
)


class _MockResp:
    """urllib.request.urlopen context-manager + read() 모방."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> "_MockResp":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


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
        "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
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
        # yt_dlp 자체 재시도 성공 → 폴백 chain 진입 안 함 → degraded 마커 없음
        self.assertNotIn("fetch_degraded", result.metadata)

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
        self.assertNotIn("fetch_degraded", result.metadata)

    def test_transcript_transient_with_meta_downgrades_to_no_transcript(self) -> None:
        """메타 ok + 자막 transient 2회 실패 → no_transcript + fetch_degraded.

        2026-04-27 정책 전환: 종전 status=failed 였으나, oEmbed/Data API 폴백
        도입 이후엔 메타가 채워진 상태에선 best-effort 분류로 보내고 degraded
        마커로 신호한다. text 는 description 폴백.
        """
        def bad_transcript(video_id):
            return None, "list() 실패: ConnectionError: boom"

        with patch.object(yt_mod, "_fetch_metadata_once",
                          return_value=(self._META_OK, None)), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=bad_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "no_transcript")
        self.assertEqual(result.title, "Test Title")
        self.assertEqual(result.text, "desc")
        self.assertTrue(result.metadata.get("fetch_degraded"))
        self.assertEqual(result.metadata.get("fetch_degraded_reason"), "transcript_blocked")
        self.assertFalse(result.metadata["has_transcript"])

    def test_empty_meta_and_no_transcript_downgrades_to_failed(self) -> None:
        """메타도 실패하고 자막도 실제로 없으면 failed (4/23 오염 사건 재현 차단)."""
        def empty_meta(url):
            return {}, "yt_dlp boom"

        def no_transcript(video_id):
            return None, "no_transcript"

        # 폴백 chain 도 모두 실패시켜 진짜 빈 payload 만들기 (Data API 키 없음 + oEmbed 실패)
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=empty_meta), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=no_transcript), \
             patch.object(yt_mod, "_fetch_oembed", return_value=None):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "failed")
        self.assertIn("metadata", result.error)
        self.assertIn("transcript", result.error)
        self.assertEqual(result.title, "")
        self.assertEqual(result.text, "")

    def test_no_transcript_with_meta_keeps_no_transcript_status(self) -> None:
        """자막 진짜 없음 + 메타 있음 = description 폴백 + no_transcript 유지.

        yt_dlp 가 정상이면 폴백 chain 진입 X → fetch_degraded 키 없음.
        """
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
        self.assertNotIn("fetch_degraded", result.metadata)


class TestFallbackChain(unittest.TestCase):
    """yt_dlp 차단 시 Data API → oEmbed 폴백 chain (2026-04-27 도입)."""

    _YT_URL = "https://www.youtube.com/watch?v=abcdefghijk"
    _DATA_API_META = {
        "title": "DataAPI Title",
        "description": "DataAPI Description",
        "channel": "DataAPI Channel",
        "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
    }
    _OEMBED_META = {
        "title": "oEmbed Title",
        "description": "",
        "channel": "oEmbed Author",
        "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
    }

    def setUp(self) -> None:
        self._sleep_patch = patch.object(yt_mod.time, "sleep", lambda *_: None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()

    @staticmethod
    def _yt_dlp_blocked(_url):
        return {}, "DownloadError: Sign in to confirm you're not a bot"

    @staticmethod
    def _no_transcript(_vid):
        return None, "list() 실패: RequestBlocked: cloud IP"

    def test_yt_dlp_blocked_data_api_ok_transcript_blocked(self) -> None:
        """yt_dlp 차단 + Data API 성공 + 자막 차단 → no_transcript + degraded(2 reasons)."""
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=self._yt_dlp_blocked), \
             patch.object(yt_mod, "_fetch_data_api", return_value=self._DATA_API_META), \
             patch.object(yt_mod, "_fetch_oembed", return_value=None), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=self._no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "no_transcript")
        self.assertEqual(result.title, "DataAPI Title")
        self.assertEqual(result.text, "DataAPI Description")
        self.assertEqual(result.metadata["thumbnail"], self._DATA_API_META["thumbnail"])
        self.assertTrue(result.metadata["fetch_degraded"])
        reason = result.metadata["fetch_degraded_reason"]
        self.assertIn("data_api_used", reason)
        self.assertIn("transcript_blocked", reason)

    def test_yt_dlp_blocked_data_api_ok_transcript_ok(self) -> None:
        """yt_dlp 차단 + Data API 성공 + 자막 정상 → ok + degraded(chain only)."""
        def good_transcript(_vid):
            return (("transcript body", "en"), None)

        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=self._yt_dlp_blocked), \
             patch.object(yt_mod, "_fetch_data_api", return_value=self._DATA_API_META), \
             patch.object(yt_mod, "_fetch_oembed", return_value=None), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=good_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.title, "DataAPI Title")
        self.assertEqual(result.text, "transcript body")
        self.assertTrue(result.metadata["has_transcript"])
        self.assertTrue(result.metadata["fetch_degraded"])
        self.assertEqual(
            result.metadata["fetch_degraded_reason"],
            "yt_dlp_blocked_data_api_used",
        )

    def test_yt_dlp_blocked_data_api_failed_oembed_ok_transcript_blocked(self) -> None:
        """yt_dlp 차단 + Data API 실패 + oEmbed 성공 + 자막 차단 → no_transcript, text=""."""
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=self._yt_dlp_blocked), \
             patch.object(yt_mod, "_fetch_data_api", return_value=None), \
             patch.object(yt_mod, "_fetch_oembed", return_value=self._OEMBED_META), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=self._no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "no_transcript")
        self.assertEqual(result.title, "oEmbed Title")
        # oEmbed 는 description 못 줌 → text 빈 문자열. classifier degraded 분기로 분류.
        self.assertEqual(result.text, "")
        self.assertEqual(result.metadata["channel"], "oEmbed Author")
        self.assertTrue(result.metadata["fetch_degraded"])
        reason = result.metadata["fetch_degraded_reason"]
        self.assertIn("oembed_used", reason)
        self.assertIn("transcript_blocked", reason)

    def test_yt_dlp_blocked_all_fallbacks_failed(self) -> None:
        """yt_dlp 차단 + Data API 실패 + oEmbed 실패 → failed."""
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=self._yt_dlp_blocked), \
             patch.object(yt_mod, "_fetch_data_api", return_value=None), \
             patch.object(yt_mod, "_fetch_oembed", return_value=None), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=self._no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.title, "")
        self.assertEqual(result.text, "")

    def test_no_api_key_skips_data_api_uses_oembed(self) -> None:
        """YOUTUBE_API_KEY 없음 + yt_dlp 차단 + oEmbed 성공 → Data API 호출 안 됨, oEmbed 폴백."""
        data_api_calls = {"n": 0}

        def spy_data_api(_vid, _key):
            data_api_calls["n"] += 1
            return self._DATA_API_META

        env = dict(os.environ)
        env.pop("YOUTUBE_API_KEY", None)

        with patch.dict(os.environ, env, clear=True), \
             patch.object(yt_mod, "_fetch_metadata_once", side_effect=self._yt_dlp_blocked), \
             patch.object(yt_mod, "_fetch_data_api", side_effect=spy_data_api), \
             patch.object(yt_mod, "_fetch_oembed", return_value=self._OEMBED_META), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=self._no_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(data_api_calls["n"], 0)  # API 키 없으니 호출 0회
        self.assertEqual(result.status, "no_transcript")
        self.assertEqual(result.title, "oEmbed Title")
        self.assertIn("oembed_used", result.metadata["fetch_degraded_reason"])

    def test_yt_dlp_ok_skips_fallback_chain(self) -> None:
        """yt_dlp 성공 시 Data API / oEmbed 호출 안 됨 (회귀 방지)."""
        meta_ok = {
            "title": "yt_dlp Title",
            "description": "yt_dlp Desc",
            "channel": "ytc",
            "duration": 30,
            "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
        }
        data_api_calls = {"n": 0}
        oembed_calls = {"n": 0}

        def spy_data_api(*_a, **_kw):
            data_api_calls["n"] += 1
            return None

        def spy_oembed(*_a, **_kw):
            oembed_calls["n"] += 1
            return None

        def good_transcript(_vid):
            return (("transcript", "ko"), None)

        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}, clear=False), \
             patch.object(yt_mod, "_fetch_metadata_once", return_value=(meta_ok, None)), \
             patch.object(yt_mod, "_fetch_data_api", side_effect=spy_data_api), \
             patch.object(yt_mod, "_fetch_oembed", side_effect=spy_oembed), \
             patch.object(yt_mod, "_fetch_transcript_once", side_effect=good_transcript):
            result = yt_mod.fetch(self._YT_URL)

        self.assertEqual(data_api_calls["n"], 0)
        self.assertEqual(oembed_calls["n"], 0)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.title, "yt_dlp Title")
        self.assertNotIn("fetch_degraded", result.metadata)


class TestFetchHelpers(unittest.TestCase):
    """_fetch_data_api / _fetch_oembed urllib 단위 테스트."""

    def test_data_api_returns_dict_on_200(self) -> None:
        body = json.dumps({
            "items": [{
                "snippet": {
                    "title": "T",
                    "description": "D",
                    "channelTitle": "C",
                    "thumbnails": {
                        "default": {"url": "low.jpg"},
                        "medium": {"url": "med.jpg"},
                        "high": {"url": "high.jpg"},
                    },
                }
            }]
        }).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_MockResp(body)):
            result = _fetch_data_api("vid123", "key")

        self.assertEqual(result, {
            "title": "T",
            "description": "D",
            "channel": "C",
            "thumbnail": "high.jpg",
        })

    def test_data_api_returns_none_on_4xx(self) -> None:
        err = urllib.error.HTTPError(
            "https://x", 403, "forbidden", {}, io.BytesIO(b"quota exceeded"),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(_fetch_data_api("vid123", "key"))

    def test_data_api_returns_none_on_empty_items(self) -> None:
        body = json.dumps({"items": []}).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=_MockResp(body)):
            self.assertIsNone(_fetch_data_api("vid123", "key"))

    def test_oembed_returns_dict_on_200(self) -> None:
        body = json.dumps({
            "title": "Hello",
            "author_name": "Author",
            "thumbnail_url": "https://i.ytimg.com/x.jpg",
        }).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_MockResp(body)):
            result = _fetch_oembed("https://www.youtube.com/watch?v=vid123")

        self.assertEqual(result, {
            "title": "Hello",
            "description": "",
            "channel": "Author",
            "thumbnail": "https://i.ytimg.com/x.jpg",
        })

    def test_oembed_returns_none_on_timeout(self) -> None:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            self.assertIsNone(_fetch_oembed("https://www.youtube.com/watch?v=vid123"))


if __name__ == "__main__":
    unittest.main()
