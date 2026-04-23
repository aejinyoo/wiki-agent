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
- 상태: 구현 중 (9/10 완료)
- 업데이트: 2026-04-23
- 다음: Task 2 통합 스모크(실 YouTube URL 자막 O/X) → Task 5 `transcript_cleanup` 에이전트 → Shortcut OCR 전환 후 user_caption 실데이터 검증

### brief-prompt-improvements
- 파일: `docs/features/brief-prompt-improvements.md`
- 상태: 구현 완료 · 검증 대기
- 업데이트: 2026-04-23
- 다음: 로컬에서 `--dry-run --force` 프리뷰 확인 → 내일 07:30 KST 결과 검증

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
