#!/usr/bin/env python3
"""Daily Brief — 일일 브리프 (매일 08:00 Sonnet).

어제 수집분 요약 최대 15개 + 최근 24h 업계 트렌드(있으면) + personal_context → 브리프 생성.
실패 시 전날 브리프 재표시 + 에러 로그.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

import _bootstrap

_bootstrap.setup(__file__)

from lib import llm as claude, paths  # noqa: E402
from lib.wiki_io import iter_wiki_items  # noqa: E402

log = logging.getLogger("daily_brief")


def _load_prompt() -> str:
    return (paths.PROMPTS_DIR / "daily_brief.md").read_text(encoding="utf-8")


def _load_personal_context() -> str:
    if paths.PERSONAL_CONTEXT.exists():
        return paths.PERSONAL_CONTEXT.read_text(encoding="utf-8")
    return "(개인화 컨텍스트 없음)"


def _items_for(target: dt.date, max_items: int = 15) -> list[dict]:
    """target 기준 어제~그제 captured된 아이템들 요약 수집."""
    yesterday = target - dt.timedelta(days=1)
    day_before = target - dt.timedelta(days=2)

    items: list[dict] = []
    for path, post in iter_wiki_items():
        captured = str(post.get("captured_at", ""))[:10]
        try:
            day = dt.date.fromisoformat(captured)
        except ValueError:
            continue
        if day < day_before or day >= target:
            continue
        items.append({
            "title": post.get("title", path.stem),
            "category": post.get("category", "-"),
            "summary": post.get("summary_3lines", "") or (post.content[:200] if post.content else ""),
            "why_it_matters": post.get("why_it_matters", "") or "",
            "what_to_try": post.get("what_to_try", "") or "",
            "url": post.get("url", ""),
            "tags": post.get("tags", []),
            "captured_at": captured,
        })

    items.sort(key=lambda x: x["captured_at"], reverse=True)
    return items[:max_items]


def _build_user_for_date(target: dt.date, items: list[dict], personal_context: str) -> str:
    lines = [
        f"오늘 날짜: {target.isoformat()}",
        "",
        "[개인화 컨텍스트]",
        personal_context,
        "",
        "[어제~그제 수집 아이템]",
    ]
    if not items:
        lines.append("(없음)")
    else:
        for i, it in enumerate(items, 1):
            lines.extend([
                f"{i}. {it['title']}",
                f"   - 카테고리: {it['category']}",
                f"   - 태그: {', '.join(it['tags'])}",
                f"   - 요약: {it['summary'][:400]}",
                f"   - 왜 중요: {it['why_it_matters'][:200]}",
                f"   - 해볼 것: {it['what_to_try'][:200]}",
                f"   - URL: {it['url']}",
                "",
            ])

    lines.extend([
        "",
        "위 데이터를 바탕으로 프롬프트의 템플릿 형식에 맞춰 브리프를 생성하세요.",
    ])
    return "\n".join(lines)


def _fallback_brief_for(target: dt.date, reason: str) -> str:
    """브리프 생성 실패 시 간단한 fallback."""
    return (
        f"# Daily Design Brief — {target.isoformat()}\n\n"
        f"> 브리프 생성 실패: {reason}\n"
        "> 전날 브리프를 참고하세요.\n"
    )


MAX_CATCHUP_DAYS = 0  # 소급 비활성화 (오늘치만 생성)


def _generate_one(target: dt.date, dry_run: bool, force: bool) -> bool:
    """target 날짜의 브리프 1개 생성. 이미 있으면 skip(또는 force)."""
    out_path = paths.DAILY_DIR / f"{target.isoformat()}.md"
    if out_path.exists() and not force:
        log.info("스킵 (이미 존재): %s", out_path.name)
        return False

    items = _items_for(target)
    log.info("[%s] 어제~그제 아이템 %d건", target.isoformat(), len(items))

    if dry_run:
        print(f"\n===== {target.isoformat()} =====")
        print(f"[dry-run] Sonnet 호출 스킵 · items={len(items)}건 · out={out_path.name}")
        return True

    personal_context = _load_personal_context()
    system = _load_prompt()
    user = _build_user_for_date(target, items, personal_context)

    try:
        result = claude.call_sonnet(system=system, user=user, max_tokens=3500)
        content = result.text.strip()
    except claude.TokenCapExceeded as e:
        log.warning("토큰 캡: %s", e)
        content = _fallback_brief_for(target, f"토큰 캡 — {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("브리프 생성 실패")
        content = _fallback_brief_for(target, str(e))

    out_path.write_text(content + "\n", encoding="utf-8")
    log.info("브리프 저장: %s", out_path)
    return True


def run(force: bool = False, dry_run: bool = False, catchup: bool = True) -> None:
    """오늘 브리프 생성. catchup=True면 최근 MAX_CATCHUP_DAYS 중 누락분 소급 생성."""
    today = dt.date.today()

    targets: list[dt.date] = []
    if catchup:
        # 과거 → 오늘 순서로 누락 날짜 수집 (최대 MAX_CATCHUP_DAYS)
        for offset in range(MAX_CATCHUP_DAYS, 0, -1):
            day = today - dt.timedelta(days=offset)
            if not (paths.DAILY_DIR / f"{day.isoformat()}.md").exists():
                targets.append(day)
    targets.append(today)

    log.info("생성 대상: %s", [d.isoformat() for d in targets])
    for day in targets:
        try:
            _generate_one(day, dry_run=dry_run, force=(force and day == today))
        except claude.TokenCapExceeded:
            log.warning("토큰 캡 도달 — 남은 catch-up 중단")
            break


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="오늘 브리프 있어도 덮어쓰기")
    ap.add_argument("--dry-run", action="store_true", help="파일 저장 없이 stdout 출력")
    ap.add_argument("--no-catchup", action="store_true",
                    help="누락된 과거 브리프 소급 생성 건너뛰기")
    args = ap.parse_args()
    run(force=args.force, dry_run=args.dry_run, catchup=not args.no_catchup)


if __name__ == "__main__":
    main()
