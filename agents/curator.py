#!/usr/bin/env python3
"""Curator — 큐레이터 (주 1회 Sonnet).

launchd 일요일 23:00. 아이템 ≥ 50 때만 돌림.
기획서 5.5 — 태그 정규화, 중복 병합, 재분류, 카테고리 신설/병합.
가드레일:
  - _meta.yaml의 protected 카테고리 존중
  - 영향 > 100건이면 자동반영 skip, 브리프 승인 요청
  - 같은 카테고리 2주 내 재변경 금지 (cooldown)

v2 (현재): LLM dry-run. 통계/개인화 컨텍스트 재생성 + Gemini Pro 호출로
태그·카테고리 정리 제안을 받아 `_changelog/YYYY-MM-DD.md` 보고서로 기록.
실제 파일 이동·rename 은 미구현 (1~2주 dry-run 검증 후 별도 PR 에서 활성화).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
from typing import Any

import _bootstrap

_bootstrap.setup(__file__)

from lib import paths  # noqa: E402
from lib.wiki_io import iter_wiki_items, load_meta, recompute_stats  # noqa: E402

log = logging.getLogger("curator")

MIN_ITEMS_TO_RUN = 50
MIN_DAYS_BETWEEN_RUNS = 7  # 7일 이내 재실행 방지

LLM_MAX_TOKENS = 6144
LLM_TEMPERATURE = 0.2
SAMPLE_PER_CATEGORY = 10
TOP_TAGS_IN_PROMPT = 50


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


# ─────────────────────────────────────────────────────────────
# v2: LLM dry-run 보조 함수
# ─────────────────────────────────────────────────────────────

_PROPOSAL_KINDS = (
    "tag_renames",
    "duplicate_merges",
    "reclassifications",
    "new_categories",
    "category_changes",
)


def _collect_snapshot() -> dict:
    """위키 전체를 LLM 프롬프트용으로 패킹. items / categories / tags 빈도."""
    items: list[dict] = []
    for path, post in iter_wiki_items():
        items.append({
            "id": post.get("id") or path.stem,
            "title": post.get("title") or path.stem,
            "category": post.get("category") or path.parent.name,
            "tags": list(post.get("tags") or []),
            "url": post.get("url") or "",
            "captured_at": post.get("captured_at") or "",
        })
    categories: dict[str, int] = {}
    tags: dict[str, int] = {}
    for it in items:
        categories[it["category"]] = categories.get(it["category"], 0) + 1
        for t in it["tags"]:
            tags[t] = tags.get(t, 0) + 1
    return {"items": items, "categories": categories, "tags": tags}


def _build_user_prompt(snapshot: dict, protected: set[str], meta: dict) -> str:
    cats_lines = [
        f"- {c}: {n}건"
        for c, n in sorted(snapshot["categories"].items(), key=lambda x: -x[1])
    ]
    tag_items = sorted(snapshot["tags"].items(), key=lambda x: -x[1])[:TOP_TAGS_IN_PROMPT]
    tag_lines = [f"- {t}: {n}건" for t, n in tag_items] or ["- (없음)"]

    by_cat: dict[str, list[dict]] = {}
    for it in snapshot["items"]:
        by_cat.setdefault(it["category"], []).append(it)
    sample_lines: list[str] = []
    for cat in sorted(by_cat.keys()):
        lst = sorted(by_cat[cat], key=lambda x: x.get("captured_at", ""), reverse=True)
        for it in lst[:SAMPLE_PER_CATEGORY]:
            sample_lines.append(
                f'- [{cat}] id={it["id"]} · "{it["title"]}" · 태그={it["tags"]}'
            )

    cur = meta.get("curator") or {}
    weekly = cur.get("weekly_limits") or {}
    guards = (
        f"- autofix_impact_limit: {cur.get('autofix_impact_limit', 100)}\n"
        f"- cooldown_days: {cur.get('cooldown_days', 14)}\n"
        f"- weekly_limits.new_category: {weekly.get('new_category', 1)}\n"
        f"- weekly_limits.split_merge_delete: {weekly.get('split_merge_delete', 2)}"
    )

    return (
        "# 카테고리별 아이템 수\n" + "\n".join(cats_lines) + "\n\n"
        f"# 태그 빈도 (Top {TOP_TAGS_IN_PROMPT})\n" + "\n".join(tag_lines) + "\n\n"
        f"# 아이템 샘플 (카테고리별 최근 {SAMPLE_PER_CATEGORY}건)\n"
        + "\n".join(sample_lines) + "\n\n"
        f"# protected 카테고리\n{sorted(protected) if protected else '(없음)'}\n\n"
        f"# 가드레일\n{guards}\n"
    )


def _parse_proposal(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 한 덩어리 추출. ```json 펜스/앞뒤 텍스트 모두 허용."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("LLM 응답에서 JSON 블록을 찾지 못함")
    return json.loads(m.group(0))


def _as_str_list(v) -> list[str]:
    """LLM 응답의 필드를 강건하게 문자열 리스트로 정규화.

    프롬프트는 단일 문자열을 요구하지만 모델이 ['a', 'b'] 처럼 리스트로 주거나
    None 으로 비울 수 있어 둘 다 흡수.
    """
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)]


def _compute_impact(kind: str, change: dict, snapshot: dict) -> int:
    items = snapshot["items"]
    if kind == "tag_renames":
        from_tag = change.get("from")
        return sum(1 for it in items if from_tag in (it.get("tags") or []))
    if kind == "reclassifications":
        return 1
    if kind == "duplicate_merges":
        return len(change.get("remove") or [])
    if kind == "new_categories":
        return len(change.get("seed_items") or [])
    if kind == "category_changes":
        targets = set(_as_str_list(change.get("target")))
        return sum(1 for it in items if it.get("category") in targets)
    return 0


def _affected_categories(kind: str, change: dict) -> set[str]:
    if kind == "reclassifications":
        return set(_as_str_list(change.get("from"))) | set(_as_str_list(change.get("to")))
    if kind == "category_changes":
        return set(_as_str_list(change.get("target"))) | set(_as_str_list(change.get("to")))
    return set()


def _compute_category_last_change() -> dict[str, dt.date]:
    """_changelog/YYYY-MM-DD.md 들 중 'applied-to: cat1, cat2' 라인 파싱.

    dry-run 보고서는 변경이 0건이라 어떤 카테고리도 last_change 에 안 잡힘.
    auto-apply 활성화 후에는 실제 변경한 카테고리만 보고서에 `**applied-to**: ...`
    형식으로 적으면 자동으로 cooldown 추적됨.
    """
    out: dict[str, dt.date] = {}
    if not paths.CHANGELOG_DIR.exists():
        return out
    pattern = re.compile(r"^\*\*applied-to\*\*:\s*(.+)$", re.MULTILINE)
    for p in paths.CHANGELOG_DIR.glob("*.md"):
        try:
            date = dt.date.fromisoformat(p.stem)
        except ValueError:
            continue
        text = p.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            for cat in [c.strip() for c in m.group(1).split(",")]:
                if not cat:
                    continue
                cur = out.get(cat)
                if cur is None or date > cur:
                    out[cat] = date
    return out


def _evaluate_proposal(
    proposal: dict,
    snapshot: dict,
    protected: set[str],
    meta: dict,
    last_change: dict[str, dt.date],
    today: dt.date | None = None,
) -> dict[str, Any]:
    """가드레일 적용: protected / 영향>impact_limit / cooldown.

    제안은 4개 통(would_apply 카테고리·신설·재분류·태그·중복) 으로 분류되거나
    `approval_required` 또는 `skipped` 로 빠짐. 출력 스키마는 보고서 렌더에 그대로 사용.
    """
    cur = meta.get("curator") or {}
    impact_limit = cur.get("autofix_impact_limit", 100)
    cooldown_days = cur.get("cooldown_days", 14)
    today = today or dt.date.today()

    out: dict[str, Any] = {k: [] for k in _PROPOSAL_KINDS}
    out["approval_required"] = list(proposal.get("approval_required") or [])
    out["skipped"] = []
    out["summary"] = proposal.get("summary") or ""

    for kind in _PROPOSAL_KINDS:
        for ch in proposal.get(kind) or []:
            impact = _compute_impact(kind, ch, snapshot)
            enriched = {**ch, "_impact": impact}
            affected = _affected_categories(kind, ch)

            if affected & protected:
                out["skipped"].append({
                    "kind": kind,
                    "change": ch,
                    "reason": f"protected 카테고리 포함: {sorted(affected & protected)}",
                })
                continue

            if impact > impact_limit:
                out["approval_required"].append({
                    "kind": kind,
                    "change": ch,
                    "impact": impact,
                    "reason": f"영향 {impact}건 > {impact_limit} 자동반영 한도",
                })
                continue

            cooldown_hit: tuple[str, int] | None = None
            for cat in affected:
                if cat in last_change:
                    days = (today - last_change[cat]).days
                    if days < cooldown_days:
                        cooldown_hit = (cat, days)
                        break
            if cooldown_hit:
                cat, days = cooldown_hit
                out["skipped"].append({
                    "kind": kind,
                    "change": ch,
                    "reason": f"카테고리 '{cat}' cooldown ({days}일 전 변경, {cooldown_days}일 미만)",
                })
                continue

            out[kind].append(enriched)

    return out


def _render_dry_run_report(evaluated: dict, snapshot: dict, llm_meta: dict) -> str:
    today = dt.date.today().isoformat()
    cats_sorted = sorted(snapshot["categories"].items(), key=lambda x: -x[1])
    cats_inline = ", ".join(f"{c}={n}" for c, n in cats_sorted)

    lines: list[str] = [
        f"# Curator dry-run — {today}",
        "",
        "**모드**: dry-run (auto-apply 비활성, 제안만 기록)",
        f"**LLM**: {llm_meta.get('model')} · 입력 {llm_meta.get('input_tokens')}t · 출력 {llm_meta.get('output_tokens')}t",
        f"**위키 스냅샷**: {len(snapshot['items'])}개 아이템 · 카테고리 [{cats_inline}]",
        "",
        "## 한 줄 요약",
        evaluated.get("summary") or "(없음)",
        "",
    ]

    def emit(title: str, kind: str, fmt) -> None:
        lst = evaluated.get(kind) or []
        lines.append(f"## {title} ({len(lst)}건)")
        if not lst:
            lines.append("(없음)")
        else:
            lines.extend(fmt(ch) for ch in lst)
        lines.append("")

    emit(
        "태그 정규화 (Phase 1 — auto-apply 활성 예정)",
        "tag_renames",
        lambda c: f"- `{c.get('from')}` → `{c.get('to')}` (영향 {c.get('_impact', '?')}건): {c.get('reason', '')}",
    )
    emit(
        "중복 병합",
        "duplicate_merges",
        lambda c: f"- keep `{c.get('keep')}` / remove {c.get('remove')}: {c.get('reason', '')}",
    )
    emit(
        "재분류 (Phase 2 — auto-apply 활성 예정)",
        "reclassifications",
        lambda c: f"- `{c.get('item_id')}`: `{c.get('from')}` → `{c.get('to')}` ({c.get('reason', '')})",
    )
    emit(
        "신설 카테고리 제안 (Phase 3 — auto-apply 비활성)",
        "new_categories",
        lambda c: f"- `{c.get('name')}` (seed {len(c.get('seed_items') or [])}건): {c.get('reason', '')}",
    )
    emit(
        "카테고리 변경 제안 (Phase 3 — auto-apply 비활성)",
        "category_changes",
        lambda c: f"- {c.get('op')} `{c.get('target')}` → `{c.get('to')}` (영향 {c.get('_impact', '?')}건): {c.get('reason', '')}",
    )

    ar = evaluated.get("approval_required") or []
    lines.append(f"## 승인 필요 ({len(ar)}건)")
    if not ar:
        lines.append("(없음)")
    else:
        for item in ar:
            lines.append(f"- {item}")
    lines.append("")

    sk = evaluated.get("skipped") or []
    lines.append(f"## 스킵 ({len(sk)}건)")
    if not sk:
        lines.append("(없음)")
    else:
        for item in sk:
            lines.append(f"- [{item.get('kind')}] {item.get('reason')}: {item.get('change')}")
    lines.append("")

    return "\n".join(lines) + "\n"


def _write_dry_run_report(text: str) -> paths.Path:
    today = dt.date.today().isoformat()
    out = paths.CHANGELOG_DIR / f"{today}.md"
    out.write_text(text, encoding="utf-8")
    return out


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
    protected = set(meta.get("protected") or [])
    log.info("protected 카테고리: %s", sorted(protected))

    count = _count_items()
    last = _last_run_date()
    log.info("현재 위키 아이템 %d건 / 마지막 Curator: %s", count, last)

    if not is_due(force=force):
        log.info("Curator 조건 불충족 — 스킵 (item>=%d & 7일↑ 간격, force로 우회 가능)",
                 MIN_ITEMS_TO_RUN)
        return

    # v1 동작: 통계 + 개인화 컨텍스트 (LLM 실패해도 유지)
    _regenerate_personal_context()

    # v2 dry-run: LLM 제안 → _changelog/YYYY-MM-DD.md 보고서
    try:
        from lib import llm  # 지연 import (테스트에서 mock 용이)

        snapshot = _collect_snapshot()
        system_prompt = (paths.PROMPTS_DIR / "curator.md").read_text(encoding="utf-8")
        user_msg = _build_user_prompt(snapshot, protected, meta)
        log.info("LLM 호출 — user prompt %d chars / items %d", len(user_msg), len(snapshot["items"]))

        result = llm.call_sonnet(
            system=system_prompt,
            user=user_msg,
            max_tokens=LLM_MAX_TOKENS,
            temperature=LLM_TEMPERATURE,
        )
        proposal = _parse_proposal(result.text)
        last_change = _compute_category_last_change()
        evaluated = _evaluate_proposal(proposal, snapshot, protected, meta, last_change)
        llm_meta = {
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        report = _render_dry_run_report(evaluated, snapshot, llm_meta)
        out = _write_dry_run_report(report)
        log.info("Curator dry-run 보고서 작성: %s", out)
    except Exception:  # noqa: BLE001
        log.exception("Curator v2 dry-run 실패 — fallback 마커만 작성")
        _mark_run_today(note="v2 dry-run 실패. v1 stats + personal_context 만 갱신됨. 로그 확인 필요.")
        return

    log.info("v2 dry-run 완료. auto-apply 활성화는 별도 PR (curator-v2 docs T 참조).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="50건 미만이어도 강제 실행")
    args = ap.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
