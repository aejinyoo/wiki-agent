"""Transcript Cleanup 에이전트 단위 테스트 (Task 5).

커버:
  - _needs_cleanup 필터 (source/fetch_status/has_transcript/길이/이미 정제됨)
  - run() 성공 경로: extracted.text_cleaned + payload.cleaned 추가 + text 원문 유지
  - run() 실패 경로: LLM 예외 발생 시 원본 그대로, 플래그 안 붙음
  - dry-run: 파일 변경 없음
  - 캡: TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP 초과 시 중단
  - classifier._build_user: text_cleaned 우선, 없으면 text 폴백
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
from unittest.mock import patch

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))


class _CleanupTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-cleanup-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        # 테스트 간 캡 격리
        os.environ["TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP"] = "15"
        os.environ["TRANSCRIPT_CLEANUP_MIN_CHARS"] = "100"
        from lib import paths as _paths
        importlib.reload(_paths)
        from lib import wiki_io as _wiki_io
        importlib.reload(_wiki_io)
        from agents import transcript_cleanup as _tc
        importlib.reload(_tc)
        self.paths = _paths
        self.wiki_io = _wiki_io
        self.tc = _tc
        # raw/ 는 _bootstrap.ensure_dirs() 가 이미 생성
        (self.tmp / "raw").mkdir(exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP", None)
        os.environ.pop("TRANSCRIPT_CLEANUP_MIN_CHARS", None)

    def _write_raw(
        self,
        *,
        item_id: str = "yt0001",
        source: str = "YouTube",
        fetch_status: str = "ok",
        text: str | None = None,
        has_transcript: bool = True,
        text_cleaned: str | None = None,
        cleaned: bool = False,
    ) -> Path:
        if text is None:
            text = "a" * 200  # 길이 ≥ 100
        extracted = {
            "title": "t",
            "text": text,
            "has_transcript": has_transcript,
        }
        if text_cleaned is not None:
            extracted["text_cleaned"] = text_cleaned
        payload = {
            "item": {
                "id": item_id,
                "url": f"https://www.youtube.com/watch?v={item_id}",
                "source": source,
                "captured_at": "2026-04-23T07:30:00+00:00",
            },
            "fetch_status": fetch_status,
            "extracted": extracted,
        }
        if cleaned:
            payload["cleaned"] = True
        p = self.tmp / "raw" / f"{item_id}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


class TestNeedsCleanupFilter(_CleanupTestBase):

    def _payload(self, **kwargs) -> dict:
        p = self._write_raw(**kwargs)
        return json.loads(p.read_text(encoding="utf-8"))

    def test_happy_path(self) -> None:
        payload = self._payload()
        self.assertTrue(self.tc._needs_cleanup(payload, 100))

    def test_rejects_non_youtube(self) -> None:
        payload = self._payload(source="X")
        self.assertFalse(self.tc._needs_cleanup(payload, 100))

    def test_rejects_no_transcript_status(self) -> None:
        payload = self._payload(fetch_status="no_transcript")
        self.assertFalse(self.tc._needs_cleanup(payload, 100))

    def test_rejects_has_transcript_false(self) -> None:
        payload = self._payload(has_transcript=False)
        self.assertFalse(self.tc._needs_cleanup(payload, 100))

    def test_rejects_already_cleaned_flag(self) -> None:
        payload = self._payload(cleaned=True)
        self.assertFalse(self.tc._needs_cleanup(payload, 100))

    def test_rejects_existing_text_cleaned(self) -> None:
        payload = self._payload(text_cleaned="prev run")
        self.assertFalse(self.tc._needs_cleanup(payload, 100))

    def test_rejects_too_short(self) -> None:
        payload = self._payload(text="short")
        self.assertFalse(self.tc._needs_cleanup(payload, 100))


class TestCleanupRun(_CleanupTestBase):

    def _fake_result(self, text: str = "cleaned prose."):
        from lib.llm import LLMResult

        return LLMResult(text=text, input_tokens=100, output_tokens=50, model="test")

    def test_success_adds_text_cleaned_and_flag_without_clobbering_text(self) -> None:
        raw_text = "hello hello um world " * 20  # 길이 ≥ 100 확보
        p = self._write_raw(text=raw_text)
        with patch("lib.llm.call_haiku", return_value=self._fake_result("hello world.")):
            self.tc.run()
        payload = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(payload["extracted"]["text"], raw_text)
        self.assertEqual(payload["extracted"]["text_cleaned"], "hello world.")
        self.assertTrue(payload["cleaned"])

    def test_llm_failure_leaves_file_intact(self) -> None:
        p = self._write_raw()
        original = p.read_text(encoding="utf-8")
        with patch("lib.llm.call_haiku", side_effect=RuntimeError("api down")):
            self.tc.run()
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_empty_llm_output_is_skipped(self) -> None:
        p = self._write_raw()
        original = p.read_text(encoding="utf-8")
        with patch("lib.llm.call_haiku", return_value=self._fake_result("   ")):
            self.tc.run()
        # 파일 변경 없음 (text_cleaned 안 붙음)
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_dry_run_does_not_call_llm_or_write(self) -> None:
        p = self._write_raw()
        original = p.read_text(encoding="utf-8")
        with patch("lib.llm.call_haiku") as mock_call:
            self.tc.run(dry_run=True)
            mock_call.assert_not_called()
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_cap_stops_after_limit(self) -> None:
        # 3건 중 limit=2 → 2개만 처리
        for i in range(3):
            self._write_raw(item_id=f"yt{i:04d}")
        with patch("lib.llm.call_haiku", return_value=self._fake_result("x")):
            self.tc.run(limit=2)
        cleaned_count = 0
        for p in (self.tmp / "raw").glob("*.json"):
            if json.loads(p.read_text(encoding="utf-8")).get("cleaned"):
                cleaned_count += 1
        self.assertEqual(cleaned_count, 2)

    def test_token_cap_exceeded_breaks_loop(self) -> None:
        self._write_raw(item_id="yt0001")
        self._write_raw(item_id="yt0002")
        from lib.llm import TokenCapExceeded

        with patch("lib.llm.call_haiku", side_effect=TokenCapExceeded("cap")):
            self.tc.run()
        # 둘 다 cleaned 안 되어야 함
        for p in sorted((self.tmp / "raw").glob("*.json")):
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.assertFalse(payload.get("cleaned"))


class TestClassifierPrefersCleaned(unittest.TestCase):
    """classifier._build_user 가 text_cleaned 우선, 없으면 text 폴백."""

    def setUp(self) -> None:
        # classifier import 시 paths 필요
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-clsfr-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        from lib import paths as _paths
        importlib.reload(_paths)
        from lib import wiki_io as _wiki_io
        importlib.reload(_wiki_io)
        from agents import classifier as _classifier
        importlib.reload(_classifier)
        self.classifier = _classifier

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _item(self) -> dict:
        return {
            "id": "abc",
            "url": "https://youtu.be/abc",
            "source": "YouTube",
            "captured_at": "2026-04-23T00:00:00Z",
        }

    def test_uses_cleaned_when_present(self) -> None:
        out = self.classifier._build_user(
            self._item(),
            {"title": "t", "text": "raw spoken um", "text_cleaned": "clean prose."},
        )
        self.assertIn("clean prose.", out)
        self.assertNotIn("raw spoken um", out)

    def test_falls_back_to_text_when_no_cleaned(self) -> None:
        out = self.classifier._build_user(
            self._item(),
            {"title": "t", "text": "raw only"},
        )
        self.assertIn("raw only", out)

    def test_empty_cleaned_falls_back_to_text(self) -> None:
        out = self.classifier._build_user(
            self._item(),
            {"title": "t", "text": "raw body", "text_cleaned": ""},
        )
        self.assertIn("raw body", out)


if __name__ == "__main__":
    unittest.main()
