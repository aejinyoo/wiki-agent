"""위키 파일 IO + 인덱스 incremental 갱신.

기획서 5.8의 "각 에이전트가 자기 쓰기 시점에 인덱스 갱신" 원칙을 따른다.
- Ingester → `add_raw_stub(item)`
- Classifier → `upsert_classified(item)`
- Curator → `recompute_stats()` (주 1회 일괄)
- Daily Brief → 읽기만
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter
import yaml

from . import paths

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 기본 데이터 모델
# ─────────────────────────────────────────────────────────────
@dataclass
class WikiItem:
    id: str
    url: str
    source: str                # X | YouTube | Threads | Instagram | Manual
    captured_at: str           # ISO8601
    title: str = ""
    summary_3lines: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    confidence: float = 0.0
    tried: bool = False
    tried_at: str | None = None
    author: str = ""
    body: str = ""             # frontmatter 이후 본문

    def to_frontmatter_post(self) -> frontmatter.Post:
        meta = {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "author": self.author,
            "captured_at": self.captured_at,
            "title": self.title,
            "summary_3lines": self.summary_3lines,
            "tags": list(self.tags),
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "tried": self.tried,
            "tried_at": self.tried_at,
        }
        return frontmatter.Post(self.body or "", **meta)


# ─────────────────────────────────────────────────────────────
# 식별자·슬러그
# ─────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    """URL 기반 안정적 id."""
    normalized = url.strip().lower().rstrip("/")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def slugify(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60] or fallback


def item_filename(item: WikiItem) -> str:
    date = item.captured_at[:10] if item.captured_at else dt.date.today().isoformat()
    return f"{date}-{slugify(item.title, item.id)}.md"


# ─────────────────────────────────────────────────────────────
# _meta.yaml
# ─────────────────────────────────────────────────────────────

def load_meta() -> dict:
    if not paths.META_YAML.exists():
        return {}
    return yaml.safe_load(paths.META_YAML.read_text(encoding="utf-8")) or {}


# ─────────────────────────────────────────────────────────────
# _index.json — incremental
# ─────────────────────────────────────────────────────────────

def _load_index() -> dict:
    if not paths.INDEX_JSON.exists():
        return {"version": 1, "updated_at": None, "items": {}}
    try:
        return json.loads(paths.INDEX_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("_index.json 파싱 실패, 새 인덱스로 시작합니다.")
        return {"version": 1, "updated_at": None, "items": {}}


def _save_index(index: dict) -> None:
    index["updated_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    paths.INDEX_JSON.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def index_has(item_id: str) -> bool:
    return item_id in _load_index().get("items", {})


def add_raw_stub(item: WikiItem) -> None:
    """Ingester가 호출. 최소 메타데이터만 인덱스에 등록."""
    index = _load_index()
    index["items"][item.id] = {
        "url": item.url,
        "source": item.source,
        "captured_at": item.captured_at,
        "status": "raw",
        "category": None,
    }
    _save_index(index)


def upsert_classified(item: WikiItem, rel_path: str) -> None:
    """Classifier가 호출. 분류 완료된 항목을 인덱스에 반영."""
    index = _load_index()
    index["items"][item.id] = {
        "url": item.url,
        "source": item.source,
        "captured_at": item.captured_at,
        "status": "classified",
        "category": item.category,
        "tags": list(item.tags),
        "path": rel_path,
        "title": item.title,
    }
    _save_index(index)


# ─────────────────────────────────────────────────────────────
# _stats.json — Curator가 일괄, rebuild_index도 사용
# ─────────────────────────────────────────────────────────────

def recompute_stats() -> dict:
    stats = {
        "version": 1,
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "categories": {},
        "tags": {},
        "sources": {},
    }
    if not paths.WIKI_DIR.exists():
        paths.STATS_JSON.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return stats

    for md in paths.WIKI_DIR.glob("*/*.md"):
        try:
            post = frontmatter.loads(md.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("frontmatter 파싱 실패 %s: %s", md, e)
            continue
        cat = post.get("category") or md.parent.name
        src = post.get("source") or "Manual"
        tags = post.get("tags") or []
        stats["categories"][cat] = stats["categories"].get(cat, 0) + 1
        stats["sources"][src] = stats["sources"].get(src, 0) + 1
        for t in tags:
            stats["tags"][t] = stats["tags"].get(t, 0) + 1

    paths.STATS_JSON.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats


# ─────────────────────────────────────────────────────────────
# 읽기·쓰기 (item = frontmatter.Post)
# ─────────────────────────────────────────────────────────────

def write_wiki_item(item: WikiItem) -> Path:
    """wiki/{category}/*.md 저장 + 인덱스 반영. 저장 경로 반환."""
    cat_dir = paths.wiki_category_dir(item.category)
    out = cat_dir / item_filename(item)
    post = item.to_frontmatter_post()
    out.write_text(frontmatter.dumps(post), encoding="utf-8")
    rel = out.relative_to(paths.WIKI_REPO).as_posix()
    upsert_classified(item, rel)
    return out


def read_wiki_item(path: Path) -> frontmatter.Post:
    return frontmatter.loads(path.read_text(encoding="utf-8"))


def iter_wiki_items():
    """wiki/**/*.md 전수 이터레이터."""
    for md in paths.WIKI_DIR.glob("*/*.md"):
        try:
            yield md, frontmatter.loads(md.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("frontmatter 파싱 실패 %s: %s", md, e)


# ─────────────────────────────────────────────────────────────
# raw/ 저장
# ─────────────────────────────────────────────────────────────

def save_raw(item: WikiItem, extracted: dict) -> Path:
    """Ingester가 원본 추출 결과를 raw/<id>.json (flat) 으로 저장.

    날짜 정보는 payload['item']['captured_at'] 에 이미 담겨 있으므로
    별도 날짜 폴더를 두지 않습니다. 아카이브 시 captured_at 에서 월 버킷을 계산합니다.
    """
    paths.RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = paths.RAW_DIR / f"{item.id}.json"
    payload = {
        "item": asdict(item),
        "extracted": extracted,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_unclassified_raw():
    """아직 wiki/에 기록되지 않은 raw 파일 이터.

    두 구조를 모두 yield (신구 호환):
      - raw/<id>.json         (현재 flat 구조)
      - raw/YYYY-MM-DD/<id>.json  (legacy — 처음 마이그레이션 전까지 남아있을 수 있음)
    """
    index = _load_index().get("items", {})
    # 중복 제거용 (같은 id 가 flat·legacy 양쪽에 있으면 안 되지만 안전장치)
    seen: set[str] = set()

    # 1) flat 구조 — raw/<id>.json
    for p in sorted(paths.RAW_DIR.glob("*.json")):
        stem = p.stem
        if stem in seen:
            continue
        seen.add(stem)
        meta = index.get(stem)
        if meta and meta.get("status") == "classified":
            continue
        yield p

    # 2) legacy 날짜 폴더 — raw/*/*.json
    for p in sorted(paths.RAW_DIR.glob("*/*.json")):
        stem = p.stem
        if stem in seen:
            continue
        seen.add(stem)
        meta = index.get(stem)
        if meta and meta.get("status") == "classified":
            continue
        yield p


# ─────────────────────────────────────────────────────────────
# raw/ 아카이브 (분류 성공 후 정리)
# ─────────────────────────────────────────────────────────────

def archive_raw(raw_path: Path) -> Path | None:
    """분류 성공한 raw 파일을 raw-archive/YYYY-MM/ 로 이동.

    - flat 구조:   raw/<id>.json             → raw-archive/YYYY-MM/<id>.json
    - legacy 구조: raw/YYYY-MM-DD/<id>.json  → raw-archive/YYYY-MM/<id>.json
                  (이동 후 빈 날짜 폴더는 제거)

    월(YYYY-MM) 결정 우선순위:
      1) JSON payload 의 item.captured_at  (정식 메타데이터)
      2) 부모 폴더 이름 (legacy 구조일 때)
      3) 오늘 날짜
    """
    try:
        if not raw_path.exists():
            return None

        # 1) JSON 내부의 captured_at 에서 월을 결정 (정식 소스)
        month: str | None = None
        try:
            data = json.loads(raw_path.read_text(encoding="utf-8"))
            captured_at = (data.get("item") or {}).get("captured_at") or ""
            if len(captured_at) >= 7:
                month = captured_at[:7]
        except Exception:  # noqa: BLE001
            pass

        # 2) 폴백: 부모 폴더가 YYYY-MM-DD 형태면 거기서
        parent = raw_path.parent
        if month is None and parent != paths.RAW_DIR and len(parent.name) >= 7:
            month = parent.name[:7]

        # 3) 최후 폴백: 오늘 날짜
        if month is None:
            month = dt.date.today().isoformat()[:7]

        dest_dir = paths.RAW_ARCHIVE_DIR / month
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / raw_path.name

        # 같은 id 가 이미 archive 에 있으면 덮어쓰기 (재분류 시나리오)
        if dest.exists():
            dest.unlink()
        raw_path.rename(dest)

        # legacy: 부모가 raw/ 자체가 아니고 비었으면 날짜 폴더 제거
        if parent != paths.RAW_DIR:
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass  # 폴더가 이미 없거나, 다른 프로세스가 쓰고 있으면 무시

        return dest
    except Exception as e:  # noqa: BLE001
        log.warning("archive_raw 실패 (%s): %s", raw_path, e)
        return None
