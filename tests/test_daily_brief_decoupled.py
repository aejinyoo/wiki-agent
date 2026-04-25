"""Daily Brief LLM 디커플링 이후 순수 함수·조립 로직 단위 테스트.

spec: docs/features/brief-llm-decoupling.md Step 6.
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


from agents import daily_brief as db  # noqa: E402


class TestPickHighlights(unittest.TestCase):
    def test_basic_filters_recent_urls(self) -> None:
        items = [
            {"url": "a", "category": "x", "confidence": 0.9},
            {"url": "b", "category": "y", "confidence": 0.8},
            {"url": "c", "category": "z", "confidence": 0.7},
            {"url": "d", "category": "x", "confidence": 0.6},
            {"url": "e", "category": "y", "confidence": 0.5},
        ]
        picks = db._pick_highlights(items, {"b"}, top_n=3)

        urls = [p["url"] for p in picks]
        self.assertEqual(len(picks), 3)
        self.assertNotIn("b", urls)

    def test_diversity_swap_when_top_n_all_same_category(self) -> None:
        items = [
            {"url": "a", "category": "x", "confidence": 0.9},
            {"url": "b", "category": "x", "confidence": 0.8},
            {"url": "c", "category": "x", "confidence": 0.7},
            {"url": "d", "category": "y", "confidence": 0.6},
            {"url": "e", "category": "x", "confidence": 0.5},
        ]
        picks = db._pick_highlights(items, set(), top_n=3)
        cats = {p["category"] for p in picks}
        self.assertGreater(len(cats), 1)
        self.assertIn("y", cats)

    def test_no_swap_when_all_items_same_category(self) -> None:
        items = [
            {"url": str(i), "category": "x", "confidence": 0.9 - 0.1 * i}
            for i in range(5)
        ]
        picks = db._pick_highlights(items, set(), top_n=3)

        self.assertEqual(len(picks), 3)
        self.assertEqual({p["category"] for p in picks}, {"x"})


class TestClassifyDifficulty(unittest.TestCase):
    def test_short_text_returns_one_star(self) -> None:
        self.assertEqual(db._classify_difficulty("잠깐 보기"), "⭐")

    def test_hard_keyword_returns_three_stars(self) -> None:
        self.assertEqual(db._classify_difficulty("프로토타입 구축해보기"), "⭐⭐⭐")


class TestRenderHighlights(unittest.TestCase):
    def test_empty_picks(self) -> None:
        out = db._render_highlights([])
        self.assertIn("📌 하이라이트", out)
        self.assertIn("(어제 수집분 없음)", out)


class TestRenderExperiments(unittest.TestCase):
    def test_skips_empty_what_to_try(self) -> None:
        picks = [
            {"title": "A", "what_to_try": ""},
            {"title": "B", "what_to_try": "   "},
            {"title": "C", "what_to_try": "계정 만들어보기"},
        ]
        out = db._render_experiments(picks)

        self.assertIn("| C |", out)
        self.assertNotIn("| A |", out)
        self.assertNotIn("| B |", out)


class TestGenerateOneWithLLMFailure(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-decoupled-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        from lib import paths as _paths

        importlib.reload(_paths)
        importlib.reload(db)
        self.paths = _paths
        self.paths.DAILY_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _llm_result(self, text: str):
        from lib.llm import LLMResult

        return LLMResult(text=text, input_tokens=10, output_tokens=5, model="test")

    def test_empty_llm_response_keeps_other_sections(self) -> None:
        target = dt.date(2026, 4, 26)
        with patch.object(db.claude, "call_sonnet", return_value=self._llm_result("")):
            db._generate_one(target, dry_run=False, force=True)

        out = self.paths.DAILY_DIR / f"{target.isoformat()}.md"
        self.assertTrue(out.exists())
        content = out.read_text(encoding="utf-8")

        # 헤더 + 4 섹션 모두 존재
        self.assertIn("# Daily Design Brief — 2026-04-26", content)
        self.assertIn("## 🔥 오늘의 3줄", content)
        self.assertIn("## 📌 하이라이트", content)
        self.assertIn("## 🧪 오늘 해볼 만한 실험", content)
        self.assertIn("## 🧭 이번 주 위키 변화", content)

        # 🔥 자리에 fallback 라인
        self.assertIn("LLM empty response", content)


if __name__ == "__main__":
    unittest.main()
