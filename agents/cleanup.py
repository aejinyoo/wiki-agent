#!/usr/bin/env python3
"""Cleanup — raw-archive 보존 기간 만료 파일 정리 (월 1회).

30일 이상 지난 `raw-archive/YYYY-MM/*.json` 파일을 `git rm` 하고,
`_changelog/cleanup-YYYY-MM-DD.md` 마커와 함께 한 커밋으로 묶어 push 한다.

실행 조건:
  - 마지막 cleanup 실행 ≥ 30일 전
    (`_changelog/cleanup-YYYY-MM-DD.md` 중 가장 최근 날짜 기준)
  - 기기가 꺼져 있다 깨어나면 nightly.py 의 RunAtLoad 트리거로 자연 catch-up

삭제 기준은 JSON 의 `item.captured_at` (ISO-8601 UTC).
파일 mtime 은 git clone·복사 시 신뢰할 수 없어 쓰지 않는다.

Curator 와 같은 `_changelog/` 디렉토리를 공유하지만 파일명 접두사
(`cleanup-`) 가 다르므로 curator 의 `_last_run_date()` 에는
ValueError 로 걸러져 서로 간섭하지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import subprocess
from pathlib import Path

import _bootstrap

_bootstrap.setup(__file__)

from lib import paths  # noqa: E402

log = logging.getLogger("cleanup")

RETENTION_DAYS = 30
MIN_DAYS_BETWEEN_RUNS = 30
_MARKER_PREFIX = "cleanup-"


# ─────────────────────────────────────────────────────────────
# is_due / 마커
# ─────────────────────────────────────────────────────────────
def _last_run_date() -> dt.date | None:
    """`_changelog/cleanup-YYYY-MM-DD.md` 중 가장 최근 날짜."""
    if not paths.CHANGELOG_DIR.exists():
        return None
    dates: list[dt.date] = []
    for p in paths.CHANGELOG_DIR.glob(f"{_MARKER_PREFIX}*.md"):
        stem = p.stem[len(_MARKER_PREFIX):]
        try:
            dates.append(dt.date.fromisoformat(stem))
        except ValueError:
            continue
    return max(dates) if dates else None


def _write_marker(summary: str) -> Path:
    today = dt.date.today().isoformat()
    out = paths.CHANGELOG_DIR / f"{_MARKER_PREFIX}{today}.md"
    out.write_text(
        f"# Cleanup run — {today}\n\n{summary}\n",
        encoding="utf-8",
    )
    return out


def is_due(force: bool = False) -> bool:
    """마지막 실행이 30일 이전이면 True. 최초 실행도 True."""
    if force:
        return True
    last = _last_run_date()
    if last is None:
        return True
    return (dt.date.today() - last).days >= MIN_DAYS_BETWEEN_RUNS


# ─────────────────────────────────────────────────────────────
# 만료 판정
# ─────────────────────────────────────────────────────────────
def _parse_captured_at(raw: object) -> dt.datetime | None:
    if not isinstance(raw, dict):
        return None
    item = raw.get("item")
    if not isinstance(item, dict):
        return None
    v = item.get("captured_at")
    if not isinstance(v, str) or not v:
        return None
    try:
        parsed = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _collect_expired(cutoff: dt.datetime) -> list[Path]:
    """cutoff 이전 captured_at 을 가진 raw-archive JSON 목록."""
    expired: list[Path] = []
    root = paths.RAW_ARCHIVE_DIR
    if not root.exists():
        return expired
    for p in sorted(root.glob("*/*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("파싱 실패 — 건너뜀: %s", p.name)
            continue
        cap = _parse_captured_at(raw)
        if cap is None:
            log.warning("captured_at 없음 — 건너뜀: %s", p.name)
            continue
        if cap < cutoff:
            expired.append(p)
    return expired


def _remove_empty_month_dirs() -> list[Path]:
    """git rm 이후 working tree 에 남은 빈 월 폴더를 정리."""
    removed: list[Path] = []
    root = paths.RAW_ARCHIVE_DIR
    if not root.exists():
        return removed
    for d in sorted(root.iterdir()):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            removed.append(d)
    return removed


# ─────────────────────────────────────────────────────────────
# Git
# ─────────────────────────────────────────────────────────────
def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_remove(files: list[Path], repo: Path) -> None:
    """배치 단위로 git rm. 경로는 repo 상대경로로 변환."""
    if not files:
        return
    rels = [str(p.relative_to(repo)) for p in files]
    BATCH = 200
    for i in range(0, len(rels), BATCH):
        _git("rm", "--quiet", *rels[i:i + BATCH], cwd=repo)


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
def run(dry_run: bool = False, force: bool = False) -> None:
    if not is_due(force=force):
        last = _last_run_date()
        log.info("Cleanup 조건 불충족 — 스킵 (마지막: %s, 30일 주기)", last)
        return

    cutoff = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=RETENTION_DAYS)
    expired = _collect_expired(cutoff)
    total_bytes = sum(p.stat().st_size for p in expired if p.exists())

    log.info(
        "만료 대상 %d건 · %.1f KB (cutoff: %s, retention: %d일)",
        len(expired),
        total_bytes / 1024,
        cutoff.isoformat(timespec="seconds"),
        RETENTION_DAYS,
    )

    if dry_run:
        for p in expired[:20]:
            log.info("  [dry-run] rm %s", p.relative_to(paths.WIKI_REPO))
        if len(expired) > 20:
            log.info("  ... +%d건", len(expired) - 20)
        return

    repo = paths.WIKI_REPO

    if not expired:
        marker = _write_marker("삭제 대상 없음 (만료 파일 없음)")
        _git("add", str(marker.relative_to(repo)), cwd=repo)
        try:
            _git("commit", "-m", "chore(cleanup): no expired raw-archive", cwd=repo)
            _git("push", cwd=repo)
        except subprocess.CalledProcessError as e:
            log.warning("마커만 있는 commit/push 실패: %s", (e.stderr or "").strip())
        log.info("삭제할 파일 없음 — 마커만 기록")
        return

    # 1) 만료 JSON 스테이징
    _git_remove(expired, repo)

    # 2) working tree 에 남은 빈 월 폴더 정리 (git 은 빈 디렉토리를 추적하지 않음)
    for d in _remove_empty_month_dirs():
        log.info("빈 폴더 제거: %s", d.relative_to(repo))

    # 3) 마커 기록 & 스테이징
    summary = (
        f"- 삭제: {len(expired)}건 ({total_bytes / 1024:.1f} KB)\n"
        f"- cutoff: {cutoff.isoformat(timespec='seconds')}\n"
        f"- retention: {RETENTION_DAYS}일\n"
    )
    marker = _write_marker(summary)
    _git("add", str(marker.relative_to(repo)), cwd=repo)

    # 4) 단일 커밋 + push
    msg = (
        f"chore(cleanup): prune raw-archive older than "
        f"{RETENTION_DAYS}d ({len(expired)} files)"
    )
    _git("commit", "-m", msg, cwd=repo)
    try:
        _git("push", cwd=repo)
    except subprocess.CalledProcessError as e:
        log.error("git push 실패 — 로컬 커밋은 유지됨: %s", (e.stderr or "").strip())
        raise

    log.info("Cleanup 완료 — %s", msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="삭제 대상만 로그에 출력하고 종료")
    ap.add_argument("--force", action="store_true",
                    help="30일 주기 체크 우회")
    args = ap.parse_args()
    run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
