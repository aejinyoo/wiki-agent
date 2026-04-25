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
from urllib.parse import urlparse

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


# ─────────────────────────────────────────────────────────────
# 마크다운 조립 (섹션별 Python 렌더링)
# ─────────────────────────────────────────────────────────────

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

_DIFF_EASY_KW = ("방문", "확인", "저장", "계정", "체험", "구독")
_DIFF_HARD_KW = ("구축", "제작", "개발", "풀스택", "파이프라인")


def _classify_difficulty(what_to_try: str) -> str:
    """⭐/⭐⭐/⭐⭐⭐ 분류 (휴리스틱)."""
    s = (what_to_try or "").strip()
    if any(kw in s for kw in _DIFF_HARD_KW) or len(s) > 200:
        return "⭐⭐⭐"
    if any(kw in s for kw in _DIFF_EASY_KW) or len(s) < 60:
        return "⭐"
    return "⭐⭐"


def _difficulty_eta(stars: str) -> str:
    return {"⭐": "30m", "⭐⭐": "1h", "⭐⭐⭐": "3h+"}.get(stars, "1h")


def _source_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _render_header(target: dt.date) -> str:
    return f"# Daily Design Brief — {target.isoformat()} ({_WEEKDAY_KO[target.weekday()]})"


def _render_highlights(picks: list[dict]) -> str:
    head = "## 📌 하이라이트 (어제 수집분)"
    if not picks:
        return f"{head}\n\n(어제 수집분 없음)"

    blocks: list[str] = [head]
    for i, p in enumerate(picks, 1):
        title = p.get("title", "") or "(제목 없음)"
        url = p.get("url", "") or ""
        category = p.get("category", "-") or "-"
        host = _source_host(url) or "-"
        tags = (p.get("tags") or [])[:2]
        tag_str = " ".join(f"`{t}`" for t in tags)
        meta = f"- **{category}** · **{host}**"
        if tag_str:
            meta += f" · {tag_str}"
        why = (p.get("why_it_matters") or "").strip()
        what = (p.get("what_to_try") or "").strip()
        card = [
            f"### {i}. [{title}]({url})",
            meta,
            f"- 왜 봐야 하나: {why}" if why else "- 왜 봐야 하나: -",
            f"- 해볼 것: {what}" if what else "- 해볼 것: -",
        ]
        blocks.append("\n".join(card))
    return "\n\n".join(blocks)


def _render_experiments(picks: list[dict]) -> str:
    head = "## 🧪 오늘 해볼 만한 실험 (Top 3)"
    rows: list[tuple[str, str, str, str]] = []
    for p in picks:
        what = (p.get("what_to_try") or "").strip()
        if not what:
            continue
        stars = _classify_difficulty(what)
        rows.append((stars, _difficulty_eta(stars), p.get("title", "") or "-", what))

    if not rows:
        return f"{head}\n\n(실험 후보 없음)"

    # ⭐ → ⭐⭐⭐ 오름차순 (문자열 길이로 정렬)
    rows.sort(key=lambda r: len(r[0]))

    lines = [
        head,
        "",
        "| 난이도 | ETA | 제목 | 해볼 것 |",
        "|---|---|---|---|",
    ]
    for stars, eta, title, what in rows:
        safe_what = what.replace("|", "\\|").replace("\n", " ")
        safe_title = title.replace("|", "\\|")
        lines.append(f"| {stars} | {eta} | {safe_title} | {safe_what} |")
    return "\n".join(lines)


def _render_wiki_changes() -> str:
    return "## 🧭 이번 주 위키 변화\n\n(변화 없음)"


def _recent_highlight_urls(target: dt.date, days: int = 7) -> set[str]:
    """최근 `days` 일 브리프 📌 하이라이트의 URL 집합 (재추천 방지용)."""
    return {
        h["url"]
        for h in _recent_brief_highlights(target, days=days)
        if h.get("url")
    }


def _build_summary_user(
    target: dt.date, picks: list[dict], personal_context: str
) -> str:
    """🔥 3줄 전용 user 프롬프트. 선별 끝난 picks 만 넘김."""
    lines = [
        f"오늘 날짜: {target.isoformat()}",
        "",
        "[개인화 컨텍스트]",
        personal_context,
        "",
        "[어제 수집 핵심 아이템]",
    ]
    if not picks:
        lines.append("(없음)")
    else:
        for i, p in enumerate(picks, 1):
            lines.append(f"{i}. {p.get('title', '')}")
            summary = (p.get("summary") or "").strip()[:200]
            if summary:
                lines.append(f"   - {summary}")
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
    """target 날짜의 브리프 1개 생성. 이미 있으면 skip(또는 force).

    LLM(Sonnet) 은 🔥 3줄만 호출. 📌/🧪/🧭 는 Python 으로 조립해 부분 실패에도 살아남음.
    """
    out_path = paths.DAILY_DIR / f"{target.isoformat()}.md"
    if out_path.exists() and not force:
        log.info("스킵 (이미 존재): %s", out_path.name)
        return False

    items = _items_for(target)
    recent_urls = _recent_highlight_urls(target, days=7)
    picks = _pick_highlights(items, recent_urls, top_n=3)
    log.info(
        "[%s] items=%d recent=%d picks=%d",
        target.isoformat(), len(items), len(recent_urls), len(picks),
    )

    if dry_run:
        personal_context = _load_personal_context()
        preview = _build_summary_user(target, picks, personal_context)
        print(f"\n===== {target.isoformat()} =====")
        print(
            f"[dry-run] Sonnet 호출 스킵 · items={len(items)}건 · "
            f"recent_urls={len(recent_urls)}건 · picks={len(picks)}건 · out={out_path.name}"
        )
        print("----- picks preview -----")
        for i, p in enumerate(picks, 1):
            print(f"  {i}. [{p.get('category', '-')}] {p.get('title', '')} — {p.get('url', '')}")
        print("----- user prompt preview -----")
        print(preview[:2000])
        print("----- end preview -----")
        return True

    personal_context = _load_personal_context()
    system = _load_prompt()
    user = _build_summary_user(target, picks, personal_context)

    three_lines = ""
    try:
        result = claude.call_sonnet(system=system, user=user, max_tokens=600)
        three_lines = result.text.strip()
    except claude.TokenCapExceeded as e:
        log.warning("토큰 캡: %s", e)
        three_lines = f"- (오늘의 3줄 생성 실패: 토큰 캡 — {e})"
    except Exception:  # noqa: BLE001
        log.exception("3줄 생성 실패")
        three_lines = "- (오늘의 3줄 생성 실패)"

    if not three_lines:
        log.warning(
            "LLM 빈 응답 — 3줄 fallback target=%s items=%d picks=%d user_prompt_len=%d",
            target.isoformat(), len(items), len(picks), len(user),
        )
        three_lines = "- (오늘의 3줄 생성 실패: LLM empty response)"

    brief = "\n\n".join([
        _render_header(target),
        "## 🔥 오늘의 3줄\n" + three_lines,
        _render_highlights(picks),
        _render_experiments(picks),
        _render_wiki_changes(),
    ]) + "\n"

    out_path.write_text(brief, encoding="utf-8")
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
