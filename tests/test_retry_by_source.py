"""scripts/retry.py by-source 테스트.

각 케이스마다 임시 WIKI_REPO 를 만들어 retry.py 를 서브프로세스로 실행.
이렇게 하면 paths.py 가 테스트마다 새로 로드돼 env-based 경로 상태가 오염되지 않는다.

project 의존성(pyyaml, python-frontmatter, python-dotenv) 이 sys.executable 의
환경에 있어야 한다. uv/venv 에서 `python -m unittest tests.test_retry_by_source`
로 돌리면 됨.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RETRY = _ROOT / "scripts" / "retry.py"


class _FakeRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wa-retry-test-"))
        (self.tmp / "raw").mkdir()
        (self.tmp / "raw-archive").mkdir()
        (self.tmp / "wiki").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self) -> dict:
        e = dict(os.environ)
        e["WIKI_REPO_PATH"] = str(self.tmp)
        return e

    def _write_index(self, items: dict) -> None:
        (self.tmp / "_index.json").write_text(
            json.dumps({"version": 1, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_RETRY), *args],
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestBySourceSanity(_FakeRepoCase):
    def test_invalid_source_rejects_with_nonzero_exit(self) -> None:
        self._write_index({})
        result = self._run("by-source", "Bogus")
        self.assertNotEqual(result.returncode, 0)
        combined = result.stderr + result.stdout
        self.assertIn("알 수 없는 source", combined)

    def test_empty_match_prints_no_target(self) -> None:
        # 인덱스는 있지만 해당 source 엔트리가 없을 때
        self._write_index(
            {"xyz789": {"url": "https://youtu.be/abc", "source": "YouTube"}}
        )
        result = self._run("by-source", "Threads")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("대상 없음", result.stdout)

    def test_no_index_file_prints_no_target(self) -> None:
        # _index.json 자체가 없는 프레시 레포
        result = self._run("by-source", "Instagram")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("대상 없음", result.stdout)


class TestBySourceDryRun(_FakeRepoCase):
    def test_dry_run_lists_targets_and_changes_nothing(self) -> None:
        self._write_index(
            {
                "aaa000": {
                    "url": "https://instagram.com/p/AA/",
                    "source": "Instagram",
                    "title": "샘플 IG 1",
                },
                "bbb111": {
                    "url": "https://instagram.com/reel/BB/",
                    "source": "Instagram",
                    "title": "샘플 IG 2",
                },
                "ccc222": {
                    "url": "https://youtu.be/xyz",
                    "source": "YouTube",
                    "title": "YT 는 제외돼야 함",
                },
            }
        )
        (self.tmp / "raw" / "aaa000.json").write_text("{}", encoding="utf-8")

        result = self._run("by-source", "Instagram")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertIn("aaa000", result.stdout)
        self.assertIn("bbb111", result.stdout)
        self.assertNotIn("ccc222", result.stdout)  # 다른 source 는 섞이면 안 됨

        # 실제로는 아무 것도 안 지워졌어야 함
        self.assertTrue((self.tmp / "raw" / "aaa000.json").exists())
        idx = json.loads((self.tmp / "_index.json").read_text(encoding="utf-8"))
        self.assertIn("aaa000", idx["items"])
        self.assertIn("bbb111", idx["items"])


class TestBySourceApply(_FakeRepoCase):
    def test_apply_delete_only_removes_files_and_recomputes(self) -> None:
        self._write_index(
            {
                "aaa000": {
                    "url": "https://instagram.com/p/AA/",
                    "source": "Instagram",
                },
                "ccc222": {
                    "url": "https://youtu.be/xyz",
                    "source": "YouTube",
                },
            }
        )
        (self.tmp / "raw" / "aaa000.json").write_text("{}", encoding="utf-8")
        (self.tmp / "raw" / "ccc222.json").write_text("{}", encoding="utf-8")

        result = self._run(
            "by-source", "Instagram", "--apply", "--delete-only"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # Instagram 엔트리·파일 사라짐
        idx = json.loads((self.tmp / "_index.json").read_text(encoding="utf-8"))
        self.assertNotIn("aaa000", idx["items"])
        self.assertFalse((self.tmp / "raw" / "aaa000.json").exists())

        # 다른 source 는 그대로
        self.assertIn("ccc222", idx["items"])
        self.assertTrue((self.tmp / "raw" / "ccc222.json").exists())

        # _stats.json 이 생겼어야 함 (recompute 호출됨)
        self.assertTrue((self.tmp / "_stats.json").exists())

        # 요약 출력 확인
        self.assertIn("삭제됨: 1개", result.stdout)


if __name__ == "__main__":
    unittest.main()
