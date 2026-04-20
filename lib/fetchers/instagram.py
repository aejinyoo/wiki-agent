"""Instagram 게시물 fetcher — fail-safe placeholder.

IG는 로그인 월 때문에 본문 크롤링이 거의 불가능. 이 fetcher의 목표는
ingester를 실패로 떨어뜨리지 않으면서 원본 URL + 최소 메타만 raw에 남기는 것.
네트워크·파싱·의존성 실패를 포함해 어떤 예외 상황에서도 status="ok" 를 반환한다.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from .base import FetchResult

log = logging.getLogger(__name__)

_POST_TYPE_RE = re.compile(r"/(reel|p|tv)/", re.IGNORECASE)

_TITLE_BY_TYPE = {
    "reel": "Instagram Reel",
    "tv": "Instagram 영상",
    "p": "Instagram 게시물",
}

_PLACEHOLDER_TEXT = "Instagram 원본 확인 필요 — 로그인 후 열람"
_DEFAULT_TITLE = "Instagram 게시물"


def fetch(url: str) -> FetchResult:
    # 바깥쪽 try는 urlparse 등 예상 못한 예외까지 흡수해 절대 status="failed"로
    # 떨어지지 않도록 한다. dispatch 의 공통 except에 맡기지 않음.
    try:
        return _fetch(url)
    except Exception as e:  # noqa: BLE001
        log.info("instagram fetcher unexpected error (%s)", e)
        return _placeholder(url, post_type=None, og_found=False)


def _fetch(url: str) -> FetchResult:
    post_type = _post_type(url)
    html = _try_get_html(url)
    og = _try_parse_og(html) if html else {}

    if og:
        metadata: dict = {"url": url, "post_type": post_type, "og_found": True}
        if og.get("image"):
            metadata["thumbnail"] = og["image"]
        if og.get("video"):
            metadata["is_video"] = True
        return FetchResult(
            status="ok",
            title=og.get("title") or _DEFAULT_TITLE,
            text=og.get("description") or _PLACEHOLDER_TEXT,
            metadata=metadata,
        )

    return _placeholder(url, post_type=post_type, og_found=False)


def _placeholder(url: str, post_type: str | None, og_found: bool) -> FetchResult:
    return FetchResult(
        status="ok",
        title=_TITLE_BY_TYPE.get(post_type or "", _DEFAULT_TITLE),
        text=_PLACEHOLDER_TEXT,
        metadata={
            "url": url,
            "post_type": post_type,
            "fetch_attempted": True,
            "og_found": og_found,
        },
    )


def _post_type(url: str) -> str | None:
    m = _POST_TYPE_RE.search(urlparse(url).path)
    return m.group(1).lower() if m else None


def _try_get_html(url: str) -> str:
    try:
        import requests
    except ImportError as e:
        log.info("instagram fetcher: requests 없음 (%s)", e)
        return ""
    try:
        r = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko,en;q=0.9",
            },
        )
    except Exception as e:  # noqa: BLE001
        log.info("instagram fetcher network error (%s)", e)
        return ""
    if r.status_code != 200:
        log.info("instagram fetcher: HTTP %s %s", r.status_code, url)
        return ""
    return r.text


def _try_parse_og(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        log.info("instagram fetcher: bs4 없음 (%s)", e)
        return {}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:  # noqa: BLE001
        log.info("instagram fetcher parse error (%s)", e)
        return {}
    out: dict = {}
    for prop in ("og:title", "og:description", "og:image", "og:video"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            out[prop.split(":", 1)[1]] = tag["content"].strip()
    return out
