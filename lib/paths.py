"""경로·환경변수 중앙 관리.

모든 에이전트는 여기서 경로를 가져온다. `.env`는 AGENT_HOME/.env를 기본으로 읽는다.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# .env 로드 — 에이전트 홈 루트 기준
# ─────────────────────────────────────────────────────────────
_THIS_FILE = Path(__file__).resolve()
AGENT_HOME = Path(os.environ.get("AGENT_HOME") or _THIS_FILE.parent.parent)

load_dotenv(AGENT_HOME / ".env", override=False)


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"환경변수 {name} 가 설정되지 않았습니다. .env 확인하세요.")
    return v


# ─────────────────────────────────────────────────────────────
# 위키 repo (소스 오브 트루스)
# ─────────────────────────────────────────────────────────────
WIKI_REPO = Path(_require("WIKI_REPO_PATH")).expanduser().resolve()

WIKI_DIR = WIKI_REPO / "wiki"             # 카테고리 폴더들
DAILY_DIR = WIKI_REPO / "daily"           # 일일 브리프
RAW_DIR = WIKI_REPO / "raw"               # 수집 원본 (미분류만 남음)
RAW_ARCHIVE_DIR = WIKI_REPO / "raw-archive"  # 분류 완료된 원본 (월별 보존)
CHANGELOG_DIR = WIKI_REPO / "_changelog"  # Curator 변경 로그

INDEX_JSON = WIKI_REPO / "_index.json"
STATS_JSON = WIKI_REPO / "_stats.json"
META_YAML = WIKI_REPO / "_meta.yaml"

# Git에 올리지 않는 로컬 전용 파일 (WIKI_REPO 루트, .gitignore 처리 필요)
PERSONAL_CONTEXT = WIKI_REPO / "_personal_context.md"
USAGE_JSON = WIKI_REPO / "_usage.json"

# ─────────────────────────────────────────────────────────────
# Inbox (기본: wiki repo 루트. 과거 iCloud 경로도 선택적으로 지원)
# ─────────────────────────────────────────────────────────────
INBOX_MD = Path(
    os.environ.get("INBOX_PATH") or (WIKI_REPO / "inbox.md")
).expanduser()
INBOX_FAILED_MD = WIKI_REPO / "inbox-failed.md"
INBOX_ARCHIVE_DIR = WIKI_REPO / "inbox-archive"

# ─────────────────────────────────────────────────────────────
# GitHub (이슈 기반 inbox + Actions 자동 push 용)
# ─────────────────────────────────────────────────────────────
# "owner/repo" 형태 (예: "aejinyoo/wiki")
GITHUB_WIKI_REPO = os.environ.get("GITHUB_WIKI_REPO", "")
# PAT 또는 Actions의 GITHUB_TOKEN. 비어있으면 파일 기반 inbox 사용.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("WIKI_REPO_TOKEN", "")
INBOX_LABEL = os.environ.get("INBOX_LABEL", "inbox")


def inbox_mode() -> str:
    """'issues' | 'file'. 환경에 따라 자동 선택."""
    if GITHUB_WIKI_REPO and GITHUB_TOKEN:
        return "issues"
    return "file"

# ─────────────────────────────────────────────────────────────
# 에이전트 루트
# ─────────────────────────────────────────────────────────────
LOGS_DIR = AGENT_HOME / "logs"
PROMPTS_DIR = AGENT_HOME / "prompts"


# ─────────────────────────────────────────────────────────────
# 모델·가드레일
# ─────────────────────────────────────────────────────────────
# 역할 기반 이름 유지 (내부 구현은 Gemini):
#   MODEL_HAIKU  = 빠르고 저렴한 분류용  → 기본값 gemini-2.5-flash-lite
#   MODEL_SONNET = 긴 글쓰기·큐레이션용 → 기본값 gemini-2.5-pro
MODEL_HAIKU = os.environ.get("MODEL_HAIKU", "gemini-2.5-flash-lite")
MODEL_SONNET = os.environ.get("MODEL_SONNET", "gemini-2.5-pro")

DAILY_TOKEN_CAP_SONNET = int(os.environ.get("DAILY_TOKEN_CAP_SONNET", "25000"))
DAILY_TOKEN_CAP_HAIKU = int(os.environ.get("DAILY_TOKEN_CAP_HAIKU", "25000"))

CLASSIFIER_DAILY_ITEM_CAP = int(os.environ.get("CLASSIFIER_DAILY_ITEM_CAP", "30"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def ensure_dirs() -> None:
    """런타임에 없는 디렉토리를 만들어둔다."""
    for d in (WIKI_DIR, DAILY_DIR, RAW_DIR, RAW_ARCHIVE_DIR, CHANGELOG_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def wiki_category_dir(category: str) -> Path:
    """카테고리 폴더 경로. 없으면 생성."""
    d = WIKI_DIR / category
    d.mkdir(parents=True, exist_ok=True)
    return d
