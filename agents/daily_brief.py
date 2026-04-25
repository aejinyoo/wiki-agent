#!/usr/bin/env python3
"""Daily Brief — 일일 브리프 (매일 08:00 Sonnet).

어제 수집분 요약 최대 15개 + 최근 24h 업계 트렌드(있으면) + personal_context → 브리프 생성.
실패 시 전날 브리프 재표시 + 에러 로그.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re

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


# ─────────────────────────────────────────────────────────────
# 최근 브리프 하이라이트 파싱 (중복 추천 방지용)
# ─────────────────────────────────────────────────────────────

# "### 1. [제목](url)" 형태에서 제목·URL 추출
_HIGHLIGHT_RE = re.compile(r"^###\s+\d+\.\s+\[([^\]]+)\]\(([^)]+)\)", re.MULTILINE)
# 하이라이트 카드의 첫 불릿에서 카테고리 추출. 두 포맷 모두 대응:
#   형식 A: "- **generative-tools** · **source.com** · `tag`"  → 첫 bold=카테고리
#   형식 B: "- **카테고리** · generative-tools · `tag`"        → "**카테고리** · " 뒤 평문
_CATEGORY_B_RE = re.compile(r"^-\s+\*\*카테고리\*\*\s*·\s*([a-z0-9-]+)", re.MULTILINE)
_CATEGORY_A_RE = re.compile(r"^-\s+\*\*([a-z0-9-]+)\*\*", re.MULTILINE)


def _recent_brief_highlights(target: dt.date, days: int = 7) -> list[dict]:
    """target 이전 `days`일간 브리프에서 📌 하이라이트로 뽑힌 아이템 추출.

    반환: [{"title": ..., "url": ..., "category": ..., "date": "YYYY-MM-DD"}, ...]
    target 당일 브리프는 제외 (force 재생성 시 자기 자신을 히스토리로 보지 않기 위해).
    """
    out: list[dict] = []
    for offset in range(1, days + 1):
        day = target - dt.timedelta(days=offset)
        p = paths.DAILY_DIR / f"{day.isoformat()}.md"
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue

        # 📌 하이라이트 섹션만 대상 (🧪 실험 테이블의 링크는 제외)
        sec_start = text.find("## 📌")
        if sec_start == -1:
            continue
        sec_end = text.find("\n## ", sec_start + 3)
        section = text[sec_start : sec_end if sec_end != -1 else len(text)]

        for m in _HIGHLIGHT_RE.finditer(section):
            title, url = m.group(1).strip(), m.group(2).strip()
            # 해당 카드 블록 안에서 카테고리 추출 시도
            card_start = m.end()
            next_card = _HIGHLIGHT_RE.search(section, card_start)
            card = section[card_start : next_card.start() if next_card else len(section)]
            # 형식 B("**카테고리** · name")를 먼저, 없으면 형식 A(첫 bold)
            cat_b = _CATEGORY_B_RE.search(card)
            cat_a = None if cat_b else _CATEGORY_A_RE.search(card)
            category = (cat_b.group(1) if cat_b else (cat_a.group(1) if cat_a else ""))
            out.append({
                "title": title,
                "url": url,
                "category": category,
                "date": day.isoformat(),
            })
    return out


def _filter_recent(items: list[dict], recent_urls: set[str]) -> list[dict]:
    """최근 7일 추천 URL 과 겹치는 아이템 제외."""
    return [it for it in items if it.get("url") not in recent_urls]


def _score(it: dict) -> float:
    """1차 점수 = confidence (없으면 0.5).
    추후 personal_fit, tag_freshness 곱셈 추가 예정."""
    conf = it.get("confidence")
    if not conf:
        return 0.5
    try:
        return float(conf)
    except (TypeError, ValueError):
        return 0.5


def _pick_highlights(
    items: list[dict], recent_urls: set[str], top_n: int = 3
) -> list[dict]:
    """필터 + 점수 정렬 + 다양성 가드 적용해 top_n 개 반환.

    다양성 가드: 후보 중 점수 상위 top_n 을 단순 추출했을 때
    모두 같은 카테고리면, 차순위에서 다른 카테고리 1개를 끌어와 교체.
    교체 후보가 없으면 그대로 둠 (억지 다양화 X).
    """
    filtered = _filter_recent(items, recent_urls)
    ranked = sorted(filtered, key=_score, reverse=True)
    picks = ranked[:top_n]

    if len(picks) < top_n or top_n < 2:
        return picks

    categories = {p.get("category") for p in picks}
    if len(categories) > 1:
        return picks

    only_cat = next(iter(categories))
    for cand in ranked[top_n:]:
        if cand.get("category") != only_cat:
            return picks[:-1] + [cand]
    return picks


def _build_user_for_date(
    target: dt.date,
    items: list[dict],
    personal_context: str,
    recent_highlights: list[dict] | None = None,
) -> str:
    lines = [
        f"오늘 날짜: {target.isoformat()}",
        "",
        "[개인화 컨텍스트]",
        personal_context,
        "",
        "[최근 7일 추천 내역 — 재추천 금지]",
    ]
    if not recent_highlights:
        lines.append("(없음)")
    else:
        # 카테고리 분포 요약 (다양성 가드 근거)
        cat_counts: dict[str, int] = {}
        for h in recent_highlights:
            c = h.get("category") or "-"
            cat_counts[c] = cat_counts.get(c, 0) + 1
        dist = ", ".join(f"{c}×{n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))
        lines.append(f"카테고리 분포: {dist}")
        lines.append("")
        for h in recent_highlights:
            lines.append(
                f"- [{h['date']}] ({h.get('category') or '-'}) {h['title']} — {h['url']}"
            )

    lines.extend([
        "",
        "[어제~그제 수집 아이템]",
    ])
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
        "[최근 7일 추천 내역]에 있는 URL은 하이라이트/실험 어디에도 다시 등장시키지 말고,",
        "카테고리 분포가 한쪽으로 쏠려 있다면 오늘 하이라이트 3개는 서로 다른 카테고리로 다양화하세요.",
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
    recent = _recent_brief_highlights(target, days=7)
    log.info(
        "[%s] 어제~그제 아이템 %d건 · 최근 7일 하이라이트 %d건",
        target.isoformat(), len(items), len(recent),
    )

    if dry_run:
        print(f"\n===== {target.isoformat()} =====")
        print(
            f"[dry-run] Sonnet 호출 스킵 · items={len(items)}건 · "
            f"recent_highlights={len(recent)}건 · out={out_path.name}"
        )
        # dry-run 시 프롬프트 user 메시지 미리보기 (검증용)
        preview = _build_user_for_date(target, items, _load_personal_context(), recent)
        print("----- user prompt preview -----")
        print(preview[:2000])
        print("----- end preview -----")
        return True

    personal_context = _load_personal_context()
    system = _load_prompt()
    user = _build_user_for_date(target, items, personal_context, recent)

    try:
        result = claude.call_sonnet(system=system, user=user, max_tokens=3500)
        content = result.text.strip()
    except claude.TokenCapExceeded as e:
        log.warning("토큰 캡: %s", e)
        content = _fallback_brief_for(target, f"토큰 캡 — {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("브리프 생성 실패")
        content = _fallback_brief_for(target, str(e))

    if not content:
        # LLM 이 예외 없이 빈 응답을 돌려주는 케이스 방어 (2026-04-24 빈 파일 사건).
        # TokenCapExceeded 와 동일하게 fallback 으로 치환 — 빈 파일이 덮어쓰는 것보다
        # 명시적 실패 문구가 디버깅·사용자 인지에 유리.
        log.warning(
            "LLM 빈 응답 — fallback 치환 target=%s items=%d user_prompt_len=%d",
            target.isoformat(), len(items), len(user),
        )
        content = _fallback_brief_for(target, "LLM empty response")

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
