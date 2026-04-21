# demo-repo

**상태**: 계획 · **업데이트**: 2026-04-21

## 요약
`wiki` / `wiki-agent` / `wiki-site` 3-레포 구조를 그대로 복제한 공개 데모 레포 세트. 다른 사람이 fork/clone 해서 자기 "개인 AI 위키"를 돌릴 수 있도록 **동작하는 최소 템플릿 + 사용 가이드**를 제공. sns-fetchers (특히 Task 2 YouTube) 완료가 선행 조건 — 불완전한 파이프라인을 공유하면 "이슈 접수소" 가 열림.

**스코프**:
- `wiki-demo` (데이터 템플릿) — 빈 `_index.json`·`_meta.yaml`·카테고리 폴더 구조, 예시 md 2~3개
- `wiki-agent-demo` (로직 복제) — secrets/토큰 관련 값 플레이스홀더화, 개인 personal_context 제거, GH Actions workflow 그대로
- `wiki-site-demo` (뷰 복제) — 브랜딩·카피만 일반화, 로컬 데모용 sample content 포함
- README 3종 상호참조 업데이트 + **setup 가이드 1종** (상위 레벨)

**Out of scope**:
- 원클릭 배포 (Vercel/Netlify 버튼) — 초기엔 수동 가이드만
- SaaS 버전 / 멀티테넌트
- Gemini API 키 대행 발급

## 진행

_(착수 전)_

## 다음
- [ ] Task 2 (YouTube 자막 고도화) 완료 대기
- [ ] **D1** 데모화 체크리스트 작성 — 어떤 값이 secret/개인정보인지 식별 (개인 personal_context, 내 GH username, workflow의 `aejinyoo` 하드코딩 등)
- [ ] **D2** `wiki-demo` 레포 생성 — `_meta.yaml` 템플릿 값, 카테고리 6개 빈 폴더, 예시 md 2~3개 (라이선스 clean 콘텐츠)
- [ ] **D3** `wiki-agent-demo` 레포 생성 — hardcoded 값을 env 또는 `_meta.yaml` 참조로 교체
- [ ] **D4** `wiki-site-demo` 레포 생성 — 개인 브랜딩 제거, 카피 일반화
- [ ] **D5** `SETUP.md` 작성 — GH 토큰 발급, Gemini API 키, secrets 등록, Actions 첫 실행, iOS Shortcut 설정 순서
- [ ] **D6** 드라이런 — 내 계정과 분리된 테스트 계정으로 fork → 처음부터 끝까지 한 사이클 (이슈 등록 → cron → 분류 → 사이트 빌드) 검증

## 결정 (옵션)

_(아직 결정사항 없음 — 착수 시 기록)_

## 링크
- 선행: `docs/features/sns-fetchers.md` Task 2
- 참고: 각 레포의 현재 README 3종 (`wiki`, `wiki-agent`, `wiki-site`)
