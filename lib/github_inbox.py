"""GitHub Issues를 inbox로 사용.

iOS Shortcut이 이슈를 만들고 (title=URL, body=memo, label=inbox),
ingester가 이슈를 읽어 처리한 뒤 close 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from . import paths

log = logging.getLogger(__name__)

API = "https://api.github.com"


@dataclass
class InboxIssue:
    number: int
    url: str            # 이슈 title이 곧 캡처된 URL
    memo: str           # body
    created_at: str     # ISO8601


def _headers() -> dict:
    if not paths.GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN 미설정")
    return {
        "Authorization": f"Bearer {paths.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def list_open_inbox_issues(limit: int = 50) -> list[InboxIssue]:
    if not paths.GITHUB_WIKI_REPO:
        raise RuntimeError("GITHUB_WIKI_REPO 미설정 (예: 'aejinyoo/wiki')")
    url = f"{API}/repos/{paths.GITHUB_WIKI_REPO}/issues"
    params = {
        "state": "open",
        "labels": paths.INBOX_LABEL,
        "per_page": min(limit, 100),
        "sort": "created",
        "direction": "asc",
    }
    r = requests.get(url, headers=_headers(), params=params, timeout=20)
    r.raise_for_status()
    items: list[InboxIssue] = []
    for issue in r.json():
        if "pull_request" in issue:  # PR은 제외
            continue
        items.append(InboxIssue(
            number=issue["number"],
            url=(issue.get("title") or "").strip(),
            memo=(issue.get("body") or "").strip(),
            created_at=issue.get("created_at", ""),
        ))
    return items


def close_issue(number: int, comment: str | None = None) -> None:
    """이슈 close (선택적으로 코멘트 남김)."""
    base = f"{API}/repos/{paths.GITHUB_WIKI_REPO}/issues/{number}"
    if comment:
        requests.post(
            f"{base}/comments",
            headers=_headers(),
            json={"body": comment},
            timeout=15,
        ).raise_for_status()
    r = requests.patch(base, headers=_headers(), json={"state": "closed"}, timeout=15)
    r.raise_for_status()
    log.info("issue #%d close", number)


def label_issue_failed(number: int, reason: str) -> None:
    """실패 이슈에 'inbox-failed' 라벨 추가 + 코멘트."""
    base = f"{API}/repos/{paths.GITHUB_WIKI_REPO}/issues/{number}"
    requests.post(
        f"{base}/labels",
        headers=_headers(),
        json={"labels": ["inbox-failed"]},
        timeout=15,
    )
    requests.post(
        f"{base}/comments",
        headers=_headers(),
        json={"body": f"❌ 처리 실패: {reason}"},
        timeout=15,
    )
    log.warning("issue #%d failed 라벨 + 코멘트", number)
