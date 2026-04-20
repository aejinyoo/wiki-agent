"""YouTube 본문/메타 추출 (Task 2에서 고도화 예정)."""

from __future__ import annotations

from .base import FetchResult


def fetch(url: str) -> FetchResult:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return FetchResult(status="failed", error="yt-dlp 미설치")
    opts = {"quiet": True, "skip_download": True, "writesubtitles": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return FetchResult(
        status="ok",
        title=info.get("title", "") or "",
        text=(info.get("description", "") or "")[:20000],
        metadata={
            "channel": info.get("channel"),
            "duration": info.get("duration"),
        },
    )
