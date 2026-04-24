"""Classifier 빈-입력 가드 단위 테스트 (2026-04-23 환각 오염 회귀 방지).

URL 만 있고 TITLE·본문·USER_CAPTION 이 모두 비면 LLM 호출 없이 스킵하고,
raw 파일은 그대로 둬 다음 재수집 후 재분류가 가능해야 한다.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))


class _ClassifierTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-classifier-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        from lib import paths as _paths
        importlib.reload(_paths)
        from lib import wiki_io as _wiki_io
        importlib.reload(_wiki_io)
        from agents import classifier as _classifier
        importlib.reload(_classifier)
        self.paths = _paths
        self.classifier = _classifier
        (self.tmp / "raw").mkdir(exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_raw(self, item_id: str, *, extracted: dict, fetch_status: str = "ok") -> Path:
        p = self.paths.RAW_DIR / f"{item_id}.json"
        payload = {
            "item": {
                "id": item_id,
                "url": f"https://example.com/{item_id}",
                "source": "YouTube",
                "captured_at": "2026-04-23T06:38:15Z",
                "title": extracted.get("title", ""),
                "author": "",
                "body": "",
            },
            "fetch_status": fetch_status,
            "extracted": extracted,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p


class TestHasClassifiableSignal(_ClassifierTestBase):
    def test_all_empty_returns_false(self) -> None:
        self.assertFalse(self.classifier._has_classifiable_signal({}))
        self.assertFalse(self.classifier._has_classifiable_signal(
            {"title": "", "text": "", "user_caption": ""}
        ))
        self.assertFalse(self.classifier._has_classifiable_signal(
            {"title": "   ", "text": "\n\n", "user_caption": None}
        ))

    def test_title_alone_is_signal(self) -> None:
        self.assertTrue(self.classifier._has_classifiable_signal({"title": "안녕"}))

    def test_text_alone_is_signal(self) -> None:
        self.assertTrue(self.classifier._has_classifiable_signal({"text": "hello"}))

    def test_text_cleaned_alone_is_signal(self) -> None:
        self.assertTrue(self.classifier._has_classifiable_signal(
            {"text_cleaned": "cleaned", "text": ""}
        ))

    def test_user_caption_alone_is_signal(self) -> None:
        self.assertTrue(self.classifier._has_classifiable_signal({"user_caption": "흥미로움"}))


class TestClassifyOneEmptyInput(_ClassifierTestBase):
    def test_empty_payload_returns_none_without_llm_call(self) -> None:
        """URL 외 신호 없음 → LLM 미호출 + None 반환."""
        self._write_raw("empty01", extracted={"title": "", "text": ""},
                        fetch_status="no_transcript")
        raw_path = self.paths.RAW_DIR / "empty01.json"

        fake_haiku = MagicMock()
        with patch.object(self.classifier.claude, "call_haiku", fake_haiku):
            result = self.classifier.classify_one(raw_path, system="(sys)")

        self.assertIsNone(result)
        fake_haiku.assert_not_called()
        # raw 는 그대로 남아있어야 함 (재수집 가능 상태)
        self.assertTrue(raw_path.exists())

    def test_run_skips_empty_and_continues_with_next(self) -> None:
        """run() 은 빈 입력 raw 를 스킵하고 다음 raw 계속. 아카이브 안 함."""
        self._write_raw("empty01", extracted={"title": "", "text": ""})
        self._write_raw("real01", extracted={"title": "제목 있음", "text": "본문"})

        # call_haiku 는 real01 에만 호출되어야 함
        def fake_haiku(**_):
            from lib.llm import LLMResult

            return LLMResult(
                text=json.dumps({
                    "title": "T", "category": "trend-reports",
                    "tags": ["a", "b", "c"],
                    "summary_3lines": "- x\n- y\n- z",
                    "confidence": 0.7, "key_takeaways": ["k1", "k2", "k3"],
                    "why_it_matters": "w", "what_to_try": "t",
                    "body_ko": "", "original_language": "en",
                }),
                input_tokens=100, output_tokens=50, model="gemini-2.5-flash-lite",
            )

        with patch.object(self.classifier.claude, "call_haiku",
                          side_effect=fake_haiku) as mock_haiku:
            self.classifier.run()

        self.assertEqual(mock_haiku.call_count, 1)
        # empty01 raw 는 그대로 (아카이브 안 됨)
        self.assertTrue((self.paths.RAW_DIR / "empty01.json").exists())
        # real01 은 분류 + 아카이브 이동
        self.assertFalse((self.paths.RAW_DIR / "real01.json").exists())

    def test_run_propagates_token_cap_and_breaks_loop(self) -> None:
        """분류 중 TokenCapExceeded → 루프 중단. 다음 raw 는 건드리지 않음."""
        from lib.llm import TokenCapExceeded

        self._write_raw("a", extracted={"title": "제목", "text": "본문"})
        self._write_raw("b", extracted={"title": "제목", "text": "본문"})

        def raise_cap(**_):
            raise TokenCapExceeded("daily cap")

        with patch.object(self.classifier.claude, "call_haiku", side_effect=raise_cap):
            self.classifier.run()

        # 둘 다 미분류 상태로 남아 있어야 함
        self.assertTrue((self.paths.RAW_DIR / "a.json").exists())
        self.assertTrue((self.paths.RAW_DIR / "b.json").exists())


if __name__ == "__main__":
    unittest.main()
