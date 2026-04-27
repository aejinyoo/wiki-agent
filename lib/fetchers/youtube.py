"""YouTube fetcher — 메타(yt_dlp → Data API v3 → oEmbed) + 자막(youtube-transcript-api).

자막 우선순위 (6단계):
    1. ko 수동
    2. en/en-US/en-GB 수동
    3. 그 외 언어 수동 (첫 발견)
    4. ko 자동 생성
    5. en/en-US/en-GB 자동 생성
    6. 그 외 언어 자동 생성 (첫 발견)

자막이 있으면 60초 단위 문단으로 묶어 `text` 로 반환하고 `status="ok"`.
자막이 없거나 transient 차단이면 `status="no_transcript"` (description 또는 빈 본문).

메타 폴백 chain (yt_dlp 차단 시):
    1. yt_dlp 1차 + 1회 재시도
    2. YouTube Data API v3 (YOUTUBE_API_KEY 있을 때만, 단발)
    3. oEmbed (단발)
    셋 다 실패 → `status="failed"`. 메타 채워지면 `metadata.fetch_degraded=True`
    + `fetch_degraded_reason` 로 어디까지 폴백했는지 기록.

자막 transient 차단(RequestBlocked 등) 도 메타가 채워진 경우 `no_transcript +
fetch_degraded` 로 강등 — title 만으로도 classifier 가 best-effort 분류 가능
(2026-04-24 빈 payload 환각 가드 + classifier degraded 프롬프트 분기로 보호).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import FetchResult

log = logging.getLogger(__name__)

# transient 재시도: 1회만 — YouTube rate-limit 을 자극하지 않기 위해 얕게.
_RETRY_BACKOFF_SEC = 0.75

# 폴백 HTTP 호출 타임아웃
_FALLBACK_TIMEOUT_SEC = 10
_OEMBED_USER_AGENT = "wiki-agent oembed"

_VIDEO_ID_PATTERNS = [
    re.compile(
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/shorts/|"
        r"youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})"
    ),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),  # bare video ID
]


def _extract_video_id(url: str) -> str | None:
    for pat in _VIDEO_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def _fetch_metadata_once(url: str) -> tuple[dict, str | None]:
    """yt_dlp 1회 시도. (meta, error). 성공 시 error=None."""
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        return {}, f"yt_dlp import 실패: {e}"
    opts = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": False,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        return {}, f"{type(e).__name__}: {e}"
    return {
        "title": info.get("title") or "",
        "description": (info.get("description") or "")[:20000],
        "channel": info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or "",
    }, None


def _fetch_metadata(url: str) -> tuple[dict, str | None]:
    """yt_dlp 로 제목·채널·설명·duration·thumbnail 추출. 1회 재시도.

    반환: (meta_dict, error_or_None). 빈 meta 는 `{}` — 호출부에서 빈 dict 로 취급 가능.
    """
    meta, err = _fetch_metadata_once(url)
    if err is None:
        return meta, None
    log.warning("yt_dlp metadata 1차 실패 url=%s err=%s — 재시도", url, err)
    time.sleep(_RETRY_BACKOFF_SEC)
    meta, err2 = _fetch_metadata_once(url)
    if err2 is None:
        return meta, None
    combined = f"{err} | retry: {err2}"
    log.warning("yt_dlp metadata 재시도도 실패 url=%s err=%s", url, combined)
    return {}, combined


def _pick_thumbnail(thumbnails: dict) -> str:
    """Data API thumbnails dict 에서 high → medium → default 순 URL 선택."""
    for size in ("high", "medium", "default"):
        info = thumbnails.get(size) or {}
        url = info.get("url")
        if url:
            return url
    return ""


def _fetch_data_api(video_id: str, api_key: str) -> dict | None:
    """YouTube Data API v3 videos.list 단발 호출. 실패 시 None.

    반환 dict: {title, description, channel, thumbnail}.
    """
    qs = urllib.parse.urlencode({"part": "snippet", "id": video_id, "key": api_key})
    url = f"https://www.googleapis.com/youtube/v3/videos?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=_FALLBACK_TIMEOUT_SEC) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        log.warning("Data API HTTP %s video_id=%s — fallback skip", e.code, video_id)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Data API 네트워크 실패 video_id=%s err=%s", video_id, e)
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("Data API JSON 파싱 실패 video_id=%s err=%s", video_id, e)
        return None
    items = data.get("items") or []
    if not items:
        log.warning("Data API items 비어있음 video_id=%s — 비공개/삭제 추정", video_id)
        return None
    snippet = items[0].get("snippet") or {}
    return {
        "title": snippet.get("title") or "",
        "description": (snippet.get("description") or "")[:20000],
        "channel": snippet.get("channelTitle") or "",
        "thumbnail": _pick_thumbnail(snippet.get("thumbnails") or {}),
    }


def _fetch_oembed(url: str) -> dict | None:
    """YouTube oEmbed 단발 호출. 실패 시 None.

    반환 dict: {title, channel, thumbnail, description=""}. description 은
    oEmbed 응답에 포함되지 않아 빈 문자열로 채운다 (호출부 일관성).
    """
    qs = urllib.parse.urlencode({"url": url, "format": "json"})
    api_url = f"https://www.youtube.com/oembed?{qs}"
    req = urllib.request.Request(api_url, headers={"User-Agent": _OEMBED_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_FALLBACK_TIMEOUT_SEC) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        log.warning("oEmbed HTTP %s url=%s — fallback skip", e.code, url)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("oEmbed 네트워크 실패 url=%s err=%s", url, e)
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("oEmbed JSON 파싱 실패 url=%s err=%s", url, e)
        return None
    return {
        "title": data.get("title") or "",
        "description": "",
        "channel": data.get("author_name") or "",
        "thumbnail": data.get("thumbnail_url") or "",
    }


def _group_snippets_by_60s(fetched) -> str:
    """자막 snippet 리스트를 60초 단위 문단으로 묶어 줄바꿈 2개로 연결."""
    chunks: list[str] = []
    current: list[str] = []
    chunk_start = 0.0
    for snippet in fetched:
        if isinstance(snippet, dict):
            start = snippet.get("start", 0) or 0
            text = (snippet.get("text") or "").strip()
        else:
            start = getattr(snippet, "start", 0) or 0
            text = (getattr(snippet, "text", "") or "").strip()
        if not text:
            continue
        if start - chunk_start > 60 and current:
            chunks.append(" ".join(current))
            current = []
            chunk_start = start
        current.append(text)
    if current:
        chunks.append(" ".join(current))
    return "\n\n".join(chunks)


def _pick_transcript(transcript_list):
    """언어 우선순위 6단계로 자막 객체 선택. 반환: (transcript, language_label)."""
    # 1. ko 수동
    try:
        return transcript_list.find_manually_created_transcript(["ko"]), "ko"
    except Exception:  # noqa: BLE001
        pass
    # 2. en/en-US/en-GB 수동
    try:
        return transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"]), "en"
    except Exception:  # noqa: BLE001
        pass
    # 3. 그 외 언어 수동 (첫 발견)
    try:
        for t in transcript_list:
            if not t.is_generated:
                return t, t.language_code
    except Exception:  # noqa: BLE001
        pass
    # 4. ko 자동
    try:
        return transcript_list.find_generated_transcript(["ko"]), "ko (auto)"
    except Exception:  # noqa: BLE001
        pass
    # 5. en/en-US/en-GB 자동
    try:
        return transcript_list.find_generated_transcript(["en", "en-US", "en-GB"]), "en (auto)"
    except Exception:  # noqa: BLE001
        pass
    # 6. 그 외 언어 자동 (첫 발견)
    try:
        for t in transcript_list:
            if t.is_generated:
                return t, f"{t.language_code} (auto)"
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _fetch_transcript_once(video_id: str) -> tuple[tuple[str, str] | None, str | None]:
    """자막 추출 1회 시도. 성공 시 ((plain, language), None), 실패 시 (None, error).

    `_no_transcript_available` 같은 "자막이 진짜로 없다"는 신호는 error="no_transcript"
    로 구별해 반환 — 호출부에서 이건 재시도 대상이 아님을 판단할 수 있게.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError as e:
        return None, f"youtube_transcript_api import 실패: {e}"

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
    except Exception as e:  # noqa: BLE001
        return None, f"list() 실패: {type(e).__name__}: {e}"

    transcript, language = _pick_transcript(transcript_list)
    if transcript is None or language is None:
        return None, "no_transcript"

    try:
        fetched = transcript.fetch()
    except Exception as e:  # noqa: BLE001
        return None, f"fetch() 실패: {type(e).__name__}: {e}"

    plain = _group_snippets_by_60s(fetched)
    if not plain:
        return None, "empty_after_grouping"
    return (plain, language), None


def _fetch_transcript(video_id: str) -> tuple[tuple[str, str] | None, str | None]:
    """자막 추출. 1회 재시도. 성공 시 ((plain, language), None), 실패 시 (None, error).

    error == "no_transcript" 는 "이 영상엔 자막 자체가 없다" — 재시도하지 않음.
    그 외(네트워크·파싱 등 transient 의심)는 1회 backoff 재시도.
    """
    result, err = _fetch_transcript_once(video_id)
    if err is None:
        return result, None
    if err == "no_transcript":
        # 자막이 실제로 없는 경우 — 재시도 의미 없음
        return None, err

    log.warning("transcript 1차 실패 video_id=%s err=%s — 재시도", video_id, err)
    time.sleep(_RETRY_BACKOFF_SEC)
    result2, err2 = _fetch_transcript_once(video_id)
    if err2 is None:
        return result2, None
    combined = f"{err} | retry: {err2}"
    log.warning("transcript 재시도도 실패 video_id=%s err=%s", video_id, combined)
    return None, combined


def fetch(url: str) -> FetchResult:
    video_id = _extract_video_id(url)
    if not video_id:
        return FetchResult(status="failed", error=f"video ID 추출 실패: {url}")

    meta, meta_err = _fetch_metadata(url)

    degraded_reasons: list[str] = []

    # yt_dlp 차단 → Data API → oEmbed 폴백 chain
    if meta_err is not None:
        api_key = os.environ.get("YOUTUBE_API_KEY")
        api_meta = _fetch_data_api(video_id, api_key) if api_key else None
        if api_meta:
            meta = api_meta
            degraded_reasons.append("yt_dlp_blocked_data_api_used")
        else:
            oembed_meta = _fetch_oembed(url)
            if oembed_meta:
                meta = oembed_meta
                degraded_reasons.append("yt_dlp_blocked_oembed_used")
            # 둘 다 실패면 meta 는 빈 dict — 아래 failed 분기에서 처리

    title = meta.get("title", "") or ""
    description = meta.get("description", "") or ""

    base_metadata: dict = {
        "video_id": video_id,
        "channel": meta.get("channel"),
        "duration": meta.get("duration"),
        "thumbnail": meta.get("thumbnail"),
    }

    transcript, transcript_err = _fetch_transcript(video_id)
    is_real_no_transcript = transcript_err == "no_transcript"
    transcript_transient_failed = transcript is None and not is_real_no_transcript
    if transcript_transient_failed:
        degraded_reasons.append("transcript_blocked")

    def _attach_degraded(metadata: dict) -> dict:
        if degraded_reasons:
            return {
                **metadata,
                "fetch_degraded": True,
                "fetch_degraded_reason": ", ".join(degraded_reasons),
            }
        return metadata

    # 자막 성공 — ok 경로
    if transcript is not None:
        plain, language = transcript
        return FetchResult(
            status="ok",
            title=title,
            text=plain,
            metadata=_attach_degraded(
                {
                    **base_metadata,
                    "language": language,
                    "has_transcript": True,
                }
            ),
        )

    # 자막 실패. 메타까지 비면 failed 강등 — 4/23 빈 payload 환각 오염 재현 차단.
    if not title and not description:
        reason_meta = meta_err or "all fallbacks failed"
        reason_transcript = (
            "no_transcript" if is_real_no_transcript else (transcript_err or "unknown")
        )
        return FetchResult(
            status="failed",
            error=f"metadata: {reason_meta}; transcript: {reason_transcript}",
            metadata=base_metadata,
        )

    # 메타 있음 → no_transcript 로 저장. transient 차단이면 degraded 마커가 붙음.
    return FetchResult(
        status="no_transcript",
        title=title,
        text=description,
        metadata=_attach_degraded(
            {
                **base_metadata,
                "has_transcript": False,
            }
        ),
    )
