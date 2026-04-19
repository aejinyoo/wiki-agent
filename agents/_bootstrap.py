"""에이전트 스크립트용 공통 부트스트랩.

- AGENT_HOME을 sys.path에 추가 (lib/ import 가능하게)
- 로깅 설정
- launchd에서 호출될 때 작업 디렉토리 보정
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def setup(script_file: str) -> None:
    # sys.path — AGENT_HOME(이 파일의 상위 상위)을 루트로
    here = Path(script_file).resolve()
    agent_home = here.parent.parent
    if str(agent_home) not in sys.path:
        sys.path.insert(0, str(agent_home))

    # 로깅
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 런타임 디렉토리 보장
    from lib import paths  # noqa: WPS433 (늦은 import 의도)
    paths.ensure_dirs()
