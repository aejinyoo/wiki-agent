#!/usr/bin/env python3
"""Gemini API 키·모델 연결 확인용 핑 스크립트.

사용:
    uv run scripts/ping_gemini.py

성공 시: 두 모델(flash-lite, pro)에서 각각 1토큰짜리 응답 + 사용량 출력.
실패 시: 에러 메시지 + 종료코드 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

# repo 루트를 sys.path 에 추가 (uv run 은 자동이지만 python3 직접 실행도 지원)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# WIKI_REPO_PATH 가 없어도 ping 만 하고 싶은 경우 임시로 채워준다.
import os
os.environ.setdefault("WIKI_REPO_PATH", str(ROOT / ".ping_tmp"))
(ROOT / ".ping_tmp").mkdir(exist_ok=True)

from lib import llm, paths  # noqa: E402


def ping(kind: str, model: str) -> None:
    print(f"▶ {kind} ({model}) …", end=" ", flush=True)
    try:
        if kind == "haiku":
            r = llm.call_haiku(
                system="You reply with exactly one short word.",
                user="Say: pong",
                max_tokens=8,
                temperature=0.0,
            )
        else:
            r = llm.call_sonnet(
                system="You reply with exactly one short word.",
                user="Say: pong",
                max_tokens=8,
                temperature=0.0,
            )
        print(f"OK  text={r.text!r}  in={r.input_tokens} out={r.output_tokens}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL — {type(e).__name__}: {e}")
        sys.exit(1)


def main() -> None:
    print(f"MODEL_HAIKU  = {paths.MODEL_HAIKU}")
    print(f"MODEL_SONNET = {paths.MODEL_SONNET}")
    print()
    ping("haiku", paths.MODEL_HAIKU)
    ping("sonnet", paths.MODEL_SONNET)
    print("\n✅ Gemini 연결 OK — 실제 nightly 실행으로 넘어가도 됩니다.")


if __name__ == "__main__":
    main()
