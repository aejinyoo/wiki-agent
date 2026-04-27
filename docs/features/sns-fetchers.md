# sns-fetchers

**상태**: 구현 중 (10/10 + transient 방어) · **업데이트**: 2026-04-24

## 요약
SNS 공유 링크(X, Instagram, YouTube) 본문을 채널별 어댑터로 안정 수집하도록 ingester 파이프라인 재편. 기존 generic `requests + trafilatura`가 클라이언트 렌더링/로그인월에 막혀 로그인 페이지 HTML이 인덱스에 오염되던 문제 해결.

**스코프**: `lib/fetchers/` 모듈화 + FetchResult 공통 계약 · X oEmbed · IG placeholder · YouTube 자막 고도화 · transcript_cleanup 에이전트 신설 · URL 정규화(dual-hash) · retry CLI (url/by-source).
**Out of scope**: Threads, IG 로그인 크롤링, Whisper 전사, wiki-site UI.

## 진행

### 2026-04-27 — oEmbed 차단 검증 워크플로 추가
- 2026-04-27 nightly 에서 YouTube `Pd-2F2gZoH8` fail. yt_dlp 는 "Sign in to confirm
  you're not a bot", youtube-transcript-api 는 RequestBlocked. 둘 다 GH Actions
  러너 IP 차단(클라우드 provider) 추정. oEmbed 폴백을 검토하기 전에 oEmbed 가
  같은 IP 에서 통하는지 먼저 검증 필요 (둘 다 차단되면 폴백 무의미).
- `.github/workflows/probe-youtube.yml` 신설 — workflow_dispatch 만, schedule 없음.
  단일 Python step 에서 차단 영상(`Pd-2F2gZoH8`) + control(`dQw4w9WgXcQ`) 각각에
  대해 (1) oEmbed `https://www.youtube.com/oembed?...` urllib 호출 (2) yt-dlp 메타
  (3) youtube-transcript-api `list()` 호출 → SUMMARY 매트릭스 출력. 검증 끝나면
  사용자 승인 후 별도 PR 로 제거 예정.
- nightly.yml 은 미변경 (회귀 위험 차단).

### 2026-04-24 — transient 실패 → 영구 오염 방지 (Task A~E)
**배경**: 2026-04-23 YouTube 영상 1건(`pnJOd5H5Zsc`, 실제로는 포토샵 보정 강좌)이
수집 시점에 빈 payload 로 저장되어 classifier 가 "AI 기반 개인화된 학습 경험 디자인"
으로 환각 분류 → 위키 md + 인덱스 + raw-archive 에 영구 오염. 4/24 daily brief
는 LLM 이 빈 문자열 응답으로 1바이트 빈 파일 덮어쓰기.

- **Task A** (`cc9b5b6`) `lib/fetchers/youtube.py` 에러 가시화 + 1회 재시도
  - `_fetch_metadata_once` / `_fetch_transcript_once` 로 분리해 각 단계 실패 사유를
    warning 로그 + 반환값에 명시 (bare `except Exception` 블랙박스 제거)
  - 두 단계 모두 `_RETRY_BACKOFF_SEC=0.75` 후 1회 재시도 — Actions 러너 IP flake /
    YouTube 일시 rate-limit 대비. 2회 연속 실패만 최종 실패로 간주.
  - metadata 재시도도 실패 + title/text 모두 빈 경우 `status="failed"` 로 강등.
    transient transcript 실패도 `no_transcript` 대신 `failed` 로 강등 — downstream
    이 "분류 대상 있음"으로 오해하지 않도록.
  - `"no_transcript"` (자막 실제 부재) 는 재시도 대상 아님 — 명시 분기.
  - `tests/fetchers/test_youtube.py` +5 케이스 (19 → 24): 메타/자막 retry 성공,
    transcript 2회 실패 강등, 빈 meta+no_transcript 강등, 진짜 no_transcript 유지
- **Task B** (`8f2021d`) `agents/ingester.py` 빈 payload 가드
  - 새 `_is_empty_payload(extracted)` / `_empty_payload_reason(result)` — status 가
    `_SAVE_STATUSES` 라도 title/text/user_caption 모두 비면 저장 거부, failed 루트
    로 이관(issues: `label_issue_failed`, file: `inbox-failed.md`). classifier 가
    빈 본문으로 환각 분류하는 2차 방어선.
  - `tests/test_ingester_status.py` +4 케이스 (13 → 17): 빈 no_transcript/빈 ok
    거부, user_caption 있으면 저장 허용, file 모드 `inbox-failed` 이관
- **Task C** (`d65c336`) `agents/classifier.py` 빈 입력 가드
  - 새 `_has_classifiable_signal(extracted)` — text_cleaned/text/title/user_caption
    중 하나라도 비공백이면 True. 모두 비면 `classify_one` 은 LLM 호출 없이 None
    반환, raw 는 그대로 두어 재수집 후 재분류 가능 상태 유지.
  - `classify_one` 이 `TokenCapExceeded` 를 더 이상 catch 하지 않고 전파 →
    `run()` 이 외부에서 catch 해 루프 중단. None 은 "skip, 다음 raw 계속" 의미로
    명확화. 완료 로그는 "처리 N건 · 스킵 M건" 표기.
  - `prompts/classifier.md` 에 "빈 입력 방어(분류 거부)" 섹션 추가 — URL 만 있고
    TITLE/본문/USER_CAPTION 모두 비면 null JSON 반환하도록 2차 방어선.
  - 신규 `tests/test_classifier_guard.py` 8 케이스: signal 휴리스틱 단위 5개 +
    빈 입력 LLM 미호출, run() skip-and-continue, TokenCapExceeded 전파
- **Task D** (`ce0d43b`) `agents/daily_brief.py` 빈 응답 가드
  - `_generate_one` 에서 Sonnet 응답 strip 후 빈 문자열이면 `_fallback_brief_for
    (target, "LLM empty response")` 로 치환 + warning 로그 (target/items 수/
    user_prompt 길이 동반). `lib.llm._generate` 의 `resp.text or ""` 을 1바이트
    빈 파일로 덮어쓰는 경로 차단.
  - 신규 `tests/test_daily_brief_empty.py` 4 케이스: 빈 응답 → fallback, 공백-only
    → fallback, 정상 응답 그대로, TokenCapExceeded fallback 회귀
- **Task E** 오염 데이터 정리 + 재수집
  - `scripts/retry.py url "https://youtube.com/watch?v=pnJOd5H5Zsc..."` 실행 →
    `_index.json` 엔트리 + `raw-archive/2026-04/72d39dcc3944.json` + `wiki/ai-ux-
    patterns/2026-04-23-ai-기반-개인화된-학습-경험-디자인.md` 제거, `_stats.json`
    재계산.
  - `wiki/daily/2026-04-24.md`(1 byte) 삭제 후 `daily_brief.py --force --no-catchup`
    재실행 → 650 bytes 정상 브리프. Task D 가드 로컬 적용된 상태에서 실행.
  - 라이브 재수집 베이스라인 확인: `fetch("...pnJOd5H5Zsc...")` → status=ok, title=
    "[#니손도돼요] 포토샵 강좌 : 보정으로 소프트 필터 효과내기", text 2569자, language=ko
- 전체 단위 81/81 통과 (이전 65 → 81 +16)

### 2026-04-23 (3)
- **Task 5** `transcript_cleanup` 에이전트 신설 — YouTube 자막을 Gemini Flash-Lite 로 prose 정제
  - 신규 `agents/transcript_cleanup.py` — ingester↔classifier 사이 단계. 필터: `source=YouTube` + `fetch_status=ok` + `has_transcript` + 길이 ≥ `TRANSCRIPT_CLEANUP_MIN_CHARS`(기본 500) + 미정제
  - 신규 `prompts/transcript_cleanup.md` — 요약/번역/정보추가 금지, 필러·중복 제거 + 문단 분할만
  - 저장 방식: 원문(`extracted.text`) 보존 + `extracted.text_cleaned` 병기 + payload 최상위 `cleaned: true` 플래그
  - `agents/classifier.py`: `_build_user` / `_compose_body` 에서 `text_cleaned` 우선, 없으면 `text` 폴백
  - `agents/nightly.py`: 오케스트레이터 2단계로 삽입 (ingester → transcript_cleanup → classifier)
  - 신규 env: `TRANSCRIPT_CLEANUP_DAILY_ITEM_CAP` (기본 15), `TRANSCRIPT_CLEANUP_MIN_CHARS` (기본 500)
  - `lib.llm.TokenCapExceeded` 시 루프 중단, 개별 LLM 예외/빈 출력은 스킵 후 계속 (파이프라인 정책)
  - 신규 `tests/test_transcript_cleanup.py` 16 케이스 — 필터/성공/실패/빈출력/dry-run/캡/토큰캡/classifier 폴백
- 전체 단위 65/65 통과

### 2026-04-23 (2)
- **Task 6** ingester status 기반 분기 정식화 — `extract_content` dict 변환 래퍼 제거, `fetchers.dispatch()` → `FetchResult` 를 ingester 본체가 직접 소비
  - `_SAVE_STATUSES = {"ok", "no_transcript"}` 화이트리스트로 저장 루트 분기
  - `login_required` / `failed` 는 저장 없이 `label_issue_failed` (이슈 모드) 또는 `inbox-failed.md` (파일 모드) 로 이관
  - `save_raw(item, extracted, *, fetch_status="ok")` 시그니처 확장 — raw payload 최상위에 `fetch_status` persist
  - `no_transcript` 케이스는 이슈 close 코멘트에 "자막 없음 — description 폴백" 플래그 표기
  - Task 2 에서 youtube 가 `metadata.fetch_status` 로 우회 표기한 필드 제거 (이제 payload 최상위에 단일 소스)
  - 신규 `tests/test_ingester_status.py` 13 케이스 (issues/file 모드 각 status 별 분기 + `_fail_reason` helper)
- 전체 단위 49/49 통과

### 2026-04-23
- **Task 2** YouTube 자막 고도화 (`76d3423`) — `lib/fetchers/youtube.py` 리팩터
  - `_extract_video_id`: watch/youtu.be/shorts/embed/v/bare ID 5개 포맷 지원
  - `_pick_transcript`: 언어 우선순위 6단계 (ko 수동 → en 수동 → 기타 수동 → ko 자동 → en 자동 → 기타 자동)
  - `_group_snippets_by_60s`: 자막을 60초 단위 문단으로 묶어 text 필드 구성
  - 자막 없으면 `status="no_transcript"` + yt_dlp description 폴백. ingester는 현재 `error` 필드 기준이라 error=None으로 두어 정상 저장 루트를 탐 (Task 6에서 status 기반 분기 도입 예정)
  - metadata: `video_id`, `channel`, `duration`, `language`, `has_transcript`, `fetch_status`
- `pyproject.toml`: `youtube-transcript-api>=0.6.2` 추가
- `tests/fetchers/test_youtube.py`: 19 케이스 (video_id 추출 11 + 60s 청크 7 + 실패 분기 1) — 네트워크 의존성 0, 모두 통과
- 라이브 스모크 통과: watch URL(수동 en 자막, 2092자) + shorts URL(자동 en 자막, 45자) + 잘못된 URL(status=failed)

### 2026-04-21
- **Task 8** IG 분류 신호: 사용자 클립보드 캡션 수용 (`5668adf`) — `lib/user_caption.py` 검증 휴리스틱 (URL/공백/None 거름, 글자수 제한 없음), `github_inbox.InboxIssue.memo` → `user_caption` 리네임, `ingester._run_issues_mode`/`_run_file_mode` 양쪽에서 validate → `extracted["user_caption"]`, `classifier._build_user` USER_CAPTION 라인 주입, `prompts/classifier.md` IG placeholder 분기에 캡션 우선 규칙 추가, `test_user_caption.py` 15 케이스

### 2026-04-20
- **Task 1.7** retry.py by-source 일괄 모드 (`28ecbd0`) — `by-source SOURCE [--apply] [--delete-only]`, dry-run 기본, ALLOWED_SOURCES 검증, 아이템별 try/except
- **Task 1.6** URL 정규화 + dual-hash (`ad604ad`) — `_TRACKING_PARAMS` blacklist, host lowercase/path 보존/쿼리 정렬, `url_hash_legacy` 유지, `test_url_hash.py` 16/16
- 과거 IG 부실 수집분 8개 정리 (wiki repo `_index.json` + md/json)

### 2026-04-19
- **Task 1.5** retry.py url 모드 (`bb30283`) — URL 단건 삭제/재시도, raw + raw-archive + md 일괄 제거
- **Task 7** IG fail-safe (`3130645`) — 항상 `status="ok"`, og best-effort + placeholder, classifier 프롬프트 분기
- **Task 4** 레포 3개 README 상호참조
- **Task 3** X oEmbed fetcher — `publish.twitter.com/oembed` + blockquote 파싱, 상태 매핑
- **Task 1** fetcher 모듈화 — `lib/fetchers/{base,__init__,generic,youtube}.py`, `FetchResult` dataclass, `extract_content()` dispatch thin wrapper

## 다음
- [ ] **Shortcut 변경(사용자)**: IG URL 공유 직전 캡션 영역 스크린샷 → Shortcut 이 "최근 사진 1장" → OCR → 이슈 body 에 동봉. iOS IG 앱은 캡션 직접 복사 불가 (2026-04-23 결정 참고)
- [ ] **Task 2 + Task 5 통합 스모크**: 실 YouTube URL 로 파이프라인 한 바퀴 — `ingester → transcript_cleanup → classifier` 순서로 raw JSON 에 `text`/`text_cleaned`/`cleaned` 필드가 제대로 쌓이는지, classifier 가 `text_cleaned` 를 소비하는지 확인 (2026-04-24 기준: 오염 영상 재수집 필요 — 사용자가 같은 URL 다시 공유하면 자동 처리됨)
- [x] **Task 5** transcript_cleanup 에이전트 (2026-04-23)
- [x] **Task 6** ingester status 기반 분기 (2026-04-23)
- [x] **Task A~E** transient 실패 → 영구 오염 방지 다층 방어 (2026-04-24)

## 결정

### 2026-04-24: transient 실패는 로깅+재시도, 빈 payload 는 저장 거부, 오염 raw 는 retry 가능 상태 유지 (Task A~D 원칙)
- **transient 실패 처리**: fetcher 내부에서만 짧은 backoff 1회 재시도 (YouTube rate-
  limit 자극 방지). 재시도 후에도 실패면 `status="failed"` 로 강등하고 `error`
  필드에 실패 사유를 사람이 읽을 수 있는 문자열로 기록. "말없이 삼키기" 금지 —
  warning 로그 + 반환값에 전부 반영.
- **빈 payload 저장 거부 (다층 방어)**: ingester 는 status 화이트리스트만 믿지 말고
  title/text/user_caption 셋 중 하나라도 있는지 추가 검사. classifier 는 또 한번
  같은 검사로 LLM 호출을 스킵. 프롬프트에도 3차 방어선(빈 입력 → null JSON)을
  명시. 단일 레이어 실패가 환각으로 빠지지 않도록 세 개 다 둔다.
- **오염 raw 는 retry 가능 상태 유지**: 빈 입력으로 스킵되는 raw 는 `raw-archive/`
  가 아니라 `raw/` 에 남겨두어 `scripts/retry.py` 또는 다음 ingester 실행에서
  재처리할 수 있게 한다. raw 를 archive 로 옮기는 것은 **성공적으로 분류된 경우만**.
- **LLM 빈 응답은 예외와 같은 등급**: Gemini 가 예외 없이 빈 문자열을 반환하는
  경로가 존재 (`resp.text or ""`). daily_brief 등 최종 쓰기 직전에 한 번 더
  `content.strip() == ""` 체크해서 fallback 으로 치환. 1바이트 빈 파일이
  production 에 저장되는 것이 가장 디버깅 어려운 실패.

### 2026-04-23: transcript_cleanup 은 원문 덮어쓰지 않고 text_cleaned 병기 (Task 5)
- 선택지: (a) `extracted.text` 덮어쓰기 — 저장 용량 절약 (b) `text_cleaned` 추가 — 원문 보존. **(b) 채택.**
- 이유: LLM 정제가 문맥 오해·환각으로 사실을 바꿨을 때 되돌릴 수 있어야 함. 재처리(프롬프트 개선 후 재실행)도 원문이 있어야 가능.
- 저장 비용은 YouTube 건당 평균 +30~40% 정도로 수용 가능. 반환 불가능한 품질 저하 리스크보다 작음.
- classifier 는 `text_cleaned` → `text` 순으로 폴백 읽기. cleanup 실패·미적용 아이템도 원래 경로로 분류 가능 (파이프라인 정책 일치).

### 2026-04-23: IG 캡션 복사 불가 → Shortcut 에서 스크린샷+OCR 로 전환
- 2026-04-21 결정("클립보드 캡션 채택")은 iOS IG 앱에서 캡션을 직접 복사할 수 없다는 제약으로 현실성 없음.
- 우회: 사용자가 공유 직전에 캡션이 보이게 스크린샷 → Shortcut 이 "최근 사진 1장"에서 OCR 로 텍스트 추출 → 이슈 body 에 동봉.
- 서버측 `validate_user_caption` / `github_inbox.body → user_caption` 파이프라인은 변경 없이 그대로 OCR 텍스트를 user_caption 으로 수용 (문자열 내용 검증 없이 URL 만 거름).
- 노이즈(Follow/Liked by/숫자 통계 등)는 `prompts/classifier.md` 의 "본문 노이즈 무시" 섹션이 이미 커버.
- 후속 개선 여지: Photos 소스를 "최근 스크린샷 1장"으로 필터링해 일반 사진 오염 차단.

### 2026-04-23: ingester 는 FetchResult.status 를 직접 읽는다 (Task 6)
- 기존 `extract_content` 가 FetchResult → dict 변환하며 `status` 를 버리고 `error` 유무로 판정 → Task 2 에서 `no_transcript` 를 `error=None` 으로 우회해야 했던 빚 발생.
- 해결: ingester 본체가 `fetchers.dispatch()` 를 직접 호출하고 `status` 로 분기. `_SAVE_STATUSES = {"ok", "no_transcript"}` 화이트리스트.
- `fetch_status` 는 `save_raw` 가 raw payload **최상위**에 단일 소스로 persist. youtube 가 `metadata.fetch_status` 에 중복 표기하던 건 제거.
- 왜 `no_transcript` 를 저장 루트에 태우나: description 폴백이 있어 최소 분류 신호가 남고, Task 5 의 `transcript_cleanup` 은 자막 있는 경우만 대상이므로 downstream 에서 이 status 를 보고 skip 하면 된다.

### 2026-04-21: IG 분류 신호는 사용자 클립보드 캡션 채택, vision 거절
- Vision(첫 이미지 분석) vs. 사용자 클립보드 캡션 두 안 비교. 캡션 채택. 토큰 비용 0 + 텍스트 신호가 이미지보다 분류 정확도 높음.
- **글자수 제한 없음**: 한 단어("맛있어")·해시태그 한 줄도 유효 신호. 컷오프는 휴리스틱 부담만 늘림.
- **URL 형식만 거름**: 클립보드 오염(전혀 무관한 링크 복사) 중 가장 흔한 케이스만 좁게 차단, 나머지는 신뢰.
- **거르는 위치는 서버(ingester)**: Shortcut이 아니라 ingester에서 거른다 — raw 로그에 "들어왔지만 버려짐" 흔적을 남겨 디버깅 가능.

### 2026-04-20: path 대소문자는 정규화에서 보존
- IG shortcode(`DC6MVPGpE_L`), YouTube videoID(`dQw4w9WgXcQ`)가 case-sensitive → path lowercase 하면 같은 콘텐츠가 다른 해시로 저장되는 재앙. scheme+netloc만 lowercase.

### 2026-04-20: URL 정규화는 dual-hash 과도기
- 기존 인덱스 엔트리는 legacy 해시로 저장돼 있어 즉시 교체하면 중복 탐지 깨짐. `url_hash_legacy` 보존 → `url_hashes()` 둘 다 반환 → `index_has_url()` 이중 체크. legacy 0 되면 단일화.

### 2026-04-19: YouTube 자막 LLM 후처리는 별도 에이전트 (B안)
- classifier 프롬프트 비대화 방지 + 쿼터 가드 독립 + cleanup 실패해도 classifier 진행 가능. A안(classifier 통합) 거절.

### 2026-04-19: IG는 본문 확보 포기 + fail-safe
- 사용자 피드백: IG는 원본 링크로 직접 보면 됨. 목표는 "그냥 안 깨지게". burner 계정은 다음 마일스톤.

### 2026-04-19: X는 oEmbed 1차 전략
- 비용 0, 인증 불필요. 공유받는 트윗 70~80%가 단일 트윗이라 충분. API v2 Basic($100/월) / Playwright(밴 리스크) 거절. 스레드·비공개는 `login_required`로 폴백.

### 2026-04-19: 새 레포 분리 대신 wiki-agent 통합
- 채널별 레포 계획 → `lib/fetchers/` 하위 모듈로 통합. 레포 수 오버엔지니어링 회피. 새 채널은 `__init__.py::_DISPATCH` + 모듈 1개로 추가.

## 링크
- `lib/fetchers/`, `lib/wiki_io.py`, `lib/user_caption.py`, `lib/github_inbox.py`, `agents/ingester.py`, `agents/classifier.py`, `prompts/classifier.md`, `scripts/retry.py`, `tests/test_url_hash.py`, `tests/test_retry_by_source.py`, `tests/test_user_caption.py`
- 커밋: `28ecbd0`, `ad604ad`, `bb30283`, `3130645`, `5668adf`, `76d3423`, `cc9b5b6`, `8f2021d`, `d65c336`, `ce0d43b`
- 참고 스킬: `.claude/skills/youtube-transcript/` (Task 2 포팅 대상)
