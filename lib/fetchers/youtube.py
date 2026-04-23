"""YouTube fetcher — 메타(yt_dlp) + 자막(youtube-transcript-api).

자막 우선순위 (6단계):
    1. ko 수동
    2. en/en-US/en-GB 수동
    3. 그 외 언어 수동 (첫 발견)
    4. ko 자동 생성
    5. en/en-US/en-GB 자동 생성
    6. 그 외 언어 자동 생성 (첫 발견)

자막이 있으면 60초 단위 문단으로 묶어 `text` 로 반환하고 `status="ok"`.
자막이 없으면 yt_dlp description 으로 폴백하고 `status="no_transcript"` —
ingester 가 저장 루트로 태우되 degraded 플래그는 raw payload 의 fetch_status 에 보존.
video ID 추출 실패 등 치명적 오류만 `status="failed"`.
"""

from __future__ import annotations

import re

from .base import FetchResult

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


def _fetch_metadata(url: str) -> dict:
    """yt_dlp 로 제목·채널·설명·duration 추출. 실패 시 빈 dict."""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return {}
    opts = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": False,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:  # noqa: BLE001
        return {}
    return {
        "title": info.get("title") or "",
        "description": (info.get("description") or "")[:20000],
        "channel": info.get("channel") or "",
        "duration": info.get("duration"),
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


def _fetch_transcript(video_id: str) -> tuple[str, str] | None:
    """자막 추출. 성공 시 (plain_text, language), 실패 시 None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError:
        return None

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
    except Exception:  # noqa: BLE001
        return None

    transcript, language = _pick_transcript(transcript_list)
    if transcript is None or language is None:
        return None

    try:
        fetched = transcript.fetch()
    except Exception:  # noqa: BLE001
        return None

    plain = _group_snippets_by_60s(fetched)
    if not plain:
        return None
    return plain, language


def fetch(url: str) -> FetchResult:
    video_id = _extract_video_id(url)
    if not video_id:
        return FetchResult(status="failed", error=f"video ID 추출 실패: {url}")

    meta = _fetch_metadata(url)
    transcript = _fetch_transcript(video_id)

    base_metadata: dict = {
        "video_id": video_id,
        "channel": meta.get("channel"),
        "duration": meta.get("duration"),
    }

    if transcript is not None:
        plain, language = transcript
        return FetchResult(
            status="ok",
            title=meta.get("title", "") or "",
            text=plain,
            metadata={
                **base_metadata,
                "language": language,
                "has_transcript": True,
            },
        )

    # 자막 없음 → description 폴백. ingester 는 status="no_transcript" 를 저장 루트로 분기.
    description = meta.get("description", "") or ""
    return FetchResult(
        status="no_transcript",
        title=meta.get("title", "") or "",
        text=description,
        metadata={
            **base_metadata,
            "has_transcript": False,
        },
    )
