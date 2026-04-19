# design-wiki-agent

AI 디자인 개인 위키 에이전트. `wiki` repo를 대상으로 Ingester·Classifier·Curator·Daily Brief 4개 에이전트를 돌린다. **LLM은 Google Gemini 사용** (Flash-Lite + Pro, 무료 tier 안에서 동작).

- **기획서**: [aejinyoo/wiki · design-wiki-agent-plan.md](https://github.com/aejinyoo/wiki/blob/master/design-wiki-agent-plan.md)
- **대상 저장소**: [aejinyoo/wiki](https://github.com/aejinyoo/wiki)
- **실행 환경**: **GitHub Actions** (cron), 기기가 꺼져 있어도 매일 작동
- **스케줄**: 매일 07:30 KST (UTC 22:30, 전날)

## 아키텍처

```
[iPhone 공유시트]
     │
     ▼
[iOS Shortcut]  ── GitHub API: POST /issues (title=URL, label=inbox)
     │
     ▼
[aejinyoo/wiki — Issue #N with label:inbox]
     │
     ▼
[GitHub Actions (cron 07:30 KST)]
     checkout code + data → nightly.py → commit + push
     │
     ▼
[aejinyoo/wiki — wiki/*.md, daily/YYYY-MM-DD.md]
     │
     ▼
[iPhone — Working Copy 앱으로 wiki repo 동기화]
     │
     ▼
[Obsidian vault + Scriptable 위젯]
```

## 구조

```
wiki-agent/
├─ .env                      # 개인 API 키 (git 제외)
├─ .env.example              # 템플릿
├─ pyproject.toml            # uv 프로젝트
├─ agents/
│  ├─ nightly.py             # ← launchd가 부르는 오케스트레이터
│  ├─ ingester.py            # LLM 0
│  ├─ classifier.py          # Haiku
│  ├─ curator.py             # Sonnet, 조건(아이템≥50 & 7일↑) 만족 시
│  ├─ daily_brief.py         # Sonnet, 최근 3일 catch-up 포함
│  └─ rebuild_index.py       # 복구용 (LLM 미호출)
├─ prompts/
├─ lib/
│  ├─ paths.py
│  ├─ llm.py                 # Gemini 래퍼 + 사용량 카운터
│  ├─ wiki_io.py             # frontmatter·인덱스 IO
│  └─ validate.py            # "validate & auto-fix"
├─ launchd/                  # macOS plist (nightly 1개)
└─ logs/                     # 실행 로그
```

## 실행 순서 (nightly.py 내부)

1. **Ingester** — GitHub 이슈(`inbox`) 또는 `inbox.md` → `raw/<id>.json`
   - 날짜 정보는 JSON 내부 `item.captured_at` 에 이미 들어감 (폴더 구조 대신 메타데이터로 보존)
2. **Classifier** — 미분류 `raw/` → `wiki/{category}/*.md` (Gemini Flash-Lite, 일 최대 30건)
   - 분류 성공한 원본은 `raw-archive/YYYY-MM/` 로 자동 이동 (월 단위 묶음)
   - 실패한 원본은 `raw/` 에 그대로 남아 다음 실행에서 재시도
   - 과거 `raw/YYYY-MM-DD/*.json` 구조도 자동 인식해서 같이 처리 (호환)
3. **Curator** — 조건(아이템≥50 AND 마지막 실행 ≥ 7일 전) 충족 시만
4. **Daily Brief** — 최근 3일 중 누락된 브리프 소급 + 오늘자 생성 (Gemini Pro)

각 단계는 실패해도 다음 단계가 계속 실행됩니다.

## 초기 세팅 (3단계)

### 1) GitHub 준비

```
aejinyoo/wiki        ← 데이터 repo (기획서·wiki/·daily/·raw/)
aejinyoo/wiki-agent  ← 이 repo (에이전트 코드)
```

**wiki repo에 `inbox` 라벨 생성**: Issues 탭 → Labels → New label → name: `inbox`.

### 2) Gemini API 키 발급 + PAT + Actions Secret 등록

**2-1) Gemini API 키 발급**

1. https://aistudio.google.com/apikey 접속 (구글 계정 로그인)
2. **Create API key** 클릭 → 프로젝트 선택(없으면 자동 생성)
3. `AIza…` 로 시작하는 키 복사
4. 무료 tier 한도: Flash-Lite·Pro 모두 하루 수백만 토큰 수준 — 이 프로젝트 상한(25k/일) 대비 압도적으로 여유 있음

**2-2) GitHub PAT 발급 (wiki repo 접근용)**

1. GitHub → Settings → Developer settings → **Personal access tokens** → **Fine-grained tokens** → Generate new token
2. 권한:
   - Resource owner: 본인
   - Repository access: `aejinyoo/wiki` 만 선택
   - Permissions — Repository:
     - **Contents**: Read and write
     - **Issues**: Read and write
   - Expiration: 90일 (기한 오면 갱신 필요)
3. 발급된 토큰 복사

**2-3) Actions Secrets 등록**

`aejinyoo/wiki-agent` → Settings → Secrets and variables → Actions → **New repository secret**:
   - `GEMINI_API_KEY`   = 위 2-1)에서 발급한 Gemini 키
   - `WIKI_REPO_TOKEN`  = 위 2-2)에서 만든 PAT

Variables (선택, 기본값 있으면 생략 가능):
   - `GITHUB_WIKI_REPO`  = `aejinyoo/wiki`
   - `INBOX_LABEL`       = `inbox`

### 3) Actions workflow 푸시

이 repo(`wiki-agent`)의 `.github/workflows/nightly.yml`이 커밋되어 있으면 자동으로 등록됨.
Actions 탭에서 "Nightly Wiki Agent" 확인.

첫 실행은 **수동**으로 테스트:
- Actions 탭 → "Nightly Wiki Agent" → **Run workflow**
- `Dry run: true` 로 돌려 로그 확인

### (선택) 로컬 테스트

로컬에서 직접 돌려보고 싶을 때만:

```bash
cp .env.example .env
chmod 600 .env
# .env 에 GEMINI_API_KEY, WIKI_REPO_PATH, WIKI_REPO_TOKEN 채우기

uv sync
uv run agents/ingester.py --dry-run
uv run agents/nightly.py --dry-run
```

## 수동 실행

```bash
# 전체 파이프라인 (launchd가 부르는 것과 동일)
uv run agents/nightly.py

# 개별 에이전트
uv run agents/ingester.py
uv run agents/classifier.py --limit 5
uv run agents/daily_brief.py --force        # 오늘자 재생성
uv run agents/daily_brief.py --no-catchup   # 오늘 것만, 과거 소급 X
uv run agents/curator.py --force            # 50건 미만이어도 강제

# 전수 인덱스 재생성 (LLM 미호출, 복구용)
uv run agents/rebuild_index.py
```

## 가드레일

- 토큰: `DAILY_TOKEN_CAP_*` 초과 시 당일 중단, `_usage.json`에 기록
- 위키 검증: Classifier/Curator가 `.md` 쓰기 전 `lib/validate.py`로 auto-fix
- Curator 가드레일: `wiki/_meta.yaml` 참조 (protected, cooldown, weekly limits)

## iOS Shortcut — "Add to Wiki Inbox"

공유시트에서 URL 공유 → wiki repo에 이슈 자동 생성.

**Shortcut 구성:**

1. 트리거: "공유 시트" 활성화, 받는 타입: URL
2. Action: **Get Contents of URL**
   - URL: `https://api.github.com/repos/aejinyoo/wiki/issues`
   - Method: `POST`
   - Headers:
     - `Authorization`: `Bearer <PAT>` ← 위 2)에서 만든 PAT 동일 값 사용 (Contents/Issues RW)
     - `Accept`: `application/vnd.github+json`
   - Request Body (JSON):
     ```json
     {
       "title": "공유된 URL",
       "labels": ["inbox"]
     }
     ```
     - `"공유된 URL"` 값은 Shortcut 변수 **Shortcut Input** 사용
3. Action: **Show Notification** → "위키에 추가됨" + 햅틱

홈화면에 아이콘 고정해두면 공유 플로우 원탭.

**PAT 주의**: PAT가 Shortcut 안에 평문 저장됨. 분실 우려되면 iCloud Keychain의 Shortcut 암호화 기능 활성화 + PAT 권한을 `aejinyoo/wiki` repo만으로 제한했는지 재확인.

## 모바일 뷰어 (Working Copy + Obsidian)

1. **Working Copy** (iOS) 설치 → `aejinyoo/wiki` clone
2. **Obsidian** 설치 → "Open folder as vault" → Working Copy의 wiki 폴더 선택
3. Working Copy에서 pull 자동화:
   - Working Copy → Settings → Automation → "Periodic Fetch" 활성화 (5분~30분)
   - 또는 Obsidian 열 때마다 수동 Pull

이러면 매일 아침 브리프가 자동 push → Working Copy fetch → Obsidian에서 바로 보임.
