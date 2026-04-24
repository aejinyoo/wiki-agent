"""Daily Brief 빈 응답 가드 단위 테스트 (2026-04-24 빈 파일 사건 회귀 방지).

Gemini 가 예외 없이 빈 문자열을 돌려주면 파일을 덮어쓰기 전에 fallback 문구로
치환해야 한다. `lib.llm._generate` 는 `resp.text or ""` 로 빈 응답을 조용히
통과시키므로 daily_brief 가 마지막 방어선이다.
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))


class _DailyBriefTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-daily-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        from lib import paths as _paths
        importlib.reload(_paths)
        from agents import daily_brief as _db
        importlib.reload(_db)
        self.paths = _paths
        self.db = _db
        self.paths.DAILY_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _llm_result(self, text: str):
        from lib.llm import LLMResult

        return LLMResult(text=text, input_tokens=10, output_tokens=5, model="test")


class TestEmptyResponseGuard(_DailyBriefTestBase):
    def test_empty_response_replaced_with_fallback(self) -> None:
        target = dt.date(2026, 4, 24)
        with patch.object(self.db.claude, "call_sonnet",
                          return_value=self._llm_result("")):
            self.db._generate_one(target, dry_run=False, force=False)

        out = self.paths.DAILY_DIR / f"{target.isoformat()}.md"
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")
        self.assertIn("LLM empty response", content)
        # 빈 줄만 있는 파일이 아님을 확인
        self.assertGreater(len(content.strip()), 20)

    def test_whitespace_only_response_replaced_with_fallback(self) -> None:
        target = dt.date(2026, 4, 24)
        with patch.object(self.db.claude, "call_sonnet",
                          return_value=self._llm_result("   \n\n  ")):
            self.db._generate_one(target, dry_run=False, force=False)

        out = self.paths.DAILY_DIR / f"{target.isoformat()}.md"
        content = out.read_text(encoding="utf-8")
        self.assertIn("LLM empty response", content)

    def test_normal_response_saved_as_is(self) -> None:
        target = dt.date(2026, 4, 24)
        normal = "# Daily Brief\n\n실제 내용"
        with patch.object(self.db.claude, "call_sonnet",
                          return_value=self._llm_result(normal)):
            self.db._generate_one(target, dry_run=False, force=False)

        out = self.paths.DAILY_DIR / f"{target.isoformat()}.md"
        content = out.read_text(encoding="utf-8")
        self.assertIn("실제 내용", content)
        self.assertNotIn("LLM empty response", content)

    def test_token_cap_still_produces_fallback(self) -> None:
        """회귀 방지 — 기존 TokenCapExceeded 경로도 유지."""
        from lib.llm import TokenCapExceeded

        target = dt.date(2026, 4, 24)
        with patch.object(self.db.claude, "call_sonnet",
                          side_effect=TokenCapExceeded("cap")):
            self.db._generate_one(target, dry_run=False, force=False)

        out = self.paths.DAILY_DIR / f"{target.isoformat()}.md"
        content = out.read_text(encoding="utf-8")
        self.assertIn("토큰 캡", content)


if __name__ == "__main__":
    unittest.main()
