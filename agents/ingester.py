#!/usr/bin/env python3
"""Ingester — 수집기 (LLM 0).

launchd 5분 주기. inbox.md를 읽어 frontmatter 블록별로 파싱 → URL 종류에 따라 본문 추출 →
raw/YYYY-MM-DD/<id>.json 저장 → 처리한 블록만 inbox.md에서 제거.

실패한 블록은 inbox-failed.md로 이관.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
from pathlib import Path

import _bootstrap

_bootstrap.setup(__file__)

from lib import paths  # noqa: E402
from lib.wiki_io import (  # noqa: E402
    WikiItem,
    add_raw_stub,
    index_has_url,
    save_raw,
    url_hash,
)
from lib.validate import _infer_source  # noqa: E402
from lib import github_inbox  # noqa: E402
from lib import fetchers  # noqa: E402

log = logging.getLogger("ingester")


# ─────────────────────────────────────────────────────────────
# inbox.md 파싱
# ─────────────────────────────────────────────────────────────
FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<body>.*?)\n---\s*\n?",
    re.MULTILINE | re.DOTALL,
)


def parse_inbox_blocks(text: str) -> list[dict]:
    """각 '--- ... ---' 블록을 YAML-ish dict로 파싱. 간단 파서."""
    blocks: list[dict] = []
    for m in FRONTMATTER_RE.finditer(text):
        raw = m.group("body")
        d: dict = {"_raw": m.group(0)}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            d[k.strip()] = v.strip()
        blocks.append(d)
    return blocks


# ─────────────────────────────────────────────────────────────
# URL별 본문 추출 — lib/fetchers 로 분리됨
# ─────────────────────────────────────────────────────────────
def extract_content(url: str, source: str) -> dict:
    """fetchers.dispatch 호출 후 기존 dict shape 으로 변환.

    status 기반 분기는 Task 5에서 도입. 지금은 기존 {title, text, error, ...} 형태를
    그대로 유지해서 호출부를 깨지 않는다.
    """
    result = fetchers.dispatch(url, source)
    out: dict = {
        "title": result.title,
        "text": result.text,
        **result.metadata,
    }
    if result.error:
        out["error"] = result.error
    return out


# ─────────────────────────────────────────────────────────────
# 실행 본체
# ─────────────────────────────────────────────────────────────
def run(dry_run: bool = False) -> None:
    mode = paths.inbox_mode()
    log.info("inbox mode: %s", mode)
    if mode == "issues":
        _run_issues_mode(dry_run=dry_run)
    else:
        _run_file_mode(dry_run=dry_run)


def _run_issues_mode(dry_run: bool) -> None:
    """GitHub Issues를 inbox로 사용 (Actions/서버 환경)."""
    try:
        issues = github_inbox.list_open_inbox_issues()
    except Exception:
        log.exception("GitHub inbox 읽기 실패")
        return

    if not issues:
        log.info("열린 inbox 이슈 없음.")
        return

    log.info("inbox 이슈 %d건 감지.", len(issues))
    for issue in issues:
        url = issue.url
        if not url.startswith("http"):
            log.warning("이슈 #%d title이 URL 아님: %r", issue.number, url)
            if not dry_run:
                github_inbox.label_issue_failed(issue.number, "title이 URL 아님")
            continue

        item_id = url_hash(url)
        if index_has_url(url):
            log.info("중복 건너뜀 #%d id=%s", issue.number, item_id)
            if not dry_run:
                github_inbox.close_issue(issue.number, "⏭ 이미 위키에 존재 — 건너뜀")
            continue

        source = _infer_source(url)
        captured_at = issue.created_at

        extracted = extract_content(url, source)
        item = WikiItem(
            id=item_id,
            url=url,
            source=source,
            captured_at=captured_at,
            title=extracted.get("title", "") or "",
            body=extracted.get("text", "") or "",
        )

        if dry_run:
            log.info("[dry-run] #%d id=%s title=%s", issue.number, item.id, item.title[:40])
            continue

        if extracted.get("error"):
            log.warning("추출 실패 #%d: %s", issue.number, extracted["error"])
            github_inbox.label_issue_failed(issue.number, extracted["error"])
            continue

        save_raw(item, extracted)
        add_raw_stub(item)
        github_inbox.close_issue(
            issue.number,
            f"✅ 수집 완료 — id={item.id}, source={item.source}",
        )
        log.info("raw 저장 + 이슈 close: #%d → id=%s", issue.number, item.id)


def _run_file_mode(dry_run: bool) -> None:
    """로컬 테스트용 — wiki repo 내 inbox.md 파일을 사용."""
    inbox = paths.INBOX_MD
    if not inbox.exists():
        log.info("inbox.md 없음: %s", inbox)
        return

    text = inbox.read_text(encoding="utf-8")
    blocks = parse_inbox_blocks(text)
    if not blocks:
        log.info("새 블록 없음.")
        return

    log.info("inbox 블록 %d개 감지.", len(blocks))
    processed_raws: list[str] = []
    failed_raws: list[str] = []

    for b in blocks:
        url = b.get("url", "").strip()
        if not url:
            log.warning("url 없는 블록 스킵.")
            failed_raws.append(b["_raw"])
            continue

        item_id = url_hash(url)
        if index_has_url(url):
            log.info("중복 건너뜀 id=%s url=%s", item_id, url)
            processed_raws.append(b["_raw"])
            continue

        source_hint = b.get("source_hint", "")
        source = _infer_source(url) if not source_hint else {
            "twitter": "X", "x": "X",
            "youtube": "YouTube",
            "threads": "Threads",
            "instagram": "Instagram",
        }.get(source_hint.lower(), _infer_source(url))

        captured_at = b.get("captured_at") or dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        )

        extracted = extract_content(url, source)
        item = WikiItem(
            id=item_id,
            url=url,
            source=source,
            captured_at=captured_at,
            title=extracted.get("title", "") or "",
            author="",
            body=extracted.get("text", "") or "",
        )

        if dry_run:
            log.info("[dry-run] 저장 스킵 id=%s title=%s", item.id, item.title[:40])
            processed_raws.append(b["_raw"])
            continue

        save_raw(item, extracted)
        add_raw_stub(item)
        processed_raws.append(b["_raw"])
        log.info("raw 저장 id=%s source=%s", item.id, item.source)

    if dry_run:
        log.info("[dry-run] inbox.md 비우기 스킵.")
        return

    # 처리된 블록만 제거, 실패는 inbox-failed.md로
    remaining = text
    for raw in processed_raws + failed_raws:
        remaining = remaining.replace(raw, "", 1)
    inbox.write_text(remaining.lstrip("\n"), encoding="utf-8")

    if failed_raws:
        with paths.INBOX_FAILED_MD.open("a", encoding="utf-8") as f:
            for raw in failed_raws:
                f.write(raw + "\n")

    log.info("완료: 처리=%d, 실패=%d", len(processed_raws), len(failed_raws))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
