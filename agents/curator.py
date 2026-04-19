#!/usr/bin/env python3
"""Curator — 큐레이터 (주 1회 Sonnet).

launchd 일요일 23:00. 아이템 ≥ 50 때만 돌림.
기획서 5.5 — 태그 정규화, 중복 병합, 재분류, 카테고리 신설/병합.
가드레일:
  - _meta.yaml의 protected 카테고리 존중
  - 영향 > 100건이면 자동반영 skip, 브리프 승인 요청
  - 같은 카테고리 2주 내 재변경 금지 (cooldown)

이 스크립트는 "뼈대"입니다 — 실제 변경 로직은 W4에서 프롬프트·반영기와 함께 완성.
지금은 (1) 조건 체크 (2) 통계 재계산 (3) 개인화 컨텍스트 재생성 까지만 안전하게 수행.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

import _bootstrap

_bootstrap.setup(__file__)

from lib import paths  # noqa: E402
from lib.wiki_io import iter_wiki_items, load_meta, recompute_stats  # noqa: E402

log = logging.getLogger("curator")

MIN_ITEMS_TO_RUN = 50
MIN_DAYS_BETWEEN_RUNS = 7  # 7일 이내 재실행 방지


def _last_run_date() -> dt.date | None:
    """_changelog/YYYY-MM-DD.md 중 가장 최근 날짜."""
    if not paths.CHANGELOG_DIR.exists():
        return None
    dates = []
    for p in paths.CHANGELOG_DIR.glob("*.md"):
        try:
            dates.append(dt.date.fromisoformat(p.stem))
        except ValueError:
            continue
    return max(dates) if dates else None


def _mark_run_today(note: str = "v1 — stats + personal_context 재생성") -> None:
    today = dt.date.today().isoformat()
    out = paths.CHANGELOG_DIR / f"{today}.md"
    out.write_text(f"# Curator run — {today}\n\n{note}\n", encoding="utf-8")


def _count_items() -> int:
    return sum(1 for _ in iter_wiki_items())


def _regenerate_personal_context() -> None:
    """기획서 5.4 — 카테고리 분포·태그·tried 시그널 요약 (최대 300토큰 근사).

    v1은 Sonnet 호출 없이 통계 기반으로만 생성. 나중에 Sonnet으로 업그레이드.
    """
    stats = recompute_stats()
    cats = stats.get("categories", {})
    tags = stats.get("tags", {})

    top_cats = sorted(cats.items(), key=lambda x: -x[1])[:5]
    top_tags = sorted(tags.items(), key=lambda x: -x[1])[:10]

    # tried=true 최근 5건
    recent_tried: list[str] = []
    for path, post in iter_wiki_items():
        if post.get("tried") is True:
            recent_tried.append(f"- {post.get('title', path.stem)} · {post.get('category', '-')}")
            if len(recent_tried) >= 5:
                break

    cat_lines = [f"- {name}: {count}" for name, count in top_cats] or ["- (없음)"]
    tag_lines = [f"- {name}: {count}" for name, count in top_tags] or ["- (없음)"]
    tried_lines = recent_tried or ["- (없음)"]

    md = [
        "# Personal Context",
        f"_자동 생성 · {dt.datetime.utcnow().isoformat(timespec='seconds')}Z_",
        "",
        "## 카테고리 분포 Top 5",
        *cat_lines,
        "",
        "## 자주 쓰는 태그 Top 10",
        *tag_lines,
        "",
        "## 최근 tried=true 아이템",
        *tried_lines,
    ]
    paths.PERSONAL_CONTEXT.write_text("\n".join(md) + "\n", encoding="utf-8")
    log.info("_personal_context.md 갱신")


def is_due(force: bool = False) -> bool:
    """실행 조건: 아이템 ≥ 50 AND 마지막 실행 ≥ 7일 전."""
    if force:
        return True
    if _count_items() < MIN_ITEMS_TO_RUN:
        return False
    last = _last_run_date()
    if last is None:
        return True
    return (dt.date.today() - last).days >= MIN_DAYS_BETWEEN_RUNS


def run(force: bool = False) -> None:
    meta = load_meta()
    protected = set((meta.get("protected") or []))
    log.info("protected 카테고리: %s", sorted(protected))

    count = _count_items()
    last = _last_run_date()
    log.info("현재 위키 아이템 %d건 / 마지막 Curator: %s", count, last)

    if not is_due(force=force):
        log.info("Curator 조건 불충족 — 스킵 (item>=%d & 7일↑ 간격, force로 우회 가능)",
                 MIN_ITEMS_TO_RUN)
        return

    # v1: 통계 + 개인화 컨텍스트만 갱신
    _regenerate_personal_context()
    _mark_run_today()

    # v2 TODO:
    #   1) Sonnet에 태그·카테고리 스냅샷 전달 → 정규화/병합/분할 제안 받기
    #   2) 가드레일 통과한 것만 실제 파일 이동·rename 반영
    #   3) _changelog/YYYY-MM-DD.md 상세 로그
    #   4) Git 커밋 훅 (nightly 후처리에서)
    log.info("v1 Curator 완료. 태그/카테고리 자동 재정비는 W4에서 활성화.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="50건 미만이어도 강제 실행")
    args = ap.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
