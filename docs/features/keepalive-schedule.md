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

- **버그 + 수정** (커밋 `7404afb`): 첫 수동 실행에서 keepalive push 가 403(`denied to github-actions[bot]`)으로 실패.
  - 원인: `actions/checkout@v4`(wiki-agent) 기본 `persist-credentials: true` 가 기본 GITHUB_TOKEN(contents:read)을 `http.https://github.com/.extraheader` 에 전역 저장 → URL 에 PAT 를 박아도 저장된 토큰 헤더가 우선 전송돼 덮임.
  - 수정: wiki-agent 체크아웃에 `persist-credentials: false` 추가. (nightly 배치는 wiki-agent 에 push 안 하므로 안전.)
- **검증 완료**: 재실행 후 keepalive ✓ + 전체 job ✓. remote 에 `ef7807c chore: keepalive 2026-07-19` 커밋 확인 (author `wiki-agent[bot]`, `.keepalive/last-run` = `2026-07-19T07:46:19Z`).
- 전제 확인됨: `WIKI_REPO_TOKEN` 이 wiki-agent repo 에 Contents: R/W 보유 (사용자 확인 + push 성공).

## 다음
- [ ] 특이사항 없음. 이후 매일 nightly 의 keepalive 스텝이 60일 타이머 자동 리셋.

## 결정
### 2026-07-19: keepalive 를 별도 스텝 + PAT push 로
- 맥락: nightly 의 실질 산출물은 모두 wiki repo 로 가고 wiki-agent 는 로직만 담아 커밋이 안 생김.
- 선택: 데이터가 아닌 타임스탬프 파일(`.keepalive/last-run`)만 wiki-agent 에 커밋. 실패해도 도는 `if: always()`.
- 이유: GH 의 60일 자동 비활성화는 "repo 활동"이 기준이며, **`GITHUB_TOKEN` 커밋은 활동으로 카운트되지 않음**. PAT push 만 타이머를 리셋함.
