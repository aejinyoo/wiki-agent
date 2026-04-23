# sns-fetchers

**상태**: 구현 중 (9/10) · **업데이트**: 2026-04-23

## 요약
SNS 공유 링크(X, Instagram, YouTube) 본문을 채널별 어댑터로 안정 수집하도록 ingester 파이프라인 재편. 기존 generic `requests + trafilatura`가 클라이언트 렌더링/로그인월에 막혀 로그인 페이지 HTML이 인덱스에 오염되던 문제 해결.

**스코프**: `lib/fetchers/` 모듈화 + FetchResult 공통 계약 · X oEmbed · IG placeholder · YouTube 자막 고도화 · transcript_cleanup 에이전트 신설 · URL 정규화(dual-hash) · retry CLI (url/by-source).
**Out of scope**: Threads, IG 로그인 크롤링, Whisper 전사, wiki-site UI.

## 진행

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
- [ ] **Task 2 통합 스모크**: 실제 YouTube URL(자막 O/X 각 1건)로 `fetch()` 호출해 raw JSON 구조 확인 (top-level `fetch_status` 포함 확인)
- [ ] **Task 5** `transcript_cleanup` 에이전트 신설 — ingester↔classifier 사이, Gemini Flash-Lite, `cleaned` 플래그 + 일일 캡. Task 6 의 `fetch_status` 를 읽어 `no_transcript` 는 건너뛰고 `ok` 중 youtube 자막만 대상으로
- [x] **Task 6** ingester status 기반 분기 (2026-04-23)

## 결정

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
- 커밋: `28ecbd0`, `ad604ad`, `bb30283`, `3130645`, `5668adf`, `76d3423`
- 참고 스킬: `.claude/skills/youtube-transcript/` (Task 2 포팅 대상)
