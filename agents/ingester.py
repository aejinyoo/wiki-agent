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
from lib.user_caption import validate_user_caption  # noqa: E402
from lib import github_inbox  # noqa: E402
from lib import fetchers  # noqa: E402
from lib.fetchers import FetchResult  # noqa: E402


# status 가 이 집합에 속하면 raw 로 저장한다. no_transcript 는 description 폴백이
# 있어 분류 신호가 남으므로 저장 루트로 태운다.
_SAVE_STATUSES = frozenset({"ok", "no_transcript"})

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
def _result_to_extracted(result: FetchResult) -> dict:
    """FetchResult 를 classifier 가 읽는 extracted dict 로 변환.

    status / error 는 제외 (status 는 save_raw 인자로, error 는 저장 시점에 기록 불필요).
    """
    return {
        "title": result.title,
        "text": result.text,
        **result.metadata,
    }


def _fail_reason(result: FetchResult) -> str:
    """failed / login_required 상태의 이슈 라벨링용 사유 메시지."""
    if result.status == "login_required":
        return result.error or "login required"
    return result.error or f"fetch status: {result.status}"


def _is_empty_payload(extracted: dict) -> bool:
    """status 가 저장-허용이어도 title/text/user_caption 모두 빈 경우를 감지.

    이런 payload 가 저장 루트에 오르면 classifier 가 URL+빈 본문만으로
    환각을 생성한다 (2026-04-23 오염 사건 재현). 저장 거부하고 failed 루트로
    이관하는 것이 안전하다.
    """
    title = (extracted.get("title") or "").strip()
    text = (extracted.get("text") or "").strip()
    caption = (extracted.get("user_caption") or "").strip()
    return not (title or text or caption)


def _empty_payload_reason(result: FetchResult) -> str:
    """빈 payload 저장 거부 시 이슈 라벨 / inbox-failed 에 기록할 사유."""
    base = f"empty payload (status={result.status})"
    if result.error:
        return f"{base} — fetcher_error: {result.error}"
    return base


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

        result = fetchers.dispatch(url, source)
        extracted = _result_to_extracted(result)
        caption = validate_user_caption(issue.user_caption)
        if caption:
            extracted["user_caption"] = caption
        item = WikiItem(
            id=item_id,
            url=url,
            source=source,
            captured_at=captured_at,
            title=extracted.get("title", "") or "",
            body=extracted.get("text", "") or "",
        )

        if dry_run:
            log.info(
                "[dry-run] #%d id=%s status=%s title=%s",
                issue.number, item.id, result.status, item.title[:40],
            )
            continue

        if result.status not in _SAVE_STATUSES:
            reason = _fail_reason(result)
            log.warning("추출 실패 #%d status=%s: %s", issue.number, result.status, reason)
            github_inbox.label_issue_failed(issue.number, reason)
            continue

        if _is_empty_payload(extracted):
            reason = _empty_payload_reason(result)
            log.warning("빈 payload 저장 거부 #%d status=%s: %s",
                        issue.number, result.status, reason)
            github_inbox.label_issue_failed(issue.number, reason)
            continue

        save_raw(item, extracted, fetch_status=result.status)
        add_raw_stub(item)
        note = " (자막 없음 — description 폴백)" if result.status == "no_transcript" else ""
        github_inbox.close_issue(
            issue.number,
            f"✅ 수집 완료 — id={item.id}, source={item.source}{note}",
        )
        log.info(
            "raw 저장 + 이슈 close: #%d → id=%s status=%s",
            issue.number, item.id, result.status,
        )


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

        result = fetchers.dispatch(url, source)
        extracted = _result_to_extracted(result)
        caption = validate_user_caption(b.get("user_caption"))
        if caption:
            extracted["user_caption"] = caption
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
            log.info(
                "[dry-run] 저장 스킵 id=%s status=%s title=%s",
                item.id, result.status, item.title[:40],
            )
            processed_raws.append(b["_raw"])
            continue

        if result.status not in _SAVE_STATUSES:
            reason = _fail_reason(result)
            log.warning("추출 실패 id=%s status=%s: %s", item.id, result.status, reason)
            failed_raws.append(b["_raw"])
            continue

        if _is_empty_payload(extracted):
            reason = _empty_payload_reason(result)
            log.warning("빈 payload 저장 거부 id=%s status=%s: %s",
                        item.id, result.status, reason)
            failed_raws.append(b["_raw"])
            continue

        save_raw(item, extracted, fetch_status=result.status)
        add_raw_stub(item)
        processed_raws.append(b["_raw"])
        log.info("raw 저장 id=%s source=%s status=%s", item.id, item.source, result.status)

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
