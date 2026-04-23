#!/usr/bin/env python3
"""Transcript Cleanup — YouTube 자막 정제 에이전트 (Gemini Flash-Lite).

Ingester 와 Classifier 사이에 끼어 들어가 YouTube 자막 본문을 prose 로 다듬는다.
구어 자막(ASR 또는 수동)은 필러·중복·구두점 누락이 심해 classifier 가 의미 신호로
활용하기 어렵다. 이 에이전트가 정제본을 `extracted.text_cleaned` 로 원문 옆에
추가 저장하면 classifier 는 정제본 → 원문 순으로 폴백해 읽는다.

Scope:
  - source == "YouTube"
  - fetch_status == "ok"
  - metadata.has_transcript is True
  - 아직 cleanup 되지 않음 (payload.cleaned != True)
  - extracted.text 길이 >= TRANSCRIPT_CLEANUP_MIN_CHARS

실패 정책: 파이프라인 정책대로 개별 실패는 다음 아이템 계속. TokenCapExceeded 시
현재 아이템 없이 종료.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import _bootstrap

_bootstrap.setup(__file__)

from lib import llm as claude, paths  # noqa: E402

log = logging.getLogger("transcript_cleanup")


# ─────────────────────────────────────────────────────────────
# 프롬프트 로딩
# ─────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    return (paths.PROMPTS_DIR / "transcript_cleanup.md").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 대상 선별
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Candidate:
    path: Path
    payload: dict  # 파일 재읽기 피하려고 같이 들고 다님


def _iter_candidates():
    """조건 만족하는 raw/<id>.json 을 순회 (이터레이터)."""
    if not paths.RAW_DIR.exists():
        return
    min_chars = paths.TRANSCRIPT_CLEANUP_MIN_CHARS
    for p in sorted(paths.RAW_DIR.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("raw 파싱 실패 %s: %s", p.name, e)
            continue
        if not _needs_cleanup(payload, min_chars):
            continue
        yield _Candidate(path=p, payload=payload)


def _needs_cleanup(payload: dict, min_chars: int) -> bool:
    item = payload.get("item") or {}
    if item.get("source") != "YouTube":
        return False
    if payload.get("fetch_status") != "ok":
        return False
    if payload.get("cleaned") is True:
        return False
    extracted = payload.get("extracted") or {}
    if not (extracted.get("has_transcript") is True):
        return False
    text = extracted.get("text") or ""
    if len(text) < min_chars:
        return False
    # 이미 text_cleaned 있으면 재작업 방지 (재생성 필요하면 flag 를 수동 제거)
    if extracted.get("text_cleaned"):
        return False
    return True


# ─────────────────────────────────────────────────────────────
# cleanup 수행
# ─────────────────────────────────────────────────────────────

def _clean_one(candidate: _Candidate, system: str) -> bool:
    """한 아이템 정제. 성공 시 raw 파일 갱신 후 True, 실패/캡이면 False."""
    extracted = candidate.payload.get("extracted") or {}
    text = extracted.get("text") or ""

    try:
        result = claude.call_haiku(
            system=system,
            user=text,
            max_tokens=min(4096, max(1024, len(text) // 2 + 512)),
            temperature=0.2,
        )
    except claude.TokenCapExceeded:
        raise  # 호출자에서 루프 중단
    except Exception as e:  # noqa: BLE001
        log.warning("정제 실패 %s: %s", candidate.path.name, e)
        return False

    cleaned = (result.text or "").strip()
    if not cleaned:
        log.warning("정제 결과 빈 문자열: %s — 스킵", candidate.path.name)
        return False

    # 원문을 건드리지 않고 text_cleaned 만 추가 + top-level cleaned 플래그
    extracted["text_cleaned"] = cleaned
    candidate.payload["extracted"] = extracted
    candidate.payload["cleaned"] = True
    candidate.path.write_text(
        json.dumps(candidate.payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "정제 완료 %s (in=%d out=%d tokens)",
        candidate.path.name, result.input_tokens, result.output_tokens,
    )
    return True


# ─────────────────────────────────────────────────────────────
# 실행 본체
# ─────────────────────────────────────────────────────────────

def run(limit: int | None = None, dry_run: bool = False) -> None:
    cap = min(limit or paths.TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP,
              paths.TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP)
    system = _load_system_prompt()

    processed = 0
    skipped_dry = 0
    for candidate in _iter_candidates():
        if processed >= cap:
            log.info("일일 캡(%d) 도달, 중단.", cap)
            break

        item_id = (candidate.payload.get("item") or {}).get("id", candidate.path.stem)
        text_len = len((candidate.payload.get("extracted") or {}).get("text") or "")

        if dry_run:
            log.info("[dry-run] 대상 id=%s len=%d", item_id, text_len)
            skipped_dry += 1
            continue

        try:
            ok = _clean_one(candidate, system)
        except claude.TokenCapExceeded as e:
            log.warning("토큰 캡: %s", e)
            break
        if ok:
            processed += 1

    if dry_run:
        log.info("[dry-run] 완료: 대상 %d건 식별", skipped_dry)
    else:
        log.info("완료: 정제 %d건", processed)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
