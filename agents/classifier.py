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
from lib.wiki_io import WikiItem, iter_unclassified_raw, load_raw, write_wiki_item  # noqa: E402

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
    text = (extracted.get("text") or "")[:6000]
    return (
        f"URL: {item_data['url']}\n"
        f"SOURCE: {item_data['source']}\n"
        f"TITLE: {extracted.get('title', '')}\n"
        f"CAPTURED_AT: {item_data['captured_at']}\n"
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
    }


def classify_one(raw_path: Path, system: str) -> WikiItem | None:
    payload = load_raw(raw_path)
    item_data = payload["item"]
    extracted = payload.get("extracted", {})

    user = _build_user(item_data, extracted)

    try:
        result = claude.call_haiku(system=system, user=user, max_tokens=800)
    except claude.TokenCapExceeded as e:
        log.warning("토큰 캡: %s", e)
        return None

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
        body=(extracted.get("text") or "")[:6000],
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
    for raw_path in iter_unclassified_raw():
        if processed >= cap:
            log.info("일일 캡(%d) 도달, 중단.", cap)
            break

        log.info("분류 중: %s", raw_path.name)
        if dry_run:
            log.info("[dry-run] Haiku 호출 스킵: %s", raw_path.name)
            processed += 1
            continue

        item = classify_one(raw_path, system)
        if item is None:
            break  # 토큰 캡 초과

        out = write_wiki_item(item)
        log.info("저장: %s", out.relative_to(paths.WIKI_REPO))
        processed += 1

    log.info("완료: 처리 %d건", processed)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
