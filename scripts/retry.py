#!/usr/bin/env python3
"""URL 기반 재시도/삭제 도구.

사용법:
  python scripts/retry.py url "https://x.com/..."
      인덱스/파일 제거 → 같은 URL 이 다음에 공유되면 재수집됨

  python scripts/retry.py url "https://x.com/..." --delete-only
      완전 삭제. (같은 URL 재공유 시 ingester 는 여전히 재수집하지만
      의도가 '완전 삭제' 임을 명확히 표시)

두 모드 모두 _stats.json 재계산을 자동으로 포함.
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

log = logging.getLogger("retry")


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
    # 폴백: frontmatter의 id 로 전수 스캔
    if not paths.WIKI_DIR.exists():
        return []
    matches: list[Path] = []
    for md, post in wiki_io.iter_wiki_items():
        if post.get("id") == item_id:
            matches.append(md)
    return matches


def run(url: str, delete_only: bool) -> int:
    # 과도기 호환: 정규화 해시 + legacy 해시 둘 다 훑는다.
    candidate_ids = wiki_io.url_hashes(url)
    log.info("대상 URL: %s (id 후보=%s)", url, candidate_ids)

    deleted: list[Path] = []
    found_entries: list[tuple[str, dict]] = []

    for cid in candidate_ids:
        entry = wiki_io.remove_from_index(cid)
        if entry is not None:
            found_entries.append((cid, entry))
            log.info(
                "인덱스 제거: id=%s status=%s category=%s",
                cid,
                entry.get("status"),
                entry.get("category"),
            )
        for p in _find_raw_files(cid):
            if _delete_file(p):
                deleted.append(p)
        for p in _find_archive_files(cid):
            if _delete_file(p):
                deleted.append(p)
        for p in _find_wiki_files(cid, entry):
            if _delete_file(p):
                deleted.append(p)

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
    if deleted:
        print(f"  삭제한 파일 {len(deleted)}개:")
        for p in deleted:
            try:
                rel = p.relative_to(paths.WIKI_REPO)
                print(f"    - {rel}")
            except ValueError:
                print(f"    - {p}")
    else:
        print("  삭제한 파일: 없음")

    if delete_only:
        print("  → 완전 삭제. (같은 URL 재공유 시 ingester 는 재수집함)")
    else:
        print("  → 다음에 같은 URL 이 공유되면 재처리됩니다.")
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="URL 기반 재시도/삭제 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_url = sub.add_parser("url", help="URL로 대상 지정")
    p_url.add_argument("url", help="대상 URL")
    p_url.add_argument(
        "--delete-only",
        action="store_true",
        help="완전 삭제 (재시도 큐 복귀 안 함 — 의도 명시용)",
    )

    args = ap.parse_args()
    if args.cmd == "url":
        sys.exit(run(args.url, delete_only=args.delete_only))
    ap.error(f"알 수 없는 명령: {args.cmd}")


if __name__ == "__main__":
    main()
