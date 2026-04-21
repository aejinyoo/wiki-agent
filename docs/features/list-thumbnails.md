# list-thumbnails

**상태**: 계획 (md-images 와 병합 구현) · **업데이트**: 2026-04-21

## 요약
wiki-site 의 `ItemCard` / 일간 브리프 / 검색 결과에 **카드 썸네일 이미지**를 표시. 데이터 소스는 원본 URL 의 `og:image` — 파이프라인 수집 시점에 한 번만 읽어 `WikiItem.thumbnail` 로 보존. 로드 실패·만료·hotlink 차단은 **소스별 기본 아이콘 CSS fallback** 으로 흡수. 카드에 이미지가 붙으면 맛집/릴/영상 콘텐츠 식별 속도가 크게 올라감.

**구현 계획**: 1차 스코프를 `md-images.md` 의 본문 인라인(`body_images`) 과 **한 브랜치에서 병합**. 공유 요소(OG 추출 util, `WikiItem` 필드, fetcher 훅) 가 겹쳐서 분리하면 중복 작업. 예상 소요 **6~9h (≈ 1 day)**.

**스코프** (list-thumbnails 본체):
- fetcher 단 OG 추출 **표준화** — 현재 IG 에만 있는 로직을 `lib/fetchers/og.py` 공용 util로 올리고, generic·x·youtube 도 사용
- `WikiItem` 에 `thumbnail: str = ""` 필드 추가 + `to_frontmatter_post()` 반영
- classifier 가 raw → md 변환 시 `extracted.thumbnail` → `item.thumbnail` 패스스루
- wiki-site `content.config.ts` schema 에 `thumbnail: z.string().optional()` 추가
- `ItemCard.astro` / `DailyCard.astro` 썸네일 슬롯: `aspect-ratio: 16/9` 컨테이너 + `object-fit: cover` + `<picture>` CSS fallback (소스 로고 SVG)

**병합 범위** (md-images 에서 가져오는 부분):
- `WikiItem.body_images: list[str]` 필드 + 프론트매터 반영
- generic fetcher 에 `<article>` 스코프 `<img src>` 수집 추가 (text 는 정제된 그대로 유지 — LLM 격리 원칙)
- IG/X/YouTube 는 `body_images` 빈 리스트 반환 (본문 짧아 대표 1장이면 충분)
- classifier `_compose_body` 가 generic 소스일 때만 하단에 인라인 `![](url)` 섹션 추가
- Astro 상세 페이지는 본문 markdown 자연 렌더 (Astro `<Content/>` 가 이미 원격 URL 지원)

**Out of scope**:
- 썸네일/본문 이미지 **프록시/캐시** — 원본 URL 직접 참조. 트래픽 누적 단계에서 재검토
- 이미지 리사이즈 / srcset / LQIP — 성능 이슈 생기면 2차
- og 태그의 `image:width`/`image:height` 파싱 — 16/9 고정으로 불필요

## 진행

_(착수 전)_

## 다음
- [ ] **T1** `lib/fetchers/og.py` — `parse_og(html) -> dict` 공용 util. IG 의 `_try_parse_og` 를 일반화해 이식 (title/description/image/video/site_name). 테스트 케이스 포함
- [ ] **T2** fetcher 훅 — generic/x/youtube/instagram 이 `metadata["thumbnail"]` 표준화. YouTube 는 yt-dlp `info["thumbnail"]` 직접, X 는 oEmbed 에 이미지 없으면 생략, generic 은 `og.py` 호출
- [ ] **T3** `WikiItem.thumbnail` + `WikiItem.body_images` 필드 추가, `to_frontmatter_post()` 반영, 기존 md 무해성 확인 (없으면 빈 값)
- [ ] **T4** generic fetcher 의 `<article>` 스코프 이미지 수집 — BeautifulSoup 로 `<img src>` 리스트 뽑아 `metadata["body_images"]`. trafilatura text 와 분리해 LLM 경로 격리 유지
- [ ] **T5** classifier `_compose_body` 에 조건부 인라인 이미지 섹션 (generic 소스 + `body_images` 비어있지 않을 때)
- [ ] **T6** wiki-site `content.config.ts` schema 에 `thumbnail`/`body_images` 추가, `ItemCard.astro` 에 16/9 슬롯 + CSS fallback
- [ ] **T7** 기존 분류 완료분 일괄 백필 — `scripts/backfill_thumbnails.py` (raw-archive JSON 의 `thumbnail` → md 프론트매터 주입)

## 결정

### 2026-04-21: 이미지는 원격 URL 직접 참조만, 레포 바이너리 저장 X
- 개인 텍스트 위키 성격과 바이너리 누적 안 맞음. 만료·hotlink 차단은 CSS fallback 으로 흡수.
- **트레이드오프 수용**: IG 등 signed URL 이 며칠 뒤 깨질 수 있음 — 원본 링크는 살아있으니 OK. 트래픽 누적 단계에서 프록시 재검토.

### 2026-04-21: LLM 경로 격리 원칙
- 이미지 URL 은 **DATA 경로 전용** (fetcher → raw json → md 프론트매터/본문). classifier 의 `_build_user` 에는 절대 투입 X.
- 이유: 이미지 URL 은 분류 신호로서 가치가 없음(vision 안 쓰면). Flash-Lite/Pro 쿼터·`DAILY_TOKEN_CAP` 에 추가 토큰 0.
- 구현 포인트: generic fetcher 가 trafilatura(기본 이미지 제외)로 뽑은 깨끗한 text 는 유지, BeautifulSoup 으로 **별도 패스** 해서 `body_images` 만 분리 수집.

### 2026-04-21: 카드 썸네일 aspect-ratio 16/9 고정
- OG 태그의 `image:width`/`image:height` 파싱해서 동적 비율 맞추는 복잡도 거절. 요즘 플랫폼 썸네일이 16/9 수렴 + `object-fit: cover` 로 시각 손실 최소.
- Layout shift(CLS)는 컨테이너에 고정 비율 두면 자동 해결.

### 2026-04-21: 로드 실패 = CSS fallback 아이콘
- JS onerror 대신 `<picture>` + CSS background (부모에 소스별 아이콘 깔고 `<img>` 실패 시 드러남). SSG·hydration 순서 무관.
- hotlink 차단(한국 블로그 등) 빈도가 체감상 문제면 2차에서 프록시.

### 2026-04-21: list-thumbnails 와 md-images 병합 구현
- 공유 요소: OG util, `WikiItem` 새 필드(`thumbnail`+`body_images`), fetcher 훅. 분리하면 같은 파일을 두 번 터치.
- 단, **문서는 분리 유지** — 관심사(카드 썸네일 vs. 본문 인라인)가 개념적으로 다르고 향후 스코프 확장(예: 본문 이미지 프록시만) 시 독립 추적 가능해야 함.

## 링크
- 병합 문서: `docs/features/md-images.md` — `body_images` 정책·generic 수집 책임
- 영향 파일(예상): `lib/fetchers/og.py`(신규), `lib/fetchers/{generic,x,youtube,instagram,map}.py`, `lib/wiki_io.py::WikiItem`, `agents/classifier.py::_compose_body`, `wiki-site/src/content.config.ts`, `wiki-site/src/components/ItemCard.astro`, `wiki-site/src/components/DailyCard.astro`, `scripts/backfill_thumbnails.py`(신규)
- 참고 (archive): `docs/archive/wiki-site-spec.md` 의 "og:image (Phase 2)"
