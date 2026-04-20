"""일반 HTML 페이지 본문 추출."""

from __future__ import annotations

import re

from .base import FetchResult


def fetch(url: str) -> FetchResult:
    import requests
    import trafilatura

    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (wiki-agent)"})
    r.raise_for_status()
    html = r.text
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    return FetchResult(
        status="ok",
        title=title,
        text=extracted[:20000],
        metadata={"html_len": len(html)},
    )
