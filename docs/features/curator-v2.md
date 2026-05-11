# curator-v2

**상태**: 계획 · **업데이트**: 2026-05-11

## 요약

큐레이터를 v1 (stats + `_personal_context.md` 재생성) 에서 v2 (LLM 기반 태그·카테고리 정리 제안) 로 확장. **MVP 는 dry-run 모드** — Gemini Pro 호출해서 `prompts/curator.md` 의 JSON 제안을 받고 `_changelog/YYYY-MM-DD.md` 에 사람이 읽을 수 있는 보고서로 기록만 함. 실제 파일 이동·rename 은 1~2주 dry-run 결과 검토 후 별도 PR 에서 auto-apply 활성화.

## 배경

- 위키 68건 도달 → 큐레이터 갯수 조건 충족
- 현재 분포 편향: `generative-tools 33, trend-reports 25, ai-ux-patterns 5, agent-interaction 3, prompt-ui 2, design-system-automation 0`
- `trend-reports` 가 protected 인데 비-디자인 콘텐츠 (음식·라이프스타일) 가 쌓임 → curator 가 분리 제안할 수 있도록 protected 해제 필요
- 기획서 5.5 의 5개 액션 중 **auto-apply 는 Phase 1+2 (태그 정규화 + 재분류) 만 우선**. Phase 3 (신설·병합·분할·삭제) 는 LLM 이 제안하면 dry-run 보고서에 기록만 함.

## 결정

### 2026-05-11: dry-run 우선 + 출력은 모든 액션 / 미래 auto-apply 는 Phase 1+2 부터
- 맥락: 한 번에 모든 액션 켜기엔 파일 이동·rename 위험 큼
- 선택: 1차는 dry-run 만 (안전). LLM 은 5개 액션 전부 제안할 수 있으나 적용은 0건.
- 이유: 1~2주간 어떤 제안이 나오는지 보고 신뢰 쌓은 뒤 Phase 1 → 2 → 3 단계적 활성화

### 2026-05-11: `_meta.yaml` 의 `protected: [trend-reports]` 해제
- 맥락: trend-reports 가 사실상 미분류 폴더처럼 운영됨 (음식·라이프스타일 다수)
- 선택: protected 에서 빼고 curator 가 새 카테고리 (`food`, `lifestyle` 등) 분할 제안하도록
- 이유: dry-run 이라 즉시 변경은 없음. 제안만 받고 사람이 검토.

## 진행

(아직 시작 전)

## 다음

- [ ] **T1** `agents/curator.py` 에 LLM 호출·파싱·dry-run 보고서 작성 로직 추가
  - `_build_user_prompt()` — 카테고리별 아이템 수 / 전체 태그 빈도 / 최근 추가분 샘플 / protected / 가드레일 패킹
  - `_call_curator_llm()` — `lib/llm.call_sonnet` 호출, 시스템 프롬프트는 `prompts/curator.md`
  - `_parse_proposal()` — JSON 한 덩어리 추출 + 스키마 검증 (방어적: code fence·앞뒤 텍스트 제거)
  - `_evaluate_proposal()` — 가드레일 적용: 영향 >100 → `approval_required` 로 이동, protected 카테고리 포함 시 제거, cooldown 검사
  - `_write_dry_run_report()` — `_changelog/YYYY-MM-DD.md` 에 사람이 읽을 수 있는 마크다운 (제안·승인 필요·스킵 사유 섹션)
- [ ] **T2** `_personal_context.md` 재생성은 dry-run 에도 그대로 (v1 동작 유지)
- [ ] **T3** `_curator_state.json` (또는 `_changelog/` 파일 파싱) 으로 카테고리별 마지막 변경일 추적 → cooldown
- [ ] **T4** `tests/test_curator.py`
  - 영향 >100 제안이 `approval_required` 로 이동되는지
  - protected 카테고리 (`_meta.yaml` 기반) 가 모든 액션에서 제거되는지
  - 잡음 섞인 LLM 응답 (```json ... ``` fence, 앞뒤 텍스트) 에서 JSON 추출
  - 가짜 위키로 통합 (mocked `call_sonnet`)
- [ ] **T5** wiki 레포 `_meta.yaml` 수정: `protected: []` (별도 커밋)
- [ ] **T6** nightly 에서 결과 확인 — `--force-curator` 로 강제 실행해서 첫 보고서 받아보고 LLM 제안 품질 평가
- [ ] **T7** docs/current.md 의 sns-fetchers 끝나는 대로 본 작업 정식 착수 (현재는 sns-fetchers 가 진행 중)

## 구현 노트

**프롬프트 입력 크기**: 68건 × ~150토큰 frontmatter 발췌 = ~10k 토큰 예상. 기획서 5-8k 보다 약간 큼. 필요 시 reclassification 입력에서 본문은 빼고 title+tags 만.

**Item ID**: `prompts/curator.md` 가 `item_id` 사용. wiki 파일은 `id` frontmatter 가 있음 (classifier 가 생성). 파일경로 ↔ id 매핑 dict 를 prompt 입력과 평가 단계에 둘 다 만들어 둠.

**Impact 계산**:
- `tag_renames`: `from` 태그 포함 아이템 수
- `reclassifications`: 1 (per item)
- `new_categories`: `seed_items` 수
- `category_changes` merge/split/delete: 대상 카테고리의 전체 아이템 수
- `duplicate_merges`: `remove` 길이

**Cooldown**: v1 dry-run 에선 변경 자체가 0 이라 cooldown 위반이 발생 안 함. 하지만 코드에 미리 넣어두면 auto-apply 켤 때 그대로 동작.

**롤백**: dry-run 이라 롤백 대상 없음. 보고서가 마음에 안 들면 그냥 무시.

## 다음 정식 착수 시점

`docs/current.md` 의 `sns-fetchers` 가 진행 중이라 그 다음. 단, **본 feature 의 T5 (`_meta.yaml` protected 해제)** 와 **첫 dry-run 실험 (T6 force run)** 은 sns-fetchers 와 무관하게 지금 가능 — 사용자가 우선순위 결정.
