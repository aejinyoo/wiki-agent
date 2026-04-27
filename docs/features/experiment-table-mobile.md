# experiment-table-mobile

**상태**: 구현 완료 · **업데이트**: 2026-04-27

## 요약
wiki-site daily 브리프의 "🧪 오늘 해볼 만한 실험" 테이블이 모바일에서 컬럼이 너무 좁아 가독성이 떨어지는 문제 해결. 해당 섹션의 markdown table 만 `<td data-label="...">` 구조로 변환하고, 모바일(≤640px)에서는 행을 카드로 쌓고 헤더를 라벨로 표시.

## 진행
### 2026-04-27
- `wiki-site/src/lib/daily-content.ts`
  - `transformExperimentTable(body)` 추가. H2 heading 이 🧪 또는 "실험" 을 포함하는 섹션의 markdown table 만 raw HTML `<table class="experiment-table">` 으로 재작성. 각 `<td>` 에 `data-label` 로 컬럼 헤더 텍스트 부여.
  - `clean()` 파이프라인에 `transformExperimentTable` 연결 (`stripOuterCodeFence → stripWeeklySection → transformExperimentTable`).
  - 셀 내 inline markdown(링크/강조)은 `marked.parseInline` 으로 보존.
- `wiki-site/src/styles/global.css`
  - `@media (max-width: 640px)` 에서 `.prose table.experiment-table` 만 `display: block` 으로 카드화. `thead` 는 시각 숨김(스크린리더용 유지). `td::before { content: attr(data-label) }` 로 라벨 표시.
  - 데스크톱은 기존 `.prose table` 스타일 그대로 유지.
- 검증: `daily/2026-04-{20,23,26,27}.md` 4개 파일에 대해 변환 결과 확인. 구포맷(`실험|난이도|예상 시간|관련 아이템`)·신포맷(`난이도|ETA|제목|해볼 것`) 모두 컬럼 헤더가 `data-label` 로 들어가서, 큐레이터가 컬럼 순서를 바꿔도 모바일 라벨이 자동으로 따라옴.

## 결정
### 2026-04-27: 적용 범위 — 실험 섹션 한정
- 맥락: `.prose table` 전체에 적용할지 / 실험 섹션만 한정할지 선택.
- 선택: 실험 섹션 한정. heading 정규식(`/^##\s+.*(?:🧪|실험)/`)으로 게이팅.
- 이유: 다른 테이블이 향후 추가될 때 의도치 않은 카드화 방지. 변환 비용도 최소.

### 2026-04-27: List 스타일 — 라벨 포함 카드
- 맥락: (a) 라벨 포함 카드, (b) 타이틀 강조 카드 두 안 중 선택.
- 선택: (a) 라벨 포함 카드.
- 이유: 큐레이터가 컬럼 순서/이름을 바꿔도 (예: 4/23 까지는 `실험|난이도|예상 시간|관련 아이템`, 4/26 부터 `난이도|ETA|제목|해볼 것`) 라벨이 헤더에서 자동으로 따라옴. 위치 기반 추정(타이틀 강조)은 포맷 변경에 취약.

## 다음
- [ ] (선택) 단위 테스트 추가 — `transformExperimentTable` 의 heading 매칭, divider 검출, 비실험 H2 무시 케이스. 현재 4개 daily 로 e2e 확인했지만 회귀 방지엔 단위 테스트가 안전.
