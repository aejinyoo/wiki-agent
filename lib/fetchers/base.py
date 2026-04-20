"""Fetcher 공통 반환 타입."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FetchResult:
    status: str  # "ok" | "no_transcript" | "login_required" | "failed"
    title: str = ""
    text: str = ""
    metadata: dict = field(default_factory=dict)
    error: str | None = None
