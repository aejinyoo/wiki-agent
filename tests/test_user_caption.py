"""user_caption 검증 + classifier 프롬프트 통합 테스트.

회귀 방지 포커스:
- 글자수 제한 없음 (한 단어/해시태그도 통과)
- URL 형식은 무시 (클립보드 오염 차단)
- None/빈/공백은 무시
- classifier._build_user 는 user_caption 있을 때만 USER_CAPTION 라인 추가
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# agents/_bootstrap 을 찾을 수 있게 agents/ 를 sys.path에 추가
# (classifier 가 `import _bootstrap` 을 사용)
_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))

from lib.user_caption import validate_user_caption  # noqa: E402


class TestValidateUserCaption(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(validate_user_caption(None))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(validate_user_caption(""))

    def test_whitespace_only_returns_none(self) -> None:
        self.assertIsNone(validate_user_caption("   \n\t  "))

    def test_plain_text_passes(self) -> None:
        self.assertEqual(
            validate_user_caption("요즘 주목하는 디자이너"),
            "요즘 주목하는 디자이너",
        )

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(validate_user_caption("  hello  "), "hello")

    def test_single_word_passes(self) -> None:
        # **글자수 제한 없음** 회귀 방지 — 한 단어도 신호로 유효
        self.assertEqual(validate_user_caption("맛있어"), "맛있어")

    def test_hashtag_passes(self) -> None:
        self.assertEqual(validate_user_caption("#prompt-ui"), "#prompt-ui")

    def test_url_is_rejected(self) -> None:
        self.assertIsNone(
            validate_user_caption("https://example.com/some/path?x=1")
        )

    def test_http_url_is_rejected(self) -> None:
        self.assertIsNone(validate_user_caption("http://foo.bar"))

    def test_text_containing_url_passes(self) -> None:
        # 전체가 URL은 아니므로 통과 — 사용자 의도 있는 캡션
        self.assertEqual(
            validate_user_caption("이 링크 재미있음 https://x.com/abc"),
            "이 링크 재미있음 https://x.com/abc",
        )

    def test_scheme_without_netloc_passes(self) -> None:
        # "text:something" 같은 모호한 형태는 캡션으로 취급
        self.assertEqual(
            validate_user_caption("mailto:foo"),
            "mailto:foo",
        )


class TestRawSaveIncludesCaption(unittest.TestCase):
    """캡션 → raw/*.json 경로까지 흐름 확인.

    ingester 가 `extracted["user_caption"]` 에 값을 넣고 `save_raw` 를 호출할 때,
    raw 파일의 `extracted.user_caption` 으로 persist 되어야 classifier 가 읽을 수 있다.
    """

    def test_save_raw_persists_user_caption(self) -> None:
        import json
        import os
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="wa-caption-test-"))
        try:
            os.environ["WIKI_REPO_PATH"] = str(tmp)
            # paths 모듈은 import 시 env 를 읽으므로, 이미 로드된 경우 리로드 필요
            import importlib

            from lib import paths as _paths
            importlib.reload(_paths)
            from lib import wiki_io as _wiki_io
            importlib.reload(_wiki_io)

            (tmp / "raw").mkdir()
            item = _wiki_io.WikiItem(
                id="test01",
                url="https://www.instagram.com/p/XXX/",
                source="Instagram",
                captured_at="2026-04-21T07:30:00+00:00",
            )
            extracted = {
                "title": "Instagram 게시물",
                "text": "Instagram 원본 확인 필요",
                "user_caption": "타투 레퍼런스 #arm",
            }
            _wiki_io.save_raw(item, extracted)

            raw_json = json.loads(
                (tmp / "raw" / "test01.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                raw_json["extracted"]["user_caption"],
                "타투 레퍼런스 #arm",
            )
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestClassifierBuildUser(unittest.TestCase):
    """end-to-end 캡션 → raw → classifier 입력 흐름 확인 중 classifier 단계."""

    def _build_user(self, item_data: dict, extracted: dict) -> str:
        # 순환 import 회피: lazy import
        from agents.classifier import _build_user

        return _build_user(item_data, extracted)

    def _item(self) -> dict:
        return {
            "id": "abc123",
            "url": "https://www.instagram.com/reel/XXX/",
            "source": "Instagram",
            "captured_at": "2026-04-21T07:30:00+00:00",
        }

    def test_includes_caption_line_when_present(self) -> None:
        out = self._build_user(
            self._item(),
            {"title": "IG Reel", "text": "Instagram 원본 확인 필요", "user_caption": "타투 레퍼런스"},
        )
        self.assertIn("USER_CAPTION: 타투 레퍼런스", out)

    def test_omits_caption_line_when_absent(self) -> None:
        out = self._build_user(
            self._item(),
            {"title": "IG Reel", "text": "Instagram 원본 확인 필요"},
        )
        self.assertNotIn("USER_CAPTION", out)

    def test_omits_caption_line_when_empty(self) -> None:
        out = self._build_user(
            self._item(),
            {"title": "IG Reel", "text": "body", "user_caption": ""},
        )
        self.assertNotIn("USER_CAPTION", out)


if __name__ == "__main__":
    unittest.main()
