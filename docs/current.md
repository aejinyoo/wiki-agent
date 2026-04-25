# 현재 진행 중인 작업

> 새 세션 시작할 때 가장 먼저 읽는 파일.
> 진행 중 기능들의 포인터만 모아둠. 상세는 각 폴더의 `progress.md` 참조.

## 진행 중

<!--
예시:
### <기능명>
- 파일: `docs/features/<기능명>.md`
- 상태: 구현 중 / 리뷰 대기 / 막힘
- 업데이트: 2026-04-21
- 다음: (한 줄)
-->

### sns-fetchers
- 파일: `docs/features/sns-fetchers.md`
- 상태: 구현 완료 · 검증 대기 (+ 2026-04-24 transient 방어 Task A~E 반영)
- 업데이트: 2026-04-24
- 다음: 실 YouTube URL 로 ingester→transcript_cleanup→classifier 한 바퀴 통합 스모크 (raw JSON 에 text/text_cleaned/cleaned 쌓이는지, classifier 가 정제본 소비하는지) → Shortcut OCR 전환 후 user_caption 실데이터 검증. 오염된 `pnJOd5H5Zsc` 영상은 사용자 재공유 시 자동 재수집됨

### brief-prompt-improvements
- 파일: `docs/features/brief-prompt-improvements.md`
- 상태: 사고 발생 · 후속 작업으로 이관 (`brief-llm-decoupling`)
- 업데이트: 2026-04-25
- 비고: 4/24 brief partial 잘림 + 4/25 빈 응답. root cause = thinking budget 압박. 정량 룰을 코드로 옮겨 해결 예정

### brief-llm-decoupling
- 파일: `docs/features/brief-llm-decoupling.md`
- 상태: Step 3 완료 (3/7)
- 업데이트: 2026-04-25
- 다음: Step 4+5 (프롬프트 압축 + 메인 흐름 리팩터링) — 한 커밋으로 묶어 nightly 호환성 유지

## 다음 예정 (착수 전)

> sns-fetchers 마무리 후 아래 순서대로. 각 항목 상세는 feature 문서 참조.
> 착수 순서: `sns-fetchers(Task 2) → list-thumbnails+md-images 병합 → demo-repo → map-url-sharing`

### list-thumbnails (+ md-images 병합 구현)
- 파일: `docs/features/list-thumbnails.md`, `docs/features/md-images.md`
- 상태: 계획 (정책 확정)
- 업데이트: 2026-04-21
- 다음: T1 `lib/fetchers/og.py` OG 추출 util 공용화 (IG `_try_parse_og` 이식)
- 예상 소요: **6~9h (≈ 1 day)** — 한 브랜치에서 썸네일 + 본문 인라인 같이
- 확정 원칙: 이미지는 원격 URL 직접 참조만 (바이너리 저장 X) · aspect-ratio 16/9 고정 · LLM 경로 격리 (토큰 증분 0) · 로드 실패 = CSS fallback 아이콘
- 소스별 정책: generic 만 `body_images` 수집, IG/X/YouTube/Map 은 썸네일 1장만

### demo-repo
- 파일: `docs/features/demo-repo.md`
- 상태: 계획
- 업데이트: 2026-04-21
- 다음: 위 썸네일·인라인 완료 후 착수 — D1 데모화 체크리스트 (secret/하드코딩 값 식별)
- 예상 소요: **8.5~13h (≈ 1.5~2 day)**
- 선행: `sns-fetchers` Task 2 + list-thumbnails 병합 (공유 시점에 썸네일 붙은 상태가 첫인상 유리)

### map-url-sharing
- 파일: `docs/features/map-url-sharing.md`
- 상태: 계획
- 업데이트: 2026-04-21
- 다음: M1 naver/google maps URL 패턴·응답 구조 조사
- 예상 소요: **5.5~8h (≈ 1 day)**
- 비고: 실사용 독립적이라 위 순서와 무관하게 끼워넣기 가능

## 최근 완료

<!--
예시:
- 2026-04-15: <기능명> — archive로 이동
-->
