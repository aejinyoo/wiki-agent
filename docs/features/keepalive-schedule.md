# keepalive-schedule

**상태**: 구현 완료 · **업데이트**: 2026-07-19

## 요약
GH Actions 스케줄 워크플로우가 60일간 repo 활동 없으면 자동 비활성화되는 함정 방지. nightly 가 wiki repo 에만 커밋하고 wiki-agent 엔 커밋하지 않아 걸림.

## 진행
### 2026-07-19
- **원인 규명**: `WIKI_REPO_TOKEN`(fine-grained PAT) 만료로 7/11 부터 nightly 중단. 그 사이 wiki-agent repo 에 커밋 활동이 없어 60일 비활성 타이머가 계속 카운트 → 스케줄 자동 비활성화 위험.
- **조치 1**: `WIKI_REPO_TOKEN` 을 만료 없음(no expiration)으로 재발급.
- **조치 2**: `nightly.yml` 에 keepalive 스텝 추가 (커밋 `e3af99d`).
  - "Trigger wiki-site rebuild" 와 "Upload logs on failure" 사이에 위치
  - `if: always() && inputs.dry_run != true` — 파이프라인 앞단계 실패해도 keepalive 는 실행 (활동 유지가 목적)
  - `.keepalive/last-run` 에 UTC 타임스탬프 기록, 변경 있으면 커밋 후 push
  - **반드시 PAT(WIKI_REPO_TOKEN)로 push** — 기본 `GITHUB_TOKEN` 이 만든 커밋은 60일 비활성 타이머를 리셋하지 못함
  - author 는 기존 커밋 스텝과 동일하게 `wiki-agent[bot]`

## 다음
- [ ] 7/20 07:30 KST nightly 실행 후 wiki-agent 에 `chore: keepalive ...` 커밋이 PAT author 로 찍혔는지 확인
- [ ] `WIKI_REPO_TOKEN` 이 wiki-agent repo 에 **Contents: Read and write** 권한 갖는지 재확인 (권한 없으면 keepalive push 실패)

## 결정
### 2026-07-19: keepalive 를 별도 스텝 + PAT push 로
- 맥락: nightly 의 실질 산출물은 모두 wiki repo 로 가고 wiki-agent 는 로직만 담아 커밋이 안 생김.
- 선택: 데이터가 아닌 타임스탬프 파일(`.keepalive/last-run`)만 wiki-agent 에 커밋. 실패해도 도는 `if: always()`.
- 이유: GH 의 60일 자동 비활성화는 "repo 활동"이 기준이며, **`GITHUB_TOKEN` 커밋은 활동으로 카운트되지 않음**. PAT push 만 타이머를 리셋함.
