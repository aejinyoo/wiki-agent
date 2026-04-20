"""source 문자열 → fetcher 함수 dispatch.

새 fetcher(예: x, instagram)를 붙일 때는 같은 폴더에 모듈을 추가하고
_DISPATCH 에 source 문자열을 매핑하면 된다.
"""

from __future__ import annotations

import logging

from . import generic, x, youtube
from .base import FetchResult

log = logging.getLogger(__name__)

_DISPATCH = {
    "YouTube": youtube.fetch,
    "X": x.fetch,
}


def dispatch(url: str, source: str) -> FetchResult:
    fetcher = _DISPATCH.get(source, generic.fetch)
    try:
        return fetcher(url)
    except Exception as e:  # noqa: BLE001
        log.warning("본문 추출 실패 url=%s err=%s", url, e)
        return FetchResult(status="failed", error=str(e))


__all__ = ["FetchResult", "dispatch"]
