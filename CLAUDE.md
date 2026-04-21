# CLAUDE.md

> Claude Code가 세션 시작 시 자동으로 읽는 파일. 프로젝트 개요 + 작업 규칙.

## 프로젝트

AI 디자인 개인 위키 에이전트. `wiki` repo 대상으로 Ingester·Classifier·Curator·Daily Brief 4개 에이전트 구동. LLM은 Google Gemini (Flash-Lite + Pro, 무료 tier). GitHub Actions cron으로 매일 07:30 KST 실행. 상세는 `README.md` 참조.

**레포 3개 구성**: [wiki](https://github.com/aejinyoo/wiki)(데이터) · **wiki-agent**(로직, 이 레포) · [wiki-site](https://github.com/aejinyoo/wiki-site)(뷰).

## 세션 이어짐 규칙

새 세션 시작하면 **항상 먼저** 다음을 실행할 것:

1. `docs/current.md` 읽고 진행 중인 작업 파악
2. 사용자 요청이 특정 기능에 해당하면 `docs/features/<기능명>.md` 읽고 마지막 상태부터 이어감

세션을 끝내거나 큰 작업 단락이 지어지면 **묻지 말고 자동으로**:

1. `docs/features/<기능명>.md` 의 **진행** 섹션에 오늘 한 일 추가 (날짜, 커밋 해시 포함)
2. **다음** 체크박스 상태 갱신
3. 상태 변화 있으면 `docs/current.md` 의 해당 항목 업데이트 (업데이트 날짜, 다음 액션)
4. 큰 방향 전환·트레이드오프가 있었으면 **결정** 섹션에 메모 추가

새 기능 작업을 시작할 때:

1. `docs/features/_template.md` 를 `docs/features/<kebab-case-name>.md` 로 복사
2. `docs/current.md` 에 항목 등록

## 폴더

```
wiki-agent/
├── agents/       4개 에이전트 + nightly 오케스트레이터
├── lib/          공유 유틸 (fetchers/, wiki_io 등)
├── prompts/      LLM 프롬프트
├── scripts/      CLI (retry.py 등)
├── tests/        pytest
├── docs/         작업 로그 (이 레포의 기억)
└── launchd/      로컬 스케줄러 (참고용, 운영은 GH Actions)
```

## 컨벤션

- Python 3.11+, `uv` 관리 (`pyproject.toml`)
- 포매터/린터는 `pyproject.toml` 설정 따름
- 커밋 메시지는 짧고 명령형 (`add X`, `fix Y`, `refactor Z`)
- 파이프라인 정책: 각 단계 실패해도 다음 단계 계속 실행 (nightly)
- Gemini 무료 tier 쿼터 가드 유지 (`DAILY_TOKEN_CAP_*`)

## 참고

- 기획서: [wiki · design-wiki-agent-plan.md](https://github.com/aejinyoo/wiki/blob/master/design-wiki-agent-plan.md)
- 작업 로그 사용법: `docs/README.md`
