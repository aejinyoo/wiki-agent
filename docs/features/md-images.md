# md-images

**상태**: 계획 (정책 확정, list-thumbnails 와 병합 구현) · **업데이트**: 2026-04-21

## 요약
위키 md 파일 전반의 **이미지 처리 정책** 정립. 현재 파이프라인은 end-to-end 로 이미지를 사실상 버리고 있음. `list-thumbnails` 가 카드 썸네일 1장을 책임지고, 이 문서는 **본문 안 이미지**(주로 일반 블로그/아티클 소스) 를 상세 페이지에서 인라인 렌더하도록 보존.

**구현 계획**: `list-thumbnails.md` 와 **한 브랜치에서 병합 진행**. 공유 요소(`WikiItem.body_images` 필드, generic fetcher 의 이미지 수집, LLM 경로 격리 원칙) 가 겹침. 착수 순서·소요 시간은 `list-thumbnails.md` 참조.

**스코프**:
- generic fetcher 가 `<article>` 스코프에서 `<img src>` 리스트 수집 → `metadata["body_images"]`
- IG/X/YouTube/Map 은 `body_images` 빈 리스트 유지 (본문 짧아 대표 1장(썸네일) 이면 충분)
- `WikiItem.body_images: list[str]` 필드 + 프론트매터 반영 (list-thumbnails 와 공유)
- classifier `_compose_body` 가 generic 소스일 때만 하단에 `## 이미지` 섹션 + `![](url)` 나열
- wiki-site `<Content/>` 가 원격 URL 이미지 그대로 렌더 (Astro 기본 지원)

**Out of scope**:
- 카드 리스트 썸네일 — `list-thumbnails.md` 담당
- 본문 이미지 리사이즈 / 다운로드 / 프록시
- 이미지 OCR, 캡션 생성 등 LLM 기반 부가 처리

## 현황 (2026-04-21 코드 리뷰 결과)

**데이터 모델** — `lib/wiki_io.py::WikiItem`:
- 이미지 관련 필드 **없음**. `to_frontmatter_post()` 에도 미포함.

**Fetcher 단**:
- `instagram.py` — **유일하게** og 파싱: `og:image` → `metadata["thumbnail"]`, `og:video` → `metadata["is_video"]`
- `generic.py` — trafilatura 로 텍스트만. `include_tables=False`, 이미지 옵션 미설정 (기본 제외). `<title>` 만 따로 파싱
- `x.py` — oEmbed blockquote `<p>` 텍스트만. 첨부 미디어 URL 무시
- `youtube.py` — yt-dlp description 만. `info["thumbnail"]` 있는데 사용 안 함

**Ingester** — `agents/ingester.py::extract_content`:
- `FetchResult.metadata` 를 dict 로 spread 해서 raw json 에 저장. IG 의 `thumbnail` 은 raw 에 남음.

**Classifier** — `agents/classifier.py::_compose_body`:
- `## 한국어 요지` + `## 원문 발췌` 두 섹션만 조립
- `extracted` 의 이미지/썸네일 필드는 **전혀 참조 안 함** → 이 지점에서 정보 손실

**Wiki 저장본** (예: `wiki/trend-reports/2026-04-20-인스타그램-릴.md`):
- 프론트매터에 이미지 필드 없음, 본문에 이미지 마크다운 없음. 결과적으로 md 파일들이 전부 **텍스트-only**

**Wiki-site** — `ItemCard.astro`, `content.config.ts`:
- 카드에 이미지 슬롯 없음, schema 에 이미지 필드 없음 (`passthrough()` 로 열려는 있음)
- `docs/archive/wiki-site-spec.md` 에 `og:image (Phase 2)` 로 미구현 표시

**한 줄 결론**: IG fetcher 에 훅만 절반쯤 나 있고, 그 뒤 모든 단계가 이미지를 버린다. 데이터 모델 + classifier + site schema 3곳을 한 번에 통과시켜야 이미지가 살아남음.

## 진행

### 2026-04-21
- 현황 파악 — 위 "현황" 섹션 기준선 작성 (세션 기반 리뷰, 커밋 미발생)
- 정책 I1 확정 — 정책 A (URL-only 보존) 채택, list-thumbnails 와 병합 구현 결정

## 다음
- [x] ~~**I1** 정책 결정~~ → **정책 A 채택** (아래 결정 섹션 참조)
- [ ] **I3** generic fetcher 의 `<article>` 스코프 `<img src>` 수집 — BeautifulSoup, text 와 분리 (LLM 격리)
- [ ] **I4** classifier `_compose_body` — generic 소스 + `body_images` 있으면 `## 이미지` 섹션 추가
- [ ] **I5** wiki-site 인라인 렌더 검증 — Astro `<Content/>` 가 `![](remote-url)` 그대로 렌더하는지, CSP 충돌 없는지
- [ ] **I6** (list-thumbnails 와 공유) `WikiItem.body_images` 필드 + 프론트매터, classifier 패스스루

> **병합 브랜치 주의**: T1~T7 (썸네일) 과 I3~I6 (본문 인라인) 을 같은 브랜치에서 처리. `list-thumbnails.md::다음` 의 T 번호와 대응 관계 유지.

## 결정

### 2026-04-21: 정책 A 채택 — URL-only 보존, generic 만 수집
- **채택**: 본문 이미지는 원격 URL 만 markdown `![](url)` 로 보존. 바이너리 저장·프록시·캐시 없음.
- **소스별 차등**: generic(블로그·아티클) 만 `body_images` 채움. IG/X/YouTube/Map 은 본문이 짧고 대표 1장(썸네일) 이면 충분해서 빈 리스트.
- **기각된 대안**:
  - 정책 B (전부 스킵 유지) — 블로그 긴 글에서 figure 가 의미 있는 케이스 손실
  - 정책 C (og 1장만) — 썸네일만으로 충분한 IG/X 소스엔 이게 맞지만 blog 소스에선 정보 손실
  - trafilatura `include_images=True` — text 와 이미지 URL 이 섞여 LLM 에 들어감, 토큰 낭비 + Flash-Lite 혼란. 별도 패스로 분리.

### 2026-04-21: LLM 경로 격리 (list-thumbnails 와 공유)
- 이미지 URL 은 **DATA 경로 전용**. classifier `_build_user` 에 절대 투입 X.
- generic fetcher 의 trafilatura text 는 깨끗한 상태 유지(`include_images=False` 기본), `body_images` 는 BeautifulSoup 로 별도 패스에서 수집.
- 결과: `DAILY_TOKEN_CAP` 증분 0, Gemini 쿼터 가드 무영향.

### 2026-04-21: list-thumbnails 와 병합 구현, 문서는 분리 유지
- 공유 요소(필드·fetcher 훅·OG util) 중복 회피를 위해 한 브랜치.
- 관심사(카드 썸네일 vs. 본문 인라인) 가 개념적으로 다르고 향후 독립 확장(본문 이미지 프록시만 등) 가능성 있어 문서는 분리.

## 링크
- 병합 문서: `docs/features/list-thumbnails.md` — 썸네일 필드·OG util·fetcher 훅 정의
- 현황 참조 파일: `lib/wiki_io.py`, `lib/fetchers/{base,generic,instagram,x,youtube}.py`, `agents/ingester.py`, `agents/classifier.py`, `wiki-site/src/content.config.ts`, `wiki-site/src/components/ItemCard.astro`
