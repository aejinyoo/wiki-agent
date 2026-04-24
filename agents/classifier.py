#!/usr/bin/env python3
"""Classifier — 분류·요약기 (Haiku).

launchd 15분 주기. raw/*.json 중 아직 분류 안 된 것만 처리.
기획서 5.3 — 아이템당 ~2k 입력 + ~500 출력, 하루 최대 30건.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import _bootstrap

_bootstrap.setup(__file__)

from lib import llm as claude, paths  # noqa: E402
from lib.validate import validate_and_fix  # noqa: E402
from lib.wiki_io import (  # noqa: E402
    WikiItem,
    archive_raw,
    iter_unclassified_raw,
    load_raw,
    write_wiki_item,
)

log = logging.getLogger("classifier")


CATEGORIES = [
    "ai-ux-patterns",
    "prompt-ui",
    "agent-interaction",
    "generative-tools",
    "design-system-automation",
    "trend-reports",
]


def _load_prompt() -> str:
    p = paths.PROMPTS_DIR / "classifier.md"
    return p.read_text(encoding="utf-8")


def _load_personal_context() -> str:
    if paths.PERSONAL_CONTEXT.exists():
        return paths.PERSONAL_CONTEXT.read_text(encoding="utf-8")
    return "(개인화 컨텍스트 없음 — 초기 실행)"


def _build_system(personal_context: str) -> str:
    template = _load_prompt()
    return template.replace("{{PERSONAL_CONTEXT}}", personal_context).replace(
        "{{CATEGORIES}}", ", ".join(CATEGORIES)
    )


def _build_user(item_data: dict, extracted: dict) -> str:
    # 토큰 절약: 본문은 1500토큰 근사치로 잘라 보냄 (char 기준 ~6000)
    # transcript_cleanup 이 다듬어 둔 text_cleaned 가 있으면 그것을 우선 (원문은 raw 에 유지).
    text = (extracted.get("text_cleaned") or extracted.get("text") or "")[:6000]
    caption = extracted.get("user_caption") or ""
    caption_block = f"USER_CAPTION: {caption}\n" if caption else ""
    return (
        f"URL: {item_data['url']}\n"
        f"SOURCE: {item_data['source']}\n"
        f"TITLE: {extracted.get('title', '')}\n"
        f"CAPTURED_AT: {item_data['captured_at']}\n"
        f"{caption_block}"
        f"\n---\n본문:\n{text}\n"
    )


def _parse_classifier_output(text: str) -> dict:
    """프롬프트가 JSON만 리턴하도록 강제. 실패 시 기본값."""
    text = text.strip()
    # 코드펜스 제거
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Classifier 출력 JSON 파싱 실패: %s", text[:200])
        data = {}
    return {
        "category": data.get("category", "trend-reports"),
        "tags": data.get("tags", []),
        "summary_3lines": data.get("summary_3lines", ""),
        "confidence": float(data.get("confidence", 0.5)),
        "title": data.get("title", ""),
        "key_takeaways": data.get("key_takeaways", []) or [],
        "why_it_matters": data.get("why_it_matters", "") or "",
        "what_to_try": data.get("what_to_try", "") or "",
        "body_ko": data.get("body_ko", "") or "",
        "original_language": data.get("original_language", "") or "",
    }


def _compose_body(body_ko: str, original_text: str) -> str:
    """frontmatter 이후 markdown 본문을 조립.

    - body_ko 있으면 `## 한국어 요지` 섹션 먼저
    - 원문은 `## 원문 발췌` 로 6000자 cap
    """
    parts: list[str] = []
    if body_ko.strip():
        parts.append(f"## 한국어 요지\n\n{body_ko.strip()}")
    excerpt = (original_text or "").strip()[:6000]
    if excerpt:
        parts.append(f"## 원문 발췌\n\n{excerpt}")
    return "\n\n".join(parts)


def _has_classifiable_signal(extracted: dict) -> bool:
    """LLM 호출 전에 분류에 쓸 신호가 하나라도 있는지 검사.

    URL + 빈 TITLE + 빈 본문 + 빈 USER_CAPTION 으로 Flash-Lite 를 호출하면
    개인화 컨텍스트에만 의존해 환각을 만든다 (2026-04-23 사건).
    text_cleaned / text / title / user_caption 중 하나라도 의미 있으면 True.
    """
    text = (extracted.get("text_cleaned") or extracted.get("text") or "").strip()
    title = (extracted.get("title") or "").strip()
    caption = (extracted.get("user_caption") or "").strip()
    return bool(text or title or caption)


def classify_one(raw_path: Path, system: str) -> WikiItem | None:
    """1건 분류. 성공 시 WikiItem, 스킵(빈 입력)이면 None.

    TokenCapExceeded 는 잡지 않고 호출자에게 전파 — 루프 전체 중단 신호.
    스킵된 raw 는 그대로 두어 다음 실행에서 재처리 가능한 상태를 유지한다.
    """
    payload = load_raw(raw_path)
    item_data = payload["item"]
    extracted = payload.get("extracted", {})

    if not _has_classifiable_signal(extracted):
        log.warning(
            "빈 입력 스킵 %s — title/text/user_caption 모두 비어 LLM 호출 생략 "
            "(fetch_status=%s). raw 는 유지되어 재수집 후 재분류 가능.",
            raw_path.name, payload.get("fetch_status"),
        )
        return None

    user = _build_user(item_data, extracted)

    result = claude.call_haiku(system=system, user=user, max_tokens=2000)

    parsed = _parse_classifier_output(result.text)

    item = WikiItem(
        id=item_data["id"],
        url=item_data["url"],
        source=item_data["source"],
        captured_at=item_data["captured_at"],
        title=parsed["title"] or item_data.get("title", ""),
        author=item_data.get("author", ""),
        summary_3lines=parsed["summary_3lines"],
        tags=parsed["tags"],
        category=parsed["category"],
        confidence=parsed["confidence"],
        body=_compose_body(
            parsed["body_ko"],
            extracted.get("text_cleaned") or extracted.get("text") or "",
        ),
        key_takeaways=parsed["key_takeaways"],
        why_it_matters=parsed["why_it_matters"],
        what_to_try=parsed["what_to_try"],
        body_ko=parsed["body_ko"],
        original_language=parsed["original_language"],
    )

    note = validate_and_fix(item)
    if note:
        log.info("validate notes: %s", note.notes)

    return item


def run(limit: int | None = None, dry_run: bool = False) -> None:
    daily_cap = paths.CLASSIFIER_DAILY_ITEM_CAP
    cap = min(limit or daily_cap, daily_cap)

    personal_context = _load_personal_context()
    system = _build_system(personal_context)

    processed = 0
    skipped = 0
    for raw_path in iter_unclassified_raw():
        if processed >= cap:
            log.info("일일 캡(%d) 도달, 중단.", cap)
            break

        log.info("분류 중: %s", raw_path.name)
        if dry_run:
            log.info("[dry-run] Haiku 호출 스킵: %s", raw_path.name)
            processed += 1
            continue

        try:
            item = classify_one(raw_path, system)
        except claude.TokenCapExceeded as e:
            log.warning("토큰 캡: %s", e)
            break

        if item is None:
            # 빈 입력 등으로 스킵됨 — raw 는 그대로 두어 재분류 가능한 상태 유지
            skipped += 1
            continue

        out = write_wiki_item(item)
        log.info("저장: %s", out.relative_to(paths.WIKI_REPO))

        # 분류 성공 → raw 를 raw-archive/YYYY-MM/ 로 이동 (빈 날짜 폴더 정리 포함)
        archived = archive_raw(raw_path)
        if archived:
            log.info("아카이브: %s", archived.relative_to(paths.WIKI_REPO))

        processed += 1

    log.info("완료: 처리 %d건 · 스킵 %d건", processed, skipped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
