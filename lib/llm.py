"""Gemini 클라이언트 래퍼 + 일일 토큰 카운터.

- `call_haiku` (빠르고 저렴한 분류용) / `call_sonnet` (긴 글쓰기·큐레이션용)
  두 함수만 기억하면 됨. 내부 구현은 Google Gemini 로 돌아가지만,
  호출부 영향 최소화를 위해 기존 역할명(해이쿠/소네트)을 그대로 유지합니다.
- 호출 전 `_usage.json` 체크 → 캡 초과 시 `TokenCapExceeded` 예외.
- 호출 후 usage를 `_usage.json`에 누적 기록 (버킷 키도 기존 호환).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types as genai_types

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
# 사용량 카운터 (_usage.json) — 버킷 키는 기존 호환성 위해 haiku/sonnet 유지
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
# Gemini 클라이언트
# ─────────────────────────────────────────────────────────────

_CLIENT: genai.Client | None = None


def _client() -> genai.Client:
    """Gemini 클라이언트 (lazy init). GEMINI_API_KEY 또는 GOOGLE_API_KEY 사용."""
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "환경변수 GEMINI_API_KEY (또는 GOOGLE_API_KEY) 가 설정되지 않았습니다. "
                ".env 또는 GitHub Actions secrets를 확인하세요."
            )
        _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT


def _generate(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    thinking_budget: int | None = None,
) -> tuple[str, int, int]:
    """공통 호출 경로. (text, input_tokens, output_tokens) 반환.

    `thinking_budget` 가 지정되면 Gemini 의 thinking 토큰 상한을 명시적으로 둠.
    `max_output_tokens` 안에 thinking + visible 이 함께 잡히기 때문에 짧은
    출력 작업에서 thinking 이 폭주해 visible=0 이 되는 사고를 막는 데 유용함.
    """
    config_kwargs: dict = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_budget=thinking_budget
        )

    resp = _client().models.generate_content(
        model=model,
        contents=user,
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    text = (resp.text or "").strip()
    usage = getattr(resp, "usage_metadata", None)
    input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
    output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
    thoughts_tokens = getattr(usage, "thoughts_token_count", None) if usage else None

    candidates = getattr(resp, "candidates", None) or []
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    finish_reason_name = getattr(finish_reason, "name", None) or (
        str(finish_reason) if finish_reason is not None else None
    )

    log.info(
        "llm.generate model=%s prompt_tokens=%d candidates_tokens=%d thoughts_tokens=%s finish_reason=%s",
        model,
        input_tokens,
        output_tokens,
        thoughts_tokens if thoughts_tokens is not None else "n/a",
        finish_reason_name or "n/a",
    )
    if finish_reason_name and finish_reason_name != "STOP":
        log.warning(
            "llm.generate non-STOP finish_reason=%s model=%s (output_tokens=%d, thoughts_tokens=%s)",
            finish_reason_name,
            model,
            output_tokens,
            thoughts_tokens if thoughts_tokens is not None else "n/a",
        )

    return text, input_tokens, output_tokens


# ─────────────────────────────────────────────────────────────
# 공개 API (역할 기반 이름 — 기존 호출부 그대로 호환)
# ─────────────────────────────────────────────────────────────

def call_haiku(
    *,
    system: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> LLMResult:
    """빠르고 저렴한 분류용 모델 (Classifier). 기본값: gemini-2.5-flash-lite."""
    _check_cap("haiku", paths.DAILY_TOKEN_CAP_HAIKU)
    text, input_tokens, output_tokens = _generate(
        model=paths.MODEL_HAIKU,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    _record_usage("haiku", input_tokens, output_tokens)
    return LLMResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=paths.MODEL_HAIKU,
    )


def call_sonnet(
    *,
    system: str,
    user: str,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    thinking_budget: int | None = None,
) -> LLMResult:
    """긴 글쓰기·큐레이션용 모델 (Daily Brief / Curator). 기본값: gemini-2.5-pro."""
    _check_cap("sonnet", paths.DAILY_TOKEN_CAP_SONNET)
    text, input_tokens, output_tokens = _generate(
        model=paths.MODEL_SONNET,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_budget=thinking_budget,
    )
    _record_usage("sonnet", input_tokens, output_tokens)
    return LLMResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=paths.MODEL_SONNET,
    )
