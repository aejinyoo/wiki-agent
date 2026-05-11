"""Curator v2 dry-run: JSON 파싱 + 가드레일 평가 단위 테스트.

핵심 회귀 방지 포인트:
- protected 카테고리 (`_meta.yaml`) 가 제안에서 제거되는지
- 영향 > impact_limit 가 approval_required 로 이동되는지
- cooldown 위반 카테고리가 skip 되는지
- LLM 응답 노이즈 (```json fence, 앞뒤 텍스트) 에서 JSON 추출되는지
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

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))


class _CuratorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-curator-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        from lib import paths as _paths
        importlib.reload(_paths)
        (self.tmp / "_changelog").mkdir(parents=True, exist_ok=True)
        # curator 모듈을 paths reload 후 import (PROMPTS_DIR 캐시 회피)
        if "curator" in sys.modules:
            del sys.modules["curator"]
        from agents import curator as _curator  # noqa: F401
        importlib.reload(_curator)
        self.curator = _curator

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WIKI_REPO_PATH", None)


class TestParseProposal(_CuratorTestBase):
    def test_plain_json(self) -> None:
        text = '{"tag_renames": [], "summary": "ok"}'
        out = self.curator._parse_proposal(text)
        self.assertEqual(out["summary"], "ok")

    def test_with_json_fence(self) -> None:
        text = 'Here is my proposal:\n```json\n{"tag_renames": [{"from": "a", "to": "b"}]}\n```\nDone.'
        out = self.curator._parse_proposal(text)
        self.assertEqual(out["tag_renames"][0]["from"], "a")

    def test_with_bare_fence(self) -> None:
        text = '```\n{"summary": "x"}\n```'
        out = self.curator._parse_proposal(text)
        self.assertEqual(out["summary"], "x")

    def test_surrounding_prose_no_fence(self) -> None:
        text = '오케이 제안합니다. {"summary": "y"} 끝.'
        out = self.curator._parse_proposal(text)
        self.assertEqual(out["summary"], "y")

    def test_no_json_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.curator._parse_proposal("그냥 텍스트뿐 JSON 없음")


class TestEvaluateProposal(_CuratorTestBase):
    def _snapshot(self, items: list[dict]) -> dict:
        cats: dict[str, int] = {}
        tags: dict[str, int] = {}
        for it in items:
            cats[it["category"]] = cats.get(it["category"], 0) + 1
            for t in it.get("tags") or []:
                tags[t] = tags.get(t, 0) + 1
        return {"items": items, "categories": cats, "tags": tags}

    def test_protected_category_filtered_from_reclassification(self) -> None:
        snap = self._snapshot([
            {"id": "a", "category": "trend-reports", "tags": ["t1"]},
            {"id": "b", "category": "ai-ux-patterns", "tags": ["t2"]},
        ])
        proposal = {
            "reclassifications": [
                {"item_id": "a", "from": "trend-reports", "to": "ai-ux-patterns", "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected={"trend-reports"}, meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["reclassifications"], [])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertIn("trend-reports", out["skipped"][0]["reason"])

    def test_protected_category_filtered_from_category_changes(self) -> None:
        snap = self._snapshot([{"id": str(i), "category": "trend-reports", "tags": []} for i in range(5)])
        proposal = {
            "category_changes": [
                {"op": "split", "target": "trend-reports", "to": "food", "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected={"trend-reports"}, meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["category_changes"], [])
        self.assertEqual(len(out["skipped"]), 1)

    def test_high_impact_moved_to_approval_required(self) -> None:
        items = [{"id": str(i), "category": "x", "tags": ["legacy"]} for i in range(150)]
        snap = self._snapshot(items)
        proposal = {
            "tag_renames": [{"from": "legacy", "to": "new", "reason": "..."}],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(),
            meta={"curator": {"autofix_impact_limit": 100}}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["tag_renames"], [])
        self.assertEqual(len(out["approval_required"]), 1)
        self.assertEqual(out["approval_required"][0]["impact"], 150)

    def test_under_impact_limit_passes_through(self) -> None:
        items = [{"id": str(i), "category": "x", "tags": ["legacy"]} for i in range(5)]
        snap = self._snapshot(items)
        proposal = {
            "tag_renames": [{"from": "legacy", "to": "new", "reason": "..."}],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(),
            meta={"curator": {"autofix_impact_limit": 100}}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(len(out["tag_renames"]), 1)
        self.assertEqual(out["tag_renames"][0]["_impact"], 5)
        self.assertEqual(out["skipped"], [])

    def test_cooldown_violation_skipped(self) -> None:
        snap = self._snapshot([
            {"id": "a", "category": "alpha", "tags": []},
            {"id": "b", "category": "beta", "tags": []},
        ])
        proposal = {
            "reclassifications": [
                {"item_id": "a", "from": "alpha", "to": "beta", "reason": "..."},
            ],
        }
        last_change = {"beta": dt.date(2026, 5, 5)}  # 6일 전 변경
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(),
            meta={"curator": {"cooldown_days": 14, "autofix_impact_limit": 100}},
            last_change=last_change,
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["reclassifications"], [])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertIn("cooldown", out["skipped"][0]["reason"])

    def test_cooldown_expired_passes(self) -> None:
        snap = self._snapshot([
            {"id": "a", "category": "alpha", "tags": []},
            {"id": "b", "category": "beta", "tags": []},
        ])
        proposal = {
            "reclassifications": [
                {"item_id": "a", "from": "alpha", "to": "beta", "reason": "..."},
            ],
        }
        last_change = {"beta": dt.date(2026, 4, 20)}  # 21일 전
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(),
            meta={"curator": {"cooldown_days": 14, "autofix_impact_limit": 100}},
            last_change=last_change,
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(len(out["reclassifications"]), 1)


class TestNewCategoriesProtectedGuard(_CuratorTestBase):
    def _snapshot(self, items: list[dict]) -> dict:
        cats: dict[str, int] = {}
        tags: dict[str, int] = {}
        for it in items:
            cats[it["category"]] = cats.get(it["category"], 0) + 1
            for t in it.get("tags") or []:
                tags[t] = tags.get(t, 0) + 1
        return {"items": items, "categories": cats, "tags": tags}

    def test_seeds_in_protected_filtered_skip_if_below_min(self) -> None:
        """seed 5건 중 4건이 protected 출신 → 살아남는 seed=1 < 5 → skip."""
        items = [
            {"id": "p1", "category": "trend-reports", "tags": []},
            {"id": "p2", "category": "trend-reports", "tags": []},
            {"id": "p3", "category": "trend-reports", "tags": []},
            {"id": "p4", "category": "trend-reports", "tags": []},
            {"id": "ok1", "category": "generative-tools", "tags": []},
        ]
        snap = self._snapshot(items)
        proposal = {
            "new_categories": [
                {"name": "food", "seed_items": ["p1", "p2", "p3", "p4", "ok1"], "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected={"trend-reports"}, meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["new_categories"], [])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertIn("protected 출신", out["skipped"][0]["reason"])

    def test_seeds_in_protected_filtered_pass_if_meets_min(self) -> None:
        """seed 7건 중 2건만 protected 출신 → 살아남는 5건 == 최소 5건 → pass."""
        items = [
            {"id": f"ok{i}", "category": "generative-tools", "tags": []} for i in range(5)
        ] + [
            {"id": "p1", "category": "trend-reports", "tags": []},
            {"id": "p2", "category": "trend-reports", "tags": []},
        ]
        snap = self._snapshot(items)
        proposal = {
            "new_categories": [
                {"name": "food", "seed_items": ["ok0", "ok1", "ok2", "ok3", "ok4", "p1", "p2"], "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected={"trend-reports"}, meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(len(out["new_categories"]), 1)
        self.assertEqual(out["new_categories"][0]["_seeds_filtered_protected"], ["p1", "p2"])
        self.assertEqual(out["new_categories"][0]["seed_items"], ["ok0", "ok1", "ok2", "ok3", "ok4"])
        self.assertEqual(out["new_categories"][0]["_impact"], 5)

    def test_no_protected_seeds_passes_clean(self) -> None:
        items = [{"id": f"x{i}", "category": "generative-tools", "tags": []} for i in range(6)]
        snap = self._snapshot(items)
        proposal = {
            "new_categories": [
                {"name": "y", "seed_items": [f"x{i}" for i in range(6)], "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(), meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(len(out["new_categories"]), 1)
        self.assertNotIn("_seeds_filtered_protected", out["new_categories"][0])

    def test_below_min_seeds_skipped_even_without_protected(self) -> None:
        """seed 가 처음부터 5건 미만이면 (protected 무관) skip."""
        snap = self._snapshot([{"id": "x", "category": "generative-tools", "tags": []}])
        proposal = {
            "new_categories": [
                {"name": "y", "seed_items": ["x"], "reason": "..."},
            ],
        }
        out = self.curator._evaluate_proposal(
            proposal, snap, protected=set(), meta={}, last_change={},
            today=dt.date(2026, 5, 11),
        )
        self.assertEqual(out["new_categories"], [])
        self.assertEqual(len(out["skipped"]), 1)


class TestComputeCategoryLastChange(_CuratorTestBase):
    def test_parses_applied_to_lines(self) -> None:
        (self.tmp / "_changelog" / "2026-04-20.md").write_text(
            "# log\n\n**applied-to**: alpha, beta\n", encoding="utf-8"
        )
        (self.tmp / "_changelog" / "2026-05-05.md").write_text(
            "# log\n\n**applied-to**: beta\n", encoding="utf-8"
        )
        (self.tmp / "_changelog" / "cleanup-2026-04-21.md").write_text(
            "# cleanup\n", encoding="utf-8"
        )  # 파일명 패턴 미일치 → 무시
        out = self.curator._compute_category_last_change()
        self.assertEqual(out["alpha"], dt.date(2026, 4, 20))
        self.assertEqual(out["beta"], dt.date(2026, 5, 5))  # 더 최근 날짜 win

    def test_empty_when_no_applied_to_lines(self) -> None:
        (self.tmp / "_changelog" / "2026-04-20.md").write_text(
            "# Curator dry-run — 2026-04-20\n\n(제안만 기록)\n", encoding="utf-8"
        )
        out = self.curator._compute_category_last_change()
        self.assertEqual(out, {})


class TestRenderDryRunReport(_CuratorTestBase):
    def test_renders_all_sections(self) -> None:
        snapshot = {
            "items": [{"id": "a"}] * 3,
            "categories": {"alpha": 2, "beta": 1},
            "tags": {},
        }
        evaluated = {
            "tag_renames": [{"from": "x", "to": "y", "_impact": 3, "reason": "오타"}],
            "duplicate_merges": [],
            "reclassifications": [],
            "new_categories": [{"name": "food", "seed_items": ["1", "2", "3", "4", "5"], "reason": "..."}],
            "category_changes": [],
            "approval_required": [],
            "skipped": [],
            "summary": "테스트 요약",
        }
        llm_meta = {"model": "gemini-2.5-pro", "input_tokens": 100, "output_tokens": 200}
        out = self.curator._render_dry_run_report(evaluated, snapshot, llm_meta)
        self.assertIn("dry-run", out)
        self.assertIn("gemini-2.5-pro", out)
        self.assertIn("`x` → `y`", out)
        self.assertIn("food", out)
        self.assertIn("테스트 요약", out)


if __name__ == "__main__":
    unittest.main()
