"""사용자 클립보드 캡션 검증.

iOS Shortcut이 IG URL 공유 시 `Get Clipboard` 결과를 이슈 body(또는 inbox 블록)에
동봉해 보낸다. Ingester 레이어에서 이 값을 검증해 raw 메타의 `user_caption` 으로
저장하면, classifier가 본문 placeholder보다 우선하는 분류 신호로 활용한다.

검증 규칙:
- None / 빈 문자열 / 공백만 → None (조용히 무시)
- URL 형식 (scheme + netloc 모두 존재) → None (클립보드 오염 케이스, log.info로 흔적)
- 그 외 → strip() 결과 그대로 통과. 글자수 제한 없음.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def validate_user_caption(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if _looks_like_url(s):
        log.info("user_caption 폐기(URL 형식): %r", s[:120])
        return None
    return s


def _looks_like_url(s: str) -> bool:
    try:
        parsed = urlparse(s)
    except Exception:  # noqa: BLE001
        return False
    return bool(parsed.scheme and parsed.netloc)
