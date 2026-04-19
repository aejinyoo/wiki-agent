"""기획서 5.8 '검증은 에이전트 내부에 validate & auto-fix로 내장'.

Classifier/Curator가 .md 쓰기 직전에 호출. 오류는 로그로만 남기고 진행을 막지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import paths, wiki_io
from .wiki_io import WikiItem

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("id", "url", "source", "captured_at")
ALLOWED_SOURCES = {"X", "YouTube", "Threads", "Instagram", "Manual"}


class ValidationNote:
    """검증 과정에서 생긴 조정 메시지."""
    def __init__(self):
        self.notes: list[str] = []

    def add(self, msg: str) -> None:
        log.info("validate: %s", msg)
        self.notes.append(msg)

    def __bool__(self) -> bool:
        return bool(self.notes)


def _ensure_category_folder(category: str) -> None:
    if not category:
        return
    paths.wiki_category_dir(category)


def _coerce_tags(tags) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def validate_and_fix(item: WikiItem) -> ValidationNote:
    """WikiItem을 제자리에서 교정. 에이전트가 쓰기 직전에 호출."""
    note = ValidationNote()

    # 필수 필드
    for f in REQUIRED_FIELDS:
        if not getattr(item, f, None):
            note.add(f"필수 필드 {f} 누락 — 저장은 진행하되 확인 필요")

    # source 정규화
    if item.source not in ALLOWED_SOURCES:
        original = item.source
        inferred = _infer_source(item.url)
        item.source = inferred
        note.add(f"source='{original}' → '{inferred}'")

    # tags 캐스팅
    coerced = _coerce_tags(item.tags)
    if coerced != item.tags:
        note.add(f"tags 타입 보정 → {coerced}")
        item.tags = coerced

    # category 존재 확인 → 없으면 폴더 생성
    if item.category:
        _ensure_category_folder(item.category)

    # confidence 범위
    try:
        c = float(item.confidence)
        if c < 0 or c > 1:
            note.add(f"confidence 범위 외 ({c}) → 0~1 클램프")
            item.confidence = max(0.0, min(1.0, c))
        else:
            item.confidence = c
    except (TypeError, ValueError):
        note.add(f"confidence 캐스팅 실패 ({item.confidence!r}) → 0.0")
        item.confidence = 0.0

    # id 중복 체크 (있어도 막지는 않음, 로그만)
    if wiki_io.index_has(item.id):
        note.add(f"id={item.id} 이미 인덱스에 존재 (덮어쓰기 진행)")

    return note


def _infer_source(url: str) -> str:
    if not url:
        return "Manual"
    u = url.lower()
    if "twitter.com" in u or "x.com" in u:
        return "X"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "threads.net" in u:
        return "Threads"
    if "instagram.com" in u:
        return "Instagram"
    return "Manual"


def validate_post_file(path: Path) -> ValidationNote:
    """이미 저장된 .md 파일의 frontmatter를 검증. rebuild_index 등에서 사용."""
    note = ValidationNote()
    try:
        post = wiki_io.read_wiki_item(path)
    except Exception as e:  # noqa: BLE001
        note.add(f"frontmatter 파싱 실패 {path.name}: {e}")
        return note

    for f in REQUIRED_FIELDS:
        if not post.get(f):
            note.add(f"{path.name}: 필수 필드 {f} 누락")

    src = post.get("source")
    if src and src not in ALLOWED_SOURCES:
        note.add(f"{path.name}: source='{src}' 비정상")

    tags = post.get("tags")
    if tags is not None and not isinstance(tags, list):
        note.add(f"{path.name}: tags가 list 아님 ({type(tags).__name__})")

    return note
