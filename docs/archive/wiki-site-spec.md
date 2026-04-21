# wiki-site — 개인 디자인 위키 정적 사이트 명세

> Claude Code 에 이 문서 하나 넣으면 Phase 1 MVP 까지 자동 구현 가능한 수준의 명세.
> 수정해도 되는 값은 `TODO:` 로 표시.

---

## 1. 프로젝트 개요

`aejinyoo/wiki` repo 에 nightly 로 쌓이는 markdown 들을 **따뜻한 화이트톤 · 미니멀 · 노션/리니어 감성**의 정적 사이트로 렌더하는 프로젝트.

### 배경
- 데이터 파이프라인 (`wiki-agent`) 는 완성 상태. 매일 07:30 KST GitHub Actions 가 markdown 을 커밋함.
- iPhone 에서 GitHub 앱으로 보는 건 "간지" 가 안 남. 본인 디자인 감각에 맞는 UI 필요.
- 노트북에서도 동일 URL 로 열람 가능해야 함 (디자이너이므로 데스크탑 작업이 주).

### 유저 · 유즈케이스
- **Primary user**: 프로젝트 소유자(애진) 1인.
- **Primary device**: iPhone (홈화면 아이콘으로 PWA 진입) + MacBook (브라우저 북마크).
- **Core task**:
  1. 아침에 일어나서 오늘자 브리프 읽기 (`/daily/2026-04-20`)
  2. 주제 궁금해지면 카테고리로 탐색 (`/wiki/ai-ux-patterns`)
  3. 특정 키워드 검색 (`/search?q=...`)

### Non-goals (MVP 에선 제외)
- 편집 기능 (읽기 전용)
- 로그인 / 멀티 유저
- 서버사이드 검색 (클라이언트 Fuse.js 로 충분)
- 댓글 · 좋아요
- 커스텀 도메인 (나중에 DNS 붙임, 일단 `*.pages.dev` 또는 `*.github.io`)

---

## 2. 기술 스택

| 레이어 | 선택 | 이유 |
|--|--|--|
| 프레임워크 | **Astro 4.x** | 기본 0KB JS, markdown 1급 시민, Content Collections 로 타입 안전 |
| 스타일 | **Tailwind CSS 3.x** | 빠른 프로토타이핑, 디자인 시스템 토큰화 쉬움 |
| 타입스크립트 | TypeScript 5.x | Content schema 타입 추론 |
| 마크다운 | `@astrojs/markdown-remark` + `remark-gfm` + `rehype-slug` + `rehype-autolink-headings` | GFM(체크박스·테이블) + 헤딩 앵커 |
| 폰트 | **Pretendard Variable** (로컬 호스팅) + **JetBrains Mono** (코드) | 한글 가독성 최상, 자체 호스팅으로 FOUT 최소 |
| 검색 | **Fuse.js 7.x** | 클라이언트 퍼지 매칭, 인덱스 빌드타임 생성 |
| 배포 | **Cloudflare Pages** (추천) 또는 GitHub Pages | Cloudflare = custom domain · edge cache · 무료 티어 충분 |
| CI | GitHub Actions | 이미 쓰고 있음 |

### Astro 이유 상세
- 콘텐츠 중심 사이트에 최적. Next.js 보다 결과물이 가볍고 빌드 빠름.
- Content Collections API 로 `src/content/daily/*.md` 를 선언적으로 타입 추론.
- 나중에 React island 박아야 하면 `@astrojs/react` 추가하면 끝.

---

## 3. 데이터 소스 & 리포지토리 구조

### 데이터 repo (이미 존재)
```
aejinyoo/wiki
├─ daily/2026-04-19.md, 2026-04-20.md, ...
├─ wiki/{category}/*.md        # 분류된 아이템들
├─ _index.json                 # id → path 매핑
├─ _stats.json                 # 카테고리·태그·소스 카운트
└─ _meta.yaml                  # Curator 설정
```

각 `.md` 파일은 frontmatter (YAML) + 본문 구조.

**daily brief frontmatter 예시**
```yaml
---
date: 2026-04-19
generated_at: 2026-04-19T07:30:00Z
item_count: 5
---
# 2026-04-19 위키 브리프
...
```

**wiki item frontmatter 예시**
```yaml
---
id: a3f8c9b2d1e4
source: X
url: https://x.com/...
author: someone
captured_at: 2026-04-19T08:12:00Z
title: Some AI UX Pattern
summary_3lines: "..."
tags: [onboarding, agent-ui]
category: ai-ux-patterns
confidence: 0.87
---
(본문)
```

### 새로 만들 site repo
```
aejinyoo/wiki-site         # 이 명세로 만들 repo
├─ src/
├─ public/
├─ astro.config.mjs
├─ tailwind.config.mjs
├─ package.json
└─ .github/workflows/
```

### 콘텐츠 주입 전략 — **Git Submodule 방식 채택**

`wiki-site/src/content/wiki-data/` 를 `aejinyoo/wiki` repo 의 submodule 로 연결.

**이유:**
- 빌드 시점에 최신 콘텐츠 보장 (submodule update)
- private repo 전환 시에도 PAT 로 쉽게 접근
- Claude Code 가 로컬에서 콘텐츠 보면서 작업 가능

**대안** (원하면 바꿔도 됨): GitHub Actions 빌드 시 `actions/checkout` 으로 두 repo 를 체크아웃 후 `rsync`.

**구현:**
```bash
cd wiki-site
git submodule add https://github.com/aejinyoo/wiki src/content/wiki-data
```

`.gitmodules` 생성됨. Astro 는 `src/content/wiki-data/daily/*.md`, `src/content/wiki-data/wiki/**/*.md` 를 Content Collections 로 읽음.

---

## 4. 정보 아키텍처 (IA) · 라우트

```
/                                 → 홈 (최근 브리프 3개 + 카테고리 그리드)
/daily                            → 전체 daily 목록 (역순)
/daily/[date]                     → 특정 날짜 브리프 (e.g. /daily/2026-04-19)
/wiki                             → 카테고리 인덱스 (카드 6개)
/wiki/[category]                  → 카테고리 내 아이템 목록 (필터 + 정렬)
/wiki/[category]/[slug]           → 개별 아이템 상세
/tags/[tag]                       → 태그로 묶인 아이템 목록
/search                           → 전역 검색 (Fuse.js)
/about                            → 이 위키에 대한 설명 + 파이프라인 요약
```

### URL 설계 원칙
- 소문자, kebab-case.
- 날짜는 `YYYY-MM-DD`.
- 카테고리는 `_index.json` 에서 그대로 사용 (예: `ai-ux-patterns`).
- 아이템 slug 는 파일명(`2026-04-19-some-title.md`) 에서 `.md` 만 뗀 값.

---

## 5. 페이지 상세 명세

### 5.1 홈 (`/`)
**목적:** 들어오자마자 "오늘 뭐 읽을지" 바로 보이기.

**섹션:**
1. **헤더** — 사이트 제목 "design wiki" + 오늘 날짜
2. **Today's brief** — 오늘자 브리프 카드 1개 (큰 카드, 본문 앞 3줄 + "읽기" CTA)
   - 오늘자 없으면 가장 최근 브리프로 폴백
3. **Recent briefs** — 최근 7일 중 오늘 제외한 브리프들 리스트 (날짜 + 제목)
4. **Categories** — 카테고리 6개 그리드 (각 카드에 카테고리명 + 아이템 수)
5. **Footer** — `_stats.json` 기준 총 아이템 수, 최종 업데이트 시각

### 5.2 Daily 상세 (`/daily/[date]`)
- 헤더: 날짜, "← 어제", "내일 →" 네비
- 본문: 브리프 markdown 렌더
- 우측 or 상단: 해당 브리프에서 언급된 아이템들을 "관련 링크" 로 칩(chip) 형태 노출

### 5.3 카테고리 (`/wiki/[category]`)
- 헤더: 카테고리명, 설명 (아래 카테고리 설명 섹션 참고), 아이템 수
- 필터바: 태그 칩 (클릭 시 `/tags/[tag]` 로 이동), 정렬 (최신순 / 제목순)
- 리스트: 아이템 카드 (제목, summary_3lines, 소스·날짜·태그)

### 5.4 아이템 상세 (`/wiki/[category]/[slug]`)
- 헤더: 제목, 메타(소스·저자·날짜)
- 본문: markdown 렌더
- 하단: "원본 보기" 외부 링크 버튼 (url), 태그 칩, 같은 카테고리 이전/다음 아이템 네비

### 5.5 검색 (`/search`)
- 상단: 큰 검색 인풋 (focus auto)
- 결과: 제목·요약·카테고리·태그에 매칭된 아이템들
- 무 결과: "아직 이런 건 기록 안 했네요" 같은 따뜻한 empty state
- 빌드타임에 `public/search-index.json` 생성해서 Fuse.js 가 fetch

---

## 6. 디자인 시스템

### 6.1 컨셉
**"따뜻한 화이트 종이 위의 타이포그래피"** — 노션의 여백감 + 리니어의 절제된 선 + 본인 취향의 따뜻함.

- 그림자 거의 안 씀 (1px 보더로 공간 분리)
- 프라이머리 컬러는 채도 낮은 웜 액센트
- 폰트 위계로 정보 구조 표현 (색상 X, 굵기 O)
- 모서리 radius 4~8px 선에서 통일

### 6.2 컬러 토큰 (Tailwind `theme.extend.colors`)

```js
colors: {
  // 배경 계열 (웜 오프화이트)
  bg: {
    DEFAULT: '#FBFAF6',    // 페이지 배경
    elevated: '#FFFFFF',   // 카드 · 모달
    subtle: '#F5F3EE',     // hover · 코드 인라인
  },
  // 보더 · 구분선
  border: {
    DEFAULT: '#E8E6DF',    // 1px 구분
    strong: '#D4D2CA',     // 강조 구분
  },
  // 텍스트
  text: {
    DEFAULT: '#1D1C19',    // primary
    secondary: '#636159',  // meta, caption
    muted: '#8C8A82',      // placeholder, disabled
    inverse: '#FBFAF6',    // 다크 배경용
  },
  // 액센트 (웜 브라운 · 따뜻한 오렌지)
  accent: {
    DEFAULT: '#8B6F47',    // 링크 · CTA 텍스트
    hover: '#6B5437',
    subtle: '#F4EDE2',     // 링크 호버 배경
  },
  // 시맨틱
  semantic: {
    success: '#6B8E5A',
    warning: '#C08A3E',
    danger: '#B85C5C',
  },
}
```

### 6.3 타이포그래피

```js
fontFamily: {
  sans: ['"Pretendard Variable"', 'Pretendard', '-apple-system', 'BlinkMacSystemFont', 'system-ui', 'sans-serif'],
  mono: ['"JetBrains Mono"', 'SF Mono', 'ui-monospace', 'monospace'],
  serif: ['"Noto Serif KR"', 'serif'], // 블록 인용구에 선택적으로
},
fontSize: {
  // [size, lineHeight, letterSpacing]
  'xs':   ['12px', { lineHeight: '1.5', letterSpacing: '0' }],
  'sm':   ['14px', { lineHeight: '1.55', letterSpacing: '-0.005em' }],
  'base': ['16px', { lineHeight: '1.7', letterSpacing: '-0.01em' }],    // body
  'lg':   ['18px', { lineHeight: '1.7', letterSpacing: '-0.012em' }],
  'xl':   ['20px', { lineHeight: '1.5', letterSpacing: '-0.015em' }],
  '2xl':  ['24px', { lineHeight: '1.4', letterSpacing: '-0.02em' }],    // h3
  '3xl':  ['30px', { lineHeight: '1.3', letterSpacing: '-0.022em' }],   // h2
  '4xl':  ['40px', { lineHeight: '1.2', letterSpacing: '-0.025em' }],   // h1 · page title
  '5xl':  ['56px', { lineHeight: '1.1', letterSpacing: '-0.03em' }],    // hero
},
fontWeight: {
  normal: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
},
```

### 6.4 스페이싱 · 레이아웃
- 베이스 그리드: **4px** (Tailwind 기본).
- 콘텐츠 max-width: `720px` (글 읽는 페이지), `1080px` (리스트 · 그리드).
- 모바일 좌우 padding: `20px`, 데스크탑: `32px`.
- 섹션 간 수직 간격: `48px ~ 80px`.

### 6.5 라운드 · 보더 · 섀도우
- radius: `6px` (카드), `4px` (칩·뱃지), `8px` (이미지), `999px` (아바타).
- 기본 보더: `1px solid bg.border`.
- 섀도우는 **오직 dropdown, popover, modal 에서만**. 일반 카드는 섀도우 X.
  - `shadow-sm`: `0 1px 2px rgb(29 28 25 / 0.04)`
  - `shadow-md`: `0 4px 12px rgb(29 28 25 / 0.08)`

### 6.6 컴포넌트 스타일 가이드

#### 카드 (`<Card>`)
```
배경: bg.elevated (#FFFFFF)
보더: 1px solid bg.border
radius: 6px
padding: 24px (데스크탑) / 20px (모바일)
hover: 보더만 border.strong 으로 강조 (배경 변경 X)
```

#### 링크
```
color: accent.DEFAULT
text-decoration: none (기본)
hover: text-decoration: underline, underline-offset: 4px, text-decoration-color: accent.subtle
visited: 살짝 어두운 accent.hover
```

#### 칩 (태그)
```
배경: bg.subtle
텍스트: text.secondary
padding: 4px 10px
radius: 4px
font: 12px medium
hover: 배경 → accent.subtle, 텍스트 → accent.DEFAULT
```

#### 네비게이션 바
```
상단 고정 (sticky), 높이 56px
배경: bg.DEFAULT + backdrop-blur (반투명 효과)
하단 보더: 1px solid bg.border
왼쪽: 로고 "design wiki" (serif 또는 medium sans 16px)
오른쪽: daily / wiki / search 링크 + 검색 아이콘
```

### 6.7 아이콘
- **Lucide React** (Astro 에서는 `lucide-astro` 사용) 로 통일.
- 크기: 16, 20, 24px. 기본 stroke-width: 1.5.
- 색상: 텍스트 컨텍스트 따라감 (`currentColor`).

---

## 7. 카테고리 설명

각 카테고리 페이지 헤더에 한 줄 설명 표시. `wiki/_meta.yaml` 에 있을 수도 있으니 거기서 읽거나, 없으면 다음 기본값 사용:

```yaml
ai-ux-patterns: "AI 제품에서 반복 관찰되는 UX 패턴들"
prompt-ui: "프롬프트 입력·편집·관리 인터페이스"
agent-interaction: "에이전트와 대화·개입·감독 UI"
generative-tools: "생성형 제작 도구의 디자인 언어"
design-system-automation: "디자인 시스템 자동화 · AI 인퍼런스"
trend-reports: "리포트·아티클·시장 동향"
```

---

## 8. 기능

### 8.1 MVP (Phase 1 — 우선 구현)

- [x] 홈 · 카테고리 · 아이템 상세 · daily 상세 라우트
- [x] 전역 내비게이션
- [x] Content Collections 기반 타입 안전 콘텐츠 로딩
- [x] Markdown 렌더 (GFM, 헤딩 앵커, 코드블록 하이라이트)
- [x] 클라이언트 검색 (Fuse.js) — 제목·요약·태그 대상
- [x] 태그 페이지
- [x] 반응형 (모바일 375px ~ 데스크탑 1440px+)
- [x] 404 페이지 (따뜻한 문구)
- [x] RSS 피드 `/rss.xml` (daily briefs)
- [x] sitemap · robots.txt

### 8.2 Nice-to-have (Phase 2)

- [ ] **PWA** — manifest.json, service worker, iPhone 홈화면 아이콘, 오프라인 캐시
- [ ] **다크모드 토글** (시스템 기본 respect)
- [ ] **아이템 상세 페이지의 "같은 태그로 다른 아이템" 추천**
- [ ] **카테고리 페이지 상단 통계 스파크라인** (주간 수집량)
- [ ] **키보드 단축키** (`/` 검색, `g h` 홈, `g d` daily 등 — linear 감성)
- [ ] **OG 이미지 자동 생성** — 각 페이지에 Satori 로 이미지 생성

### 8.3 Phase 3 (장기)

- [ ] 커스텀 도메인 (e.g. `wiki.aejin.design`)
- [ ] Cloudflare Access 로 private 가드 (원한다면)
- [ ] Meilisearch self-host 로 서버사이드 검색 (아이템 1000+ 넘어갈 때)

---

## 9. 빌드 & 배포 파이프라인

### 9.1 로컬 개발

```bash
# 최초
git clone --recurse-submodules https://github.com/aejinyoo/wiki-site
cd wiki-site
pnpm install
pnpm dev    # localhost:4321

# 콘텐츠 최신화
git submodule update --remote src/content/wiki-data
```

### 9.2 프로덕션 빌드 (Cloudflare Pages)

**Cloudflare Pages 설정:**
- Framework preset: **Astro**
- Build command: `pnpm build`
- Build output: `dist`
- Root directory: `/`
- Environment variables: (없음 — public 이므로)

**Submodule 체크아웃**: Cloudflare Pages 는 submodule 자동 지원. 활성화만 하면 됨 (설정 → Build configuration).

### 9.3 자동 재배포 트리거

`wiki` repo 가 업데이트될 때마다 `wiki-site` 가 다시 빌드돼야 함.

**방식:** `wiki` repo 의 nightly workflow 마지막 단계에 `wiki-site` 로 `repository_dispatch` 이벤트 발송.

**`wiki` repo 의 nightly.yml 추가 블록:**
```yaml
- name: Trigger wiki-site rebuild
  if: success()
  run: |
    curl -X POST \
      -H "Authorization: Bearer ${{ secrets.SITE_DISPATCH_TOKEN }}" \
      -H "Accept: application/vnd.github+json" \
      https://api.github.com/repos/aejinyoo/wiki-site/dispatches \
      -d '{"event_type":"content-updated"}'
```

**`wiki-site` 의 `.github/workflows/rebuild.yml`:**
```yaml
on:
  repository_dispatch:
    types: [content-updated]

jobs:
  rebuild:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
          token: ${{ secrets.CONTENT_REPO_TOKEN }}
      - run: git submodule update --remote
      - run: git commit -am "chore: bump wiki submodule" || echo "nothing to commit"
      - run: git push
      # Cloudflare Pages 가 push 감지해서 자동 재빌드
```

**필요 토큰:**
- `SITE_DISPATCH_TOKEN` (wiki repo Actions Secret): `wiki-site` 의 `contents:write`, `actions:write` 권한 fine-grained PAT
- `CONTENT_REPO_TOKEN` (wiki-site repo Actions Secret): `wiki` repo 의 `contents:read` 권한 fine-grained PAT

> MVP 중에는 submodule bump 를 수동으로 해도 됨 (`git submodule update --remote && git push`). 자동화는 Phase 2.

---

## 10. 파일 구조

```
wiki-site/
├─ public/
│  ├─ fonts/
│  │  ├─ PretendardVariable.woff2
│  │  └─ JetBrainsMono-Regular.woff2
│  ├─ favicon.svg
│  ├─ favicon-32.png
│  └─ apple-touch-icon.png
├─ src/
│  ├─ content/
│  │  ├─ config.ts                      # Content Collections schema
│  │  └─ wiki-data/                     # submodule → aejinyoo/wiki
│  ├─ components/
│  │  ├─ Nav.astro
│  │  ├─ Footer.astro
│  │  ├─ Card.astro
│  │  ├─ Chip.astro
│  │  ├─ ItemCard.astro
│  │  ├─ DailyCard.astro
│  │  ├─ CategoryCard.astro
│  │  ├─ SearchInput.astro              # island (React or Vanilla JS)
│  │  └─ Prose.astro                    # 마크다운 prose 래퍼
│  ├─ layouts/
│  │  ├─ Base.astro                     # html, head, body
│  │  └─ Article.astro                  # 글 읽기 레이아웃 (max-w-720)
│  ├─ lib/
│  │  ├─ collections.ts                 # daily/items/categories getter
│  │  ├─ search.ts                      # Fuse 인덱스 빌더
│  │  └─ dates.ts                       # KST 헬퍼
│  ├─ pages/
│  │  ├─ index.astro
│  │  ├─ 404.astro
│  │  ├─ about.astro
│  │  ├─ search.astro
│  │  ├─ rss.xml.ts
│  │  ├─ daily/
│  │  │  ├─ index.astro
│  │  │  └─ [date].astro
│  │  ├─ wiki/
│  │  │  ├─ index.astro
│  │  │  ├─ [category]/
│  │  │  │  ├─ index.astro
│  │  │  │  └─ [slug].astro
│  │  └─ tags/
│  │     └─ [tag].astro
│  └─ styles/
│     └─ global.css                     # Tailwind + 커스텀
├─ astro.config.mjs
├─ tailwind.config.mjs
├─ tsconfig.json
├─ package.json
└─ README.md
```

---

## 11. Content Collections Schema (src/content/config.ts)

```ts
import { defineCollection, z } from 'astro:content';

const daily = defineCollection({
  type: 'content',
  schema: z.object({
    date: z.string(),                        // YYYY-MM-DD
    generated_at: z.string().optional(),
    item_count: z.number().optional(),
  }),
});

const wikiItem = defineCollection({
  type: 'content',
  schema: z.object({
    id: z.string(),
    source: z.enum(['X', 'YouTube', 'Threads', 'Instagram', 'Manual']),
    url: z.string().url(),
    author: z.string().optional().default(''),
    captured_at: z.string(),
    title: z.string(),
    summary_3lines: z.string().optional().default(''),
    tags: z.array(z.string()).default([]),
    category: z.string(),
    confidence: z.number().min(0).max(1).default(0.5),
    tried: z.boolean().optional().default(false),
    tried_at: z.string().nullable().optional(),
  }),
});

export const collections = { daily, wikiItem };
```

**Astro content 폴더 매핑:**
- `src/content/wiki-data/daily/*.md` → `daily` collection
- `src/content/wiki-data/wiki/{category}/*.md` → `wikiItem` collection

`astro.config.mjs` 의 `contentDir` 설정이나 커스텀 로더 필요 시 조정.

---

## 12. 구현 단계 (Claude Code 에 그대로 지시해도 됨)

### Phase 1 — 뼈대 (약 2~3시간)

1. `pnpm create astro@latest wiki-site` → TypeScript strict, Tailwind integration.
2. `git submodule add https://github.com/aejinyoo/wiki src/content/wiki-data`.
3. Tailwind 설정 — 6절의 컬러·타이포 토큰 이식.
4. `public/fonts/` 에 Pretendard Variable woff2 배치, `global.css` 에서 `@font-face` 등록.
5. `src/content/config.ts` 작성 (위 11절).
6. `src/layouts/Base.astro` (html skeleton + Nav + Footer).
7. `src/components/Nav.astro`, `Card.astro`, `Chip.astro` 기본형.
8. `/`, `/daily/[date]`, `/wiki/[category]`, `/wiki/[category]/[slug]` 라우트 구현.
9. `pnpm dev` 로 로컬 확인.

### Phase 2 — 검색 & 부속

10. `/search` + Fuse.js 인덱스 빌더.
11. `/tags/[tag]`.
12. `/rss.xml.ts`.
13. `/404.astro`, `/about.astro`.

### Phase 3 — 배포

14. Cloudflare Pages 프로젝트 생성, GitHub 연동, submodule 활성화.
15. 빌드 확인 후 도메인 연결 (선택).

### Phase 4 — PWA · 폴리싱

16. `manifest.json`, service worker.
17. OG 이미지 자동 생성 (Satori).
18. 키보드 단축키.
19. 다크모드.

---

## 13. 성능 · 접근성 목표

- **Lighthouse 점수 (모바일)**: Performance ≥ 95, Accessibility ≥ 100, Best Practices ≥ 95, SEO ≥ 95.
- **LCP**: < 1.5s on 4G.
- **번들 JS**: 첫 페이지 < 30KB (Astro 이므로 거의 0 KB 예상, 검색 페이지만 Fuse.js).
- **Contrast**: WCAG AA 이상 (본문 ≥ 4.5:1).
- **키보드 네비게이션**: 모든 인터랙션 요소 tab 진입 가능, focus visible.
- **semantic HTML**: `<article>`, `<nav>`, `<main>`, `<time>`, `<figure>` 적재적소.

---

## 14. SEO · 메타데이터

각 페이지:
- `<title>`: 페이지별 고유 (e.g. `2026-04-19 브리프 · design wiki`)
- `<meta description>`: daily = summary 첫 줄, item = summary_3lines, 카테고리 = 설명
- OG: `og:title`, `og:description`, `og:image` (Phase 2), `og:type=article`
- Twitter card: `summary_large_image`
- `<link rel="canonical">`

---

## 15. 에러 · 빈 상태 처리

| 상황 | 처리 |
|--|--|
| 오늘자 daily 없음 | 홈에서 "오늘은 아직 없어요, 어제 브리프 보기 →" 카드 노출 |
| 카테고리에 아이템 0개 | "이 카테고리는 아직 비어있어요" + 다른 카테고리 추천 |
| 검색 결과 0건 | "이런 주제는 아직 없네요. 태그로 탐색해볼래요?" + 인기 태그 5개 |
| 잘못된 URL | 404 페이지 — "이 페이지는 없어요" + 홈으로 돌아가기 |
| submodule 업데이트 실패 | CI 가 실패 알림 (Phase 2 에서 Slack/이메일 연동 가능) |

---

## 16. 톤 · 문구 가이드

이 사이트는 본인용이므로 **따뜻하고 개인적인 톤**:
- "없어요", "읽어볼래요?" 같은 부드러운 종결.
- "에러" 같은 딱딱한 단어보다 "아직 없네요" 식.
- 사이트 소개(`/about`)는 "왜 이걸 만들었는지" 1인칭으로.

예시 문구 (마음껏 수정):
- 홈 히어로: "매일 아침의 디자인 읽기"
- 빈 검색 결과: "아직 이런 주제는 모아두지 않았어요"
- 404: "이 페이지로 가는 길은 없어요"

---

## 17. 참고 · 영감

- **Notion** (https://notion.so) — 여백, 페이지 구조, hover 피드백
- **Linear** (https://linear.app) — 타이포 위계, 키보드 단축키, 다크모드 전환
- **Maggie Appleton** (https://maggieappleton.com) — 개인 디지털 가든의 따뜻함
- **Gwern.net** — 아이템 상세의 메타데이터 블록 처리

---

## 18. 개발자에게 한 마디

Claude Code 로 이 스펙을 구현할 때 — **디자인 토큰은 엄격하게**, **문구는 유연하게** 가져가주세요. 색상·타이포·스페이싱은 6절 그대로 써도 만족스러울 거고, 문구는 16절 예시를 참고해서 자연스럽게 풀어주면 됩니다.

첫 커밋은 Phase 1 의 1~9번 스텝까지 한 번에. `pnpm dev` 로 홈·daily·wiki·item 4개 라우트가 로컬에서 열리면 Phase 1 완료.

질문 생기면 이 문서의 섹션 번호 (예: "6.2 컬러 토큰 재검토") 로 레퍼런스해서 대화하면 빠름.
