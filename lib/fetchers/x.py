"""X (Twitter) 트윗 본문을 oEmbed 공개 엔드포인트로 추출.

API 키 불필요. x.com / twitter.com URL 모두 동일 엔드포인트로 처리한다.
"""

from __future__ import annotations

from .base import FetchResult

OEMBED_ENDPOINT = "https://publish.twitter.com/oembed"


def fetch(url: str) -> FetchResult:
    import requests
    from bs4 import BeautifulSoup

    params = {"url": url, "omit_script": "true"}
    try:
        r = requests.get(
            OEMBED_ENDPOINT,
            params=params,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (wiki-agent)"},
        )
    except requests.RequestException as e:
        return FetchResult(status="failed", error=f"network error: {e}")

    # 비공개/삭제 트윗 — Task 5 에서 status 기반 분기 예정
    if r.status_code in (403, 404):
        return FetchResult(
            status="login_required",
            error=f"oEmbed {r.status_code}: {r.text[:200]}",
        )

    if r.status_code != 200:
        return FetchResult(
            status="failed",
            error=f"oEmbed {r.status_code}: {r.text[:200]}",
        )

    try:
        payload = r.json()
    except ValueError as e:
        return FetchResult(status="failed", error=f"oEmbed JSON 파싱 실패: {e}")

    html = payload.get("html") or ""
    if not html:
        return FetchResult(status="failed", error="oEmbed 응답에 html 필드 없음")

    soup = BeautifulSoup(html, "html.parser")
    bq = soup.find("blockquote", class_="twitter-tweet")
    p = bq.find("p") if bq else None
    if p is None:
        return FetchResult(status="failed", error="oEmbed html 구조가 예상과 다름")

    # <a>는 링크만 제거하고 표시 텍스트는 유지
    for a in p.find_all("a"):
        a.unwrap()
    text = p.get_text(separator=" ", strip=True)

    return FetchResult(
        status="ok",
        title="",
        text=text,
        metadata={
            "author": payload.get("author_name"),
            "author_url": payload.get("author_url"),
            "tweet_url": payload.get("url"),
        },
    )
