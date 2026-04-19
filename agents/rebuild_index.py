#!/usr/bin/env python3
"""rebuild_index — 수동 복구용 (LLM 미호출).

wiki/**/*.md 전수 스캔 → _index.json · _stats.json 통째 재생성.
`uv run agents/rebuild_index.py` 로 실행.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging

import _bootstrap

_bootstrap.setup(__file__)

from lib import paths  # noqa: E402
from lib.validate import validate_post_file  # noqa: E402
from lib.wiki_io import iter_wiki_items, recompute_stats  # noqa: E402

log = logging.getLogger("rebuild_index")


def rebuild_index_full() -> dict:
    index = {
        "version": 1,
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "items": {},
    }
    count = 0
    issues = 0
    for path, post in iter_wiki_items():
        item_id = post.get("id") or path.stem
        note = validate_post_file(path)
        if note:
            issues += 1
        rel = path.relative_to(paths.WIKI_REPO).as_posix()
        index["items"][item_id] = {
            "url": post.get("url", ""),
            "source": post.get("source", "Manual"),
            "captured_at": post.get("captured_at", ""),
            "status": "classified",
            "category": post.get("category", path.parent.name),
            "tags": list(post.get("tags", [])),
            "path": rel,
            "title": post.get("title", ""),
        }
        count += 1

    paths.INDEX_JSON.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("_index.json 재생성: %d건 (검증 이슈 %d건)", count, issues)
    return index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args()

    if not args.stats_only:
        rebuild_index_full()

    stats = recompute_stats()
    log.info("_stats.json 재생성: 카테고리=%d, 태그=%d",
             len(stats.get("categories", {})), len(stats.get("tags", {})))


if __name__ == "__main__":
    main()
