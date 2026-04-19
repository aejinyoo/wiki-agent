"""Anthropic 클라이언트 래퍼 + 일일 토큰 카운터.

- `call_haiku` / `call_sonnet` 만 기억하면 됨.
- 호출 전 `_usage.json` 체크 → 캡 초과 시 `TokenCapExceeded` 예외.
- 호출 후 usage를 `_usage.json`에 누적 기록.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

from . import paths

log = logging.getLogger(__name__)


class TokenCapExceeded(RuntimeError):
    """당일 토큰 캡 초과 — 에이전트는 조용히 중단해야 함."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


# ─────────────────────────────────────────────────────────────
# 사용량 카운터 (_usage.json)
# ─────────────────────────────────────────────────────────────

def _today_key() -> str:
    return dt.date.today().isoformat()


def _load_usage() -> dict:
    p: Path = paths.USAGE_JSON
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("_usage.json 파싱 실패, 초기화합니다.")
        return {}


def _save_usage(data: dict) -> None:
    paths.USAGE_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _today_usage() -> dict:
    data = _load_usage()
    return data.setdefault(_today_key(), {"haiku": 0, "sonnet": 0})


def _record_usage(kind: str, input_tokens: int, output_tokens: int) -> None:
    data = _load_usage()
    bucket = data.setdefault(_today_key(), {"haiku": 0, "sonnet": 0})
    bucket[kind] = bucket.get(kind, 0) + input_tokens + output_tokens
    _save_usage(data)


def _check_cap(kind: str, cap: int) -> None:
    used = _today_usage().get(kind, 0)
    if used >= cap:
        raise TokenCapExceeded(
            f"{kind} 일일 캡 초과 (used={used} / cap={cap}). 내일 재개됩니다."
        )


# ─────────────────────────────────────────────────────────────
# 호출
# ─────────────────────────────────────────────────────────────

def _client() -> Anthropic:
    return Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용


def call_haiku(
    *,
    system: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> LLMResult:
    _check_cap("haiku", paths.DAILY_TOKEN_CAP_HAIKU)
    resp = _client().messages.create(
        model=paths.MODEL_HAIKU,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    _record_usage("haiku", resp.usage.input_tokens, resp.usage.output_tokens)
    return LLMResult(
        text=text,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        model=paths.MODEL_HAIKU,
    )


def call_sonnet(
    *,
    system: str,
    user: str,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> LLMResult:
    _check_cap("sonnet", paths.DAILY_TOKEN_CAP_SONNET)
    resp = _client().messages.create(
        model=paths.MODEL_SONNET,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    _record_usage("sonnet", resp.usage.input_tokens, resp.usage.output_tokens)
    return LLMResult(
        text=text,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        model=paths.MODEL_SONNET,
    )
