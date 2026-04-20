#!/usr/bin/env python3
"""URL 또는 source 기준 재시도/삭제 도구.

사용법:
  python scripts/retry.py url "URL"
      인덱스/파일 제거 → 같은 URL 이 다음에 공유되면 재수집됨

  python scripts/retry.py url "URL" --delete-only
      완전 삭제 (의도 명시용)

  python scripts/retry.py by-source Instagram
      dry-run — 대상 목록만 출력

  python scripts/retry.py by-source Instagram --apply --delete-only
      Instagram 전부 실제 삭제

  python scripts/retry.py by-source X --apply
      X 전부 제거 (다음 공유 시 재수집 가능)

모든 '실제 삭제' 경로는 _stats.json 재계산을 자동으로 포함.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib import paths  # noqa: E402
from lib import wiki_io  # noqa: E402
from lib.validate import ALLOWED_SOURCES  # noqa: E402

log = logging.getLogger("retry")


# ─────────────────────────────────────────────────────────────
# 저수준 파일 찾기·삭제
# ─────────────────────────────────────────────────────────────
def _delete_file(p: Path) -> bool:
    try:
        if p.is_file():
            p.unlink()
            return True
    except OSError as e:
        log.warning("삭제 실패 %s: %s", p, e)
    return False


def _find_raw_files(item_id: str) -> list[Path]:
    """flat(raw/<id>.json) + legacy(raw/YYYY-MM-DD/<id>.json)."""
    out: list[Path] = []
    flat = paths.RAW_DIR / f"{item_id}.json"
    if flat.is_file():
        out.append(flat)
    out.extend(p for p in paths.RAW_DIR.glob(f"*/{item_id}.json") if p.is_file())
    return out


def _find_archive_files(item_id: str) -> list[Path]:
    return [p for p in paths.RAW_ARCHIVE_DIR.glob(f"*/{item_id}.json") if p.is_file()]


def _find_wiki_files(item_id: str, index_entry: dict | None) -> list[Path]:
    # 인덱스에 path 가 박혀있으면 그걸 우선 사용 (정확함)
    if index_entry and index_entry.get("path"):
        return [paths.WIKI_REPO / index_entry["path"]]
    if not paths.WIKI_DIR.exists():
        return []
    matches: list[Path] = []
    for md, post in wiki_io.iter_wiki_items():
        if post.get("id") == item_id:
            matches.append(md)
    return matches


# ─────────────────────────────────────────────────────────────
# 단일 아이템 정리 — 두 모드가 공유
# ─────────────────────────────────────────────────────────────
def _remove_one(
    item_id: str, entry: dict | None, delete_only: bool
) -> tuple[dict | None, list[Path]]:
    """인덱스 엔트리 제거 + raw/archive/wiki 파일 삭제. (엔트리, 삭제된 파일들) 반환.

    entry: caller 가 미리 갖고 있는 스냅샷 (path 힌트용). None 이면 remove_from_index
    반환값을 사용한다.
    delete_only: 현재 파일 처리 로직엔 영향 없음 (로그/메시지 분기용 — 향후 block-list
    같은 기능이 생기면 분기점이 될 수 있도록 보존).
    """
    removed = wiki_io.remove_from_index(item_id)
    hint = entry if entry is not None else removed

    deleted: list[Path] = []
    for p in _find_raw_files(item_id):
        if _delete_file(p):
            deleted.append(p)
    for p in _find_archive_files(item_id):
        if _delete_file(p):
            deleted.append(p)
    for p in _find_wiki_files(item_id, hint):
        if _delete_file(p):
            deleted.append(p)
    return hint, deleted


def _print_deleted_paths(deleted: list[Path]) -> None:
    if not deleted:
        print("  삭제한 파일: 없음")
        return
    print(f"  삭제한 파일 {len(deleted)}개:")
    for p in deleted:
        try:
            rel = p.relative_to(paths.WIKI_REPO)
            print(f"    - {rel}")
        except ValueError:
            print(f"    - {p}")


# ─────────────────────────────────────────────────────────────
# url 모드
# ─────────────────────────────────────────────────────────────
def run_url(url: str, delete_only: bool) -> int:
    # 과도기 호환: 정규화 해시 + legacy 해시 둘 다 훑는다.
    candidate_ids = wiki_io.url_hashes(url)
    log.info("대상 URL: %s (id 후보=%s)", url, candidate_ids)

    deleted_all: list[Path] = []
    found_entries: list[tuple[str, dict]] = []

    for cid in candidate_ids:
        entry, deleted = _remove_one(cid, None, delete_only)
        if entry is not None:
            found_entries.append((cid, entry))
            log.info(
                "인덱스 제거: id=%s status=%s category=%s",
                cid, entry.get("status"), entry.get("category"),
            )
        deleted_all.extend(deleted)

    if not found_entries:
        log.info("인덱스에 엔트리 없음 — 파일만 정리했습니다.")

    wiki_io.recompute_stats()
    log.info("_stats.json 재계산 완료.")

    mode = "삭제 전용" if delete_only else "재시도"
    print(f"\n[{mode}] id 후보={candidate_ids}")
    print(f"  URL: {url}")
    if found_entries:
        for cid, entry in found_entries:
            print(f"  index entry (id={cid}): {entry}")
    else:
        print("  index entry: (없음)")
    _print_deleted_paths(deleted_all)

    if delete_only:
        print("  → 완전 삭제. (같은 URL 재공유 시 ingester 는 재수집함)")
    else:
        print("  → 다음에 같은 URL 이 공유되면 재처리됩니다.")
    return 0


# ─────────────────────────────────────────────────────────────
# by-source 모드
# ─────────────────────────────────────────────────────────────
def run_by_source(source: str, apply: bool, delete_only: bool) -> int:
    if source not in ALLOWED_SOURCES:
        print(
            f"[에러] 알 수 없는 source: {source!r}. "
            f"ALLOWED={sorted(ALLOWED_SOURCES)}",
            file=sys.stderr,
        )
        return 2

    matching = wiki_io.list_index_by_source(source)
    if not matching:
        print(f"[by-source {source}] 대상 없음")
        return 0

    if not apply:
        print(
            f"[by-source {source}] dry-run — 대상 {len(matching)}개 "
            f"(실제 삭제는 --apply 필요):"
        )
        for iid, entry in matching:
            title = (entry.get("title") or "")[:60]
            url = entry.get("url") or ""
            print(f"  - id={iid}  title={title!r}  url={url}")
        extra = " --delete-only" if delete_only else ""
        print(f"\n  실제 삭제: --apply{extra}")
        return 0

    mode = "삭제 전용" if delete_only else "재시도"
    log.info("[by-source %s] %s 모드 · %d건 처리 시작", source, mode, len(matching))
    total_deleted: list[Path] = []
    ok = 0
    fail = 0
    for iid, entry in matching:
        try:
            _, deleted = _remove_one(iid, entry, delete_only)
            total_deleted.extend(deleted)
            log.info("  - id=%s 삭제 파일 %d개", iid, len(deleted))
            ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("  - id=%s 처리 실패: %s", iid, e)
            fail += 1

    wiki_io.recompute_stats()
    log.info("_stats.json 재계산 완료.")

    print(f"\n[by-source {source}] 완료 — mode={mode}")
    print(f"  삭제됨: {ok}개, 실패: {fail}개, 파일 {len(total_deleted)}개")
    return 0 if fail == 0 else 1


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="URL 또는 source 기준 재시도/삭제 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_url = sub.add_parser("url", help="URL 로 대상 지정")
    p_url.add_argument("url", help="대상 URL")
    p_url.add_argument(
        "--delete-only",
        action="store_true",
        help="완전 삭제 (재시도 큐 복귀 안 함 — 의도 명시용)",
    )

    p_src = sub.add_parser("by-source", help="source 기준 일괄 처리")
    p_src.add_argument("source", help=f"ALLOWED: {sorted(ALLOWED_SOURCES)}")
    p_src.add_argument(
        "--apply",
        action="store_true",
        help="실제 삭제 수행 (없으면 dry-run — 기본값)",
    )
    p_src.add_argument(
        "--delete-only",
        action="store_true",
        help="완전 삭제 (의도 명시용). --apply 과 함께 써야 효과 발생",
    )

    args = ap.parse_args()
    if args.cmd == "url":
        sys.exit(run_url(args.url, delete_only=args.delete_only))
    if args.cmd == "by-source":
        sys.exit(
            run_by_source(args.source, apply=args.apply, delete_only=args.delete_only)
        )
    ap.error(f"알 수 없는 명령: {args.cmd}")


if __name__ == "__main__":
    main()
