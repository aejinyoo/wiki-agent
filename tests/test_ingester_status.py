"""Ingester status 기반 분기 단위 테스트 (Task 6).

FetchResult.status → action 매핑 회귀 방지:
  - ok, no_transcript  → save_raw + stub + close(issue) / remove(file)
  - login_required, failed → no save; label_failed(issue) / failed_raws(file)

Plus: raw/<id>.json payload 최상위 `fetch_status` persist 확인.
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


class _IngesterTestBase(unittest.TestCase):
    """각 테스트마다 임시 wiki repo + 모듈 리로드."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-ingester-test-"))
        os.environ["WIKI_REPO_PATH"] = str(self.tmp)
        # 테스트는 모듈 레벨 상수를 쓰므로 reload 필수
        from lib import paths as _paths
        importlib.reload(_paths)
        from lib import wiki_io as _wiki_io
        importlib.reload(_wiki_io)
        from lib import fetchers as _fetchers
        importlib.reload(_fetchers)
        from agents import ingester as _ingester
        importlib.reload(_ingester)
        self.paths = _paths
        self.wiki_io = _wiki_io
        self.fetchers = _fetchers
        self.ingester = _ingester

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fetch_result(self, **kw):
        """FetchResult 팩토리 (테스트 간단화용)."""
        return self.fetchers.FetchResult(**kw)

    def _raw_path(self, item_id: str) -> Path:
        return self.tmp / "raw" / f"{item_id}.json"

    def _load_raw(self, item_id: str) -> dict:
        return json.loads(self._raw_path(item_id).read_text(encoding="utf-8"))


class TestIssuesModeStatusBranching(_IngesterTestBase):
    """GitHub Issues 모드 — status 별로 close/label 분기."""

    def _issue(self, number: int = 1, url: str = "https://example.com/a"):
        from lib.github_inbox import InboxIssue

        return InboxIssue(
            number=number,
            url=url,
            user_caption="",
            created_at="2026-04-23T07:30:00Z",
        )

    def _run_issues_mode(self, fetch_result, issues=None):
        if issues is None:
            issues = [self._issue()]
        gi = MagicMock()
        gi.list_open_inbox_issues.return_value = issues
        with patch.object(self.ingester, "github_inbox", gi), \
             patch.object(self.ingester.fetchers, "dispatch", return_value=fetch_result):
            self.ingester._run_issues_mode(dry_run=False)
        return gi

    def test_ok_saves_raw_and_closes_issue(self) -> None:
        fr = self._fetch_result(
            status="ok",
            title="hello",
            text="body",
            metadata={"k": "v"},
        )
        gi = self._run_issues_mode(fr)
        # save 확인
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertTrue(self._raw_path(item_id).exists())
        # close 호출, label_failed 호출 안 됨
        gi.close_issue.assert_called_once()
        gi.label_issue_failed.assert_not_called()

    def test_no_transcript_saves_raw_and_closes_with_degraded_note(self) -> None:
        fr = self._fetch_result(
            status="no_transcript",
            title="video",
            text="description fallback",
            metadata={"video_id": "abcdefghijk", "has_transcript": False},
        )
        url = "https://www.youtube.com/watch?v=abcdefghijk"
        gi = self._run_issues_mode(fr, issues=[self._issue(url=url)])
        from lib.wiki_io import url_hash
        item_id = url_hash(url)
        payload = self._load_raw(item_id)
        self.assertEqual(payload["fetch_status"], "no_transcript")
        self.assertEqual(payload["extracted"]["text"], "description fallback")
        # close 코멘트에 degraded 표기
        gi.close_issue.assert_called_once()
        comment = gi.close_issue.call_args[0][1]
        self.assertIn("자막 없음", comment)
        gi.label_issue_failed.assert_not_called()

    def test_login_required_labels_failed_and_skips_save(self) -> None:
        fr = self._fetch_result(
            status="login_required",
            error="oEmbed 403: private",
        )
        gi = self._run_issues_mode(fr)
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        gi.label_issue_failed.assert_called_once()
        reason = gi.label_issue_failed.call_args[0][1]
        self.assertIn("403", reason)
        gi.close_issue.assert_not_called()

    def test_failed_labels_failed_and_skips_save(self) -> None:
        fr = self._fetch_result(status="failed", error="network timeout")
        gi = self._run_issues_mode(fr)
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        gi.label_issue_failed.assert_called_once()
        reason = gi.label_issue_failed.call_args[0][1]
        self.assertEqual(reason, "network timeout")
        gi.close_issue.assert_not_called()

    def test_save_raw_persists_fetch_status_ok(self) -> None:
        fr = self._fetch_result(status="ok", title="t", text="body", metadata={})
        self._run_issues_mode(fr)
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        payload = self._load_raw(item_id)
        self.assertEqual(payload["fetch_status"], "ok")


class TestFileModeStatusBranching(_IngesterTestBase):
    """file 모드 — inbox.md 블록별 status 분기."""

    def _write_inbox(self, url: str = "https://example.com/a") -> None:
        block = f"---\nurl: {url}\ncaptured_at: 2026-04-23T07:30:00+00:00\n---\n"
        (self.tmp / "inbox.md").write_text(block, encoding="utf-8")

    def _run_file_mode(self, fetch_result) -> None:
        with patch.object(self.ingester.fetchers, "dispatch", return_value=fetch_result):
            self.ingester._run_file_mode(dry_run=False)

    def test_ok_saves_raw_and_removes_block_from_inbox(self) -> None:
        self._write_inbox()
        fr = self._fetch_result(status="ok", title="t", text="b", metadata={})
        self._run_file_mode(fr)

        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertTrue(self._raw_path(item_id).exists())
        self.assertEqual(
            (self.tmp / "inbox.md").read_text(encoding="utf-8").strip(), ""
        )
        self.assertFalse((self.tmp / "inbox-failed.md").exists())

    def test_failed_moves_block_to_inbox_failed(self) -> None:
        self._write_inbox()
        fr = self._fetch_result(status="failed", error="boom")
        self._run_file_mode(fr)

        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        self.assertEqual(
            (self.tmp / "inbox.md").read_text(encoding="utf-8").strip(), ""
        )
        failed_text = (self.tmp / "inbox-failed.md").read_text(encoding="utf-8")
        self.assertIn("https://example.com/a", failed_text)

    def test_login_required_moves_to_inbox_failed(self) -> None:
        self._write_inbox()
        fr = self._fetch_result(status="login_required", error="403 private")
        self._run_file_mode(fr)

        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        self.assertIn(
            "https://example.com/a",
            (self.tmp / "inbox-failed.md").read_text(encoding="utf-8"),
        )

    def test_no_transcript_saves_with_fetch_status(self) -> None:
        self._write_inbox()
        fr = self._fetch_result(
            status="no_transcript",
            title="video",
            text="desc",
            metadata={"has_transcript": False},
        )
        self._run_file_mode(fr)

        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        payload = self._load_raw(item_id)
        self.assertEqual(payload["fetch_status"], "no_transcript")
        self.assertFalse((self.tmp / "inbox-failed.md").exists())


class TestEmptyPayloadGuard(_IngesterTestBase):
    """status 가 save 화이트리스트에 속해도 title/text 가 전부 비면 저장 거부.

    2026-04-23 YouTube transient 실패 → fetch_status=no_transcript + 빈 payload
    → classifier 환각 사건 회귀 방지.
    """

    def _issue(self, number: int = 1, url: str = "https://example.com/a"):
        from lib.github_inbox import InboxIssue

        return InboxIssue(
            number=number,
            url=url,
            user_caption="",
            created_at="2026-04-23T07:30:00Z",
        )

    def _run_issues_mode(self, fetch_result, issues=None):
        if issues is None:
            issues = [self._issue()]
        gi = MagicMock()
        gi.list_open_inbox_issues.return_value = issues
        with patch.object(self.ingester, "github_inbox", gi), \
             patch.object(self.ingester.fetchers, "dispatch", return_value=fetch_result):
            self.ingester._run_issues_mode(dry_run=False)
        return gi

    def test_issues_mode_rejects_empty_no_transcript_payload(self) -> None:
        """no_transcript + 빈 title·text 면 저장 없이 label_issue_failed."""
        fr = self._fetch_result(
            status="no_transcript",
            title="",
            text="",
            metadata={"video_id": "abcdefghijk", "has_transcript": False},
        )
        gi = self._run_issues_mode(fr)
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        gi.label_issue_failed.assert_called_once()
        reason = gi.label_issue_failed.call_args[0][1]
        self.assertIn("empty payload", reason)
        self.assertIn("no_transcript", reason)
        gi.close_issue.assert_not_called()

    def test_issues_mode_rejects_empty_ok_payload(self) -> None:
        """status=ok 이라도 title/text/user_caption 모두 비면 저장 거부."""
        fr = self._fetch_result(status="ok", title="", text="", metadata={})
        gi = self._run_issues_mode(fr)
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        gi.label_issue_failed.assert_called_once()

    def test_issues_mode_keeps_payload_when_user_caption_present(self) -> None:
        """title·text 가 비어도 user_caption 이 있으면 분류 신호가 있으므로 저장."""
        from lib.github_inbox import InboxIssue

        fr = self._fetch_result(status="ok", title="", text="", metadata={})
        issue = InboxIssue(
            number=2,
            url="https://example.com/b",
            user_caption="매우 흥미로운 UX 실험",
            created_at="2026-04-23T07:30:00Z",
        )
        self._run_issues_mode(fr, issues=[issue])
        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/b")
        self.assertTrue(self._raw_path(item_id).exists())
        payload = self._load_raw(item_id)
        self.assertEqual(payload["extracted"].get("user_caption"), "매우 흥미로운 UX 실험")

    def test_file_mode_rejects_empty_payload_to_inbox_failed(self) -> None:
        inbox = self.tmp / "inbox.md"
        inbox.write_text(
            "---\nurl: https://example.com/a\ncaptured_at: 2026-04-23T07:30:00+00:00\n---\n",
            encoding="utf-8",
        )
        fr = self._fetch_result(
            status="no_transcript",
            title="",
            text="",
            metadata={"has_transcript": False},
            error="metadata: boom; transcript: no_transcript",
        )
        with patch.object(self.ingester.fetchers, "dispatch", return_value=fr):
            self.ingester._run_file_mode(dry_run=False)

        from lib.wiki_io import url_hash
        item_id = url_hash("https://example.com/a")
        self.assertFalse(self._raw_path(item_id).exists())
        failed_text = (self.tmp / "inbox-failed.md").read_text(encoding="utf-8")
        self.assertIn("https://example.com/a", failed_text)


class TestFailReasonHelper(_IngesterTestBase):
    """_fail_reason 단위 — 메시지 포맷 회귀."""

    def test_login_required_uses_error_when_present(self) -> None:
        fr = self._fetch_result(status="login_required", error="oEmbed 403")
        self.assertEqual(self.ingester._fail_reason(fr), "oEmbed 403")

    def test_login_required_falls_back_to_default(self) -> None:
        fr = self._fetch_result(status="login_required")
        self.assertEqual(self.ingester._fail_reason(fr), "login required")

    def test_failed_uses_error(self) -> None:
        fr = self._fetch_result(status="failed", error="timeout")
        self.assertEqual(self.ingester._fail_reason(fr), "timeout")

    def test_failed_falls_back_to_status(self) -> None:
        fr = self._fetch_result(status="failed")
        self.assertIn("failed", self.ingester._fail_reason(fr))


if __name__ == "__main__":
    unittest.main()
