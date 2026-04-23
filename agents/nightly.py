#!/usr/bin/env python3
"""Nightly — 야간/아침 배치 오케스트레이터.

launchd 매일 07:30. 순차적으로:
  1) Ingester           — inbox.md → raw/
  2) Transcript Cleanup — raw/ 의 YouTube 자막을 prose 로 정제 (text_cleaned 추가)
  3) Classifier         — raw/ → wiki/{category}/*.md
  4) Curator            — 조건 맞으면 (아이템≥50, 마지막 실행 ≥7일 전) 자동 실행
  5) Daily Brief        — 오늘 브리프 + 누락 catch-up
  6) Cleanup            — 조건 맞으면 (마지막 실행 ≥30일 전) raw-archive 만료 파일 정리

각 단계는 실패해도 다음 단계가 돌 수 있도록 예외 격리.
"""

from __future__ import annotations

import argparse
import logging

import _bootstrap

_bootstrap.setup(__file__)

log = logging.getLogger("nightly")


def _step(name: str, fn, **kwargs) -> None:
    log.info("── [%s] 시작", name)
    try:
        fn(**kwargs)
        log.info("── [%s] 완료", name)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log.exception("── [%s] 실패 — 다음 단계 계속 진행", name)


def run(
    dry_run: bool = False,
    force_curator: bool = False,
    force_cleanup: bool = False,
) -> None:
    from agents import (  # noqa: WPS433
        ingester,
        transcript_cleanup,
        classifier,
        curator,
        daily_brief,
        cleanup,
    )

    _step("ingester", ingester.run, dry_run=dry_run)
    _step("transcript_cleanup", transcript_cleanup.run, dry_run=dry_run)
    _step("classifier", classifier.run, dry_run=dry_run)

    if curator.is_due(force=force_curator):
        _step("curator", curator.run, force=force_curator)
    else:
        log.info("── [curator] 조건 불충족, 스킵")

    _step("daily_brief", daily_brief.run, dry_run=dry_run, catchup=True)

    if cleanup.is_due(force=force_cleanup):
        _step("cleanup", cleanup.run, dry_run=dry_run, force=force_cleanup)
    else:
        log.info("── [cleanup] 조건 불충족, 스킵 (30일 주기)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-curator", action="store_true",
                    help="Curator를 조건 무관하게 강제 실행")
    ap.add_argument("--force-cleanup", action="store_true",
                    help="Cleanup을 30일 주기 무관하게 강제 실행")
    args = ap.parse_args()
    run(
        dry_run=args.dry_run,
        force_curator=args.force_curator,
        force_cleanup=args.force_cleanup,
    )


if __name__ == "__main__":
    main()
