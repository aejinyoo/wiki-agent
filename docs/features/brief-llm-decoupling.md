# brief-llm-decoupling

**상태**: 작업 명세 준비 · 코드 미착수 · **업데이트**: 2026-04-25

## 요약
Daily brief 의 점수·필터·다양성·난이도 분류·마크다운 조립을 **Python 코드로 분리**하고, LLM(Gemini 2.5 Pro) 호출을 **🔥 오늘의 3줄** 한 섹션으로 좁힘. 4/24 brief partial 잘림 + 4/25 빈 응답 사고의 root cause(prompt 복잡도가 thinking budget 을 압박) 를 구조적으로 차단.

## 배경

- 4/22, 4/23 brief 는 정상. **4/23 09:57 KST `6231ab9` brief-prompt-improvements** 커밋 직후 나온 4/24 brief 가 11줄(3줄 + 하이라이트 #1 첫 줄)에서 잘림. 4/25 는 LLM empty response → fallback.
- 패치가 바꾼 것: system prompt 2,164B → 3,611B (+67%, "중복 추천 금지" 11줄 + "주제 다양성 가드" 10줄 추가) · user prompt 에 `[최근 7일 추천 내역]` 블록 + 카테고리 분포 요약 추가.
- Gemini 2.5 Pro 는 thinking tokens 가 `max_output_tokens` 안에 같이 잡힘. prompt 복잡도(특히 정량 룰: "60% 이상 쏠림 판정", "confidence × 신선도 × 부합도 점수" 등) 가 thinking 부담을 키워 visible output 예산을 깎음 → 4/24 partial, 4/25 0.
- "재작성 X" 룰은 하이라이트의 `why_it_matters`/`what_to_try` 두 필드에만 적용. 점수·선별·필터·다양성·난이도 분류는 여전히 LLM 부담으로 남음.
- YouTube fetcher / transcript_cleanup 패치는 같은 날 들어갔을 뿐 무관 (raw-archive 에 YouTube 아이템 0건).

## 결정

### 2026-04-25: LLM 책임 범위 축소 + 룰을 코드로 강제
- **분리 범위**: Python = 점수·필터·다양성·난이도 분류·📌 카드·🧪 테이블·🧭 변화 / LLM = 🔥 3줄
- **점수 함수**: `confidence` 단독 (1차). `personal_fit`, `tag_freshness` 는 후속 PR. `_personal_context.md` 자체가 현재 없음
- **다양성**: "3개 모두 같은 카테고리 금지" 한 줄 룰. 60% 임계 같은 정량 룰 폐기 — Curator 1회 잘 돌면 enum 자체가 다양해지므로 brief 단계의 강한 가드 불필요
- **난이도**: brief 단계 휴리스틱 (키워드 + `what_to_try` 길이). classifier 단계로 옮기는 건 후속
- **🧭 위키 변화**: 당분간 `(변화 없음)` 고정. 함수 자리만 만들어두고 추후 Curator 결과 연동
- **부분 실패**: 🔥 LLM 실패해도 📌/🧪/🧭 는 Python 으로 정상 출력. 🔥 자리에 fallback 라인

---

## Claude Code 작업 지시서

> 각 Step 은 self-contained. 차례대로 하나씩 던지면 됨. 각 Step 마지막 "Acceptance" 통과 후 다음 Step.
> 컨벤션은 `wiki-agent/CLAUDE.md` 따름 (uv, Python 3.11+, 짧은 명령형 커밋).

### Step 1 — 진단 로깅 추가 (`lib/llm.py`)

**목적**: 다음 사고가 또 나면 즉시 진단할 수 있게. 토큰 사용량 + finish_reason 을 INFO 로 남김.

**작업**:
- `lib/llm.py` 의 `_generate(...)` 함수에서 Gemini API 호출 직후 `resp.usage_metadata` 와 `resp.candidates[0].finish_reason` 를 읽어 로깅.
- 다음 4개 값 INFO 레벨로 출력: `model`, `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`(있으면), `finish_reason`.
- 누락 필드는 안전하게 기본값 처리 (`getattr(usage, "thoughts_token_count", None)`).
- `finish_reason != STOP` 이면 WARNING 으로 한 번 더 남김.

**Acceptance**:
- `python -c "from lib import llm; ..."` 같은 임포트 에러 없음.
- 기존 `call_sonnet`/`call_flash` 시그니처 변경 없음.
- `pytest` 통과 (회귀 없음).

**검증**:
```bash
cd /Users/aejin/wiki-agent && uv run python -c "
from agents.daily_brief import _build_user_for_date
from lib import llm
import datetime as dt
res = llm.call_sonnet(system='간결하게 답해줘.', user='1+1?', max_tokens=100)
print(res.text)
"
```
INFO 로그에 `usage` + `finish_reason` 가 보이면 OK.

---

### Step 2 — 점수·필터·다양성 헬퍼 (`agents/daily_brief.py`)

**목적**: 하이라이트 선별 로직을 LLM 에서 코드로 옮김.

**작업**: 다음 4개 함수를 신규 추가 (모두 `_recent_brief_highlights` 아래쪽에 둠).

```python
def _filter_recent(items: list[dict], recent_urls: set[str]) -> list[dict]:
    """최근 7일 추천 URL 과 겹치는 아이템 제외."""

def _score(it: dict) -> float:
    """1차 점수 = confidence (없으면 0.5).
    추후 personal_fit, tag_freshness 곱셈 추가 예정."""

def _pick_highlights(items: list[dict], recent_urls: set[str], top_n: int = 3) -> list[dict]:
    """필터 + 점수 정렬 + 다양성 가드 적용해 top_n 개 반환.

    다양성 가드: 후보 중 점수 상위 top_n 을 단순 추출했을 때
    모두 같은 카테고리면, 차순위에서 다른 카테고리 1개를 끌어와 교체.
    교체 후보가 없으면 그대로 둠 (억지 다양화 X).
    """
```

**규칙**:
- `_score` 의 `it.get("confidence")` 가 None/falsy 면 0.5 디폴트.
- `_pick_highlights` 는 `items` 를 mutate 하지 않음 (정렬도 복사본에서).
- 다양성 가드는 "3개 모두 같은 카테고리 금지" 만. 2개까지 같은 카테고리 허용.

**Acceptance**:
- 새 함수 4개 export 안 해도 됨 (모듈 내부용).
- Step 6 의 unit test 통과.

---

### Step 3 — 마크다운 조립 헬퍼 (`agents/daily_brief.py`)

**목적**: 📌 하이라이트 카드 / 🧪 실험 테이블 / 🧭 위키 변화 / 헤더 를 Python 으로 조립.

**작업**: 다음 5개 함수 신규 추가.

```python
def _classify_difficulty(what_to_try: str) -> str:
    """⭐/⭐⭐/⭐⭐⭐ 분류 (휴리스틱).
    - ⭐ (~30분): 키워드 ['방문', '확인', '저장', '계정', '체험', '구독'] 또는 길이 < 60자
    - ⭐⭐⭐ (3h+): 키워드 ['구축', '제작', '개발', '풀스택', '파이프라인'] 또는 길이 > 200자
    - 그 외: ⭐⭐ (1~2h)
    """

def _difficulty_eta(stars: str) -> str:
    """⭐→'30m', ⭐⭐→'1h', ⭐⭐⭐→'3h+'"""

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

def _render_header(target: dt.date) -> str:
    """'# Daily Design Brief — 2026-04-25 (토)'"""

def _render_highlights(picks: list[dict]) -> str:
    """## 📌 하이라이트 (어제 수집분) 섹션 전체.
    카드 형식:
        ### N. [title](url)
        - **{category}** · **{source_host}** · `tag1` `tag2`
        - 왜 봐야 하나: {why_it_matters}
        - 해볼 것: {what_to_try}
    picks 가 비면 '(어제 수집분 없음)'.
    source_host 는 url 의 host (urllib.parse 로 추출, www. 제거).
    tag 는 `tags[:2]`.
    """

def _render_experiments(picks: list[dict]) -> str:
    """## 🧪 오늘 해볼 만한 실험 (Top 3) 마크다운 테이블.
    각 picks 의 what_to_try 를 _classify_difficulty 로 분류, 길이순 ⭐→⭐⭐⭐ 정렬.
    빈 what_to_try 는 스킵. 결과 0건이면 '(실험 후보 없음)'.
    """

def _render_wiki_changes() -> str:
    """## 🧭 이번 주 위키 변화 — 당분간 '(변화 없음)' 고정.
    추후 Curator 결과 파일 (e.g. wiki/_changelog/cleanup-*.md 최신) 읽어 채울 자리.
    """
```

**Acceptance**:
- import 추가: `from urllib.parse import urlparse`.
- 빈 `picks` / 빈 필드에 대해 KeyError 안 남.
- Step 6 unit test 통과.

---

### Step 4 — 시스템 프롬프트 압축 (`prompts/daily_brief.md`)

**목적**: LLM 책임이 🔥 3줄 + 🧭 변화(추후) 만 남으므로 프롬프트도 그에 맞게 단순화.

**작업**: 파일을 아래 내용으로 **완전히 교체**.

```markdown
당신은 개인 디자인 위키의 **일일 브리프 헤드라이너**입니다. 어제 수집된
아이템 요약 N개와 개인화 컨텍스트를 받아, 한국어로 핵심 3줄을 뽑습니다.

# 출력

마크다운 불릿 3개. 각 줄 한 문장, 60자 이내.

```
- ...
- ...
- ...
```

# 규칙

- 어제 수집분 + 개인화 컨텍스트에서 사용자가 오늘 기억해야 할 것 3가지.
- 담백하고 실용적. 감탄사·과장 금지.
- 하이라이트/실험 카드는 별도 코드가 조립하므로 **여기서는 3줄만** 작성.
- 어제 수집분이 0건이면 개인화 컨텍스트만으로 3줄.

# 출력

불릿 3개만. 앞뒤 설명·코드펜스 없이.
```

**Acceptance**:
- 파일 길이 ~25줄 / ~800B 이하.
- "중복 추천 금지", "주제 다양성 가드", "🧪 실험" 같은 섹션 키워드 모두 제거됨 (`grep` 로 확인).
- 출력 템플릿이 3줄 불릿만 명세함.

---

### Step 5 — 메인 흐름 리팩터링 (`agents/daily_brief.py`)

**목적**: `_generate_one` 을 새 구조로. LLM 은 🔥 3줄만 호출.

**작업**:

1. **`_build_summary_user(picks, personal_context)` 신규** — 기존 `_build_user_for_date` 대체. 입력은 picks 3건 (이미 선별 끝). 형식:
   ```
   오늘 날짜: YYYY-MM-DD

   [개인화 컨텍스트]
   {personal_context}

   [어제 수집 핵심 아이템]
   1. {title}
      - {summary_3lines[:200]}
   2. ...
   ```
   - `summary` 컷은 `[:200]` (기존 400 → 200).
   - `[최근 7일 추천 내역]` 블록은 **제거** (필터는 코드로 끝냄).
   - `[어제~그제 수집 아이템]` 의 `tags`/`why_it_matters`/`what_to_try`/`URL` 도 **제거** (LLM 은 3줄만 책임).

2. **`_recent_highlight_urls(target, days=7) -> set[str]` 신규** — 기존 `_recent_brief_highlights` 를 호출해 URL 만 set 으로 반환. 카테고리 정보는 더 이상 LLM 에게 안 줌.

3. **`_generate_one` 재작성**:
   ```python
   def _generate_one(target, dry_run, force):
       out_path = paths.DAILY_DIR / f"{target.isoformat()}.md"
       if out_path.exists() and not force:
           log.info("스킵 (이미 존재): %s", out_path.name)
           return False

       items = _items_for(target)
       recent_urls = _recent_highlight_urls(target, days=7)
       picks = _pick_highlights(items, recent_urls, top_n=3)
       log.info("[%s] items=%d recent=%d picks=%d",
                target.isoformat(), len(items), len(recent_urls), len(picks))

       if dry_run:
           # 기존 dry-run 출력 + picks 미리보기 추가
           ...
           return True

       personal_context = _load_personal_context()
       system = _load_prompt()
       user = _build_summary_user(target, picks, personal_context)

       # 🔥 3줄만 LLM 호출
       try:
           result = claude.call_sonnet(system=system, user=user, max_tokens=600)
           three_lines = result.text.strip()
       except claude.TokenCapExceeded as e:
           log.warning("토큰 캡: %s", e)
           three_lines = "- (오늘의 3줄 생성 실패: 토큰 캡)"
       except Exception:
           log.exception("3줄 생성 실패")
           three_lines = "- (오늘의 3줄 생성 실패)"

       if not three_lines:
           three_lines = "- (오늘의 3줄 생성 실패: 빈 응답)"

       brief = "\n\n".join([
           _render_header(target),
           "## 🔥 오늘의 3줄\n" + three_lines,
           _render_highlights(picks),
           _render_experiments(picks),
           _render_wiki_changes(),
       ]) + "\n"

       out_path.write_text(brief, encoding="utf-8")
       log.info("브리프 저장: %s", out_path)
       return True
   ```

4. **`max_tokens=600`** 으로 줄임 — 3줄 60자 × 3 + 마진. thinking 여유까지 합쳐도 600 이면 충분.

5. **`_fallback_brief_for` 는 유지하되 더 이상 호출되지 않을 가능성 높음** — 모든 섹션이 부분적으로 살아남음. 그대로 둠.

**Acceptance**:
- `_build_user_for_date` 는 더 이상 사용 안 됨 (또는 삭제). 호출처 검색 후 정리.
- 기존 dry-run CLI 동작 유지: `uv run agents/daily_brief.py --dry-run --force`.
- 빈 응답이 와도 brief 파일 안의 📌/🧪/🧭 섹션은 정상 마크다운.

---

### Step 6 — 테스트 (`tests/`)

**작업**: 신규 `tests/test_daily_brief_decoupled.py` 추가. 기존 `tests/test_daily_brief_empty.py` 가 있으면 새 흐름에 맞게 갱신.

**테스트 케이스 (최소)**:

1. `test_pick_highlights_basic` — items 5개, recent_urls 1개 겹침 → picks 3건이고 그 URL 제외.
2. `test_pick_highlights_diversity` — items 5개 모두 같은 카테고리 1개 + 다른 카테고리 1개 → picks 3건이 모두 같은 카테고리는 아님.
3. `test_pick_highlights_no_diversity_possible` — items 5개 모두 같은 카테고리 → picks 3건 모두 같은 카테고리여도 OK (억지 다양화 X).
4. `test_classify_difficulty_short` — 짧은 what_to_try → ⭐.
5. `test_classify_difficulty_long_keyword` — "프로토타입 구축..." → ⭐⭐⭐.
6. `test_render_highlights_empty` — picks=[] → "(어제 수집분 없음)" 포함 마크다운.
7. `test_render_experiments_skips_empty_what` — what_to_try 빈 아이템 스킵.
8. `test_generate_one_with_llm_failure` — `call_sonnet` 이 빈 문자열 반환하도록 mock → brief 파일 안에 📌/🧪/🧭 가 정상 출력되고 🔥 자리에 fallback 라인.

**Acceptance**:
```bash
cd /Users/aejin/wiki-agent && uv run pytest tests/ -v
```
모두 통과.

---

### Step 7 — dry-run + 실제 입력 검증

**작업**:

1. dry-run 으로 4/26 brief 생성 시뮬레이션:
   ```bash
   cd /Users/aejin/wiki-agent && uv run agents/daily_brief.py --dry-run --force 2>&1 | tee /tmp/brief-dryrun.log
   ```
   확인:
   - `picks=3` 로그
   - dry-run 출력에 LLM user prompt preview 가 짧은지 (이전 대비 ~70% 감소 기대)
   - LLM 호출은 안 함 (dry-run)

2. 실제 호출 1회 (사용자 승인 후):
   ```bash
   cd /Users/aejin/wiki-agent && uv run agents/daily_brief.py --force
   ```
   결과 `wiki/daily/2026-04-26.md` 가 정상 4섹션(🔥/📌/🧪/🧭) 모두 채워졌는지.
   `lib/llm.py` 의 새 INFO 로그에서 `thoughts_token_count` 가 작은지 (수백 토큰 수준 기대).

3. 두 결과를 `docs/features/brief-llm-decoupling.md` 의 **진행** 섹션에 기록.

**Acceptance**:
- 생성된 brief 마크다운에 4섹션 모두 존재.
- LLM 호출 1회. `finish_reason == STOP`.
- 문제 발견 시 즉시 롤백 가능 (이 PR 은 단일 커밋 권장).

---

## 진행

- 2026-04-25: Step 1 완료 (`e2d668c`) — `lib/llm.py::_generate` 에 usage + finish_reason INFO 로깅, non-STOP 시 WARNING 추가. 기존 `call_haiku`/`call_sonnet` 시그니처 변경 없음. 전체 pytest 111 통과. 검증 호출에서 root cause 재현됨: `max_tokens=100` 호출 시 `thoughts_tokens=97, candidates_tokens=0, finish_reason=MAX_TOKENS` → 4/24 brief 절단 메커니즘 그대로 확인.
- 2026-04-25: Step 2 완료 (`fce2a75`) — `agents/daily_brief.py` 에 `_filter_recent`, `_score`, `_pick_highlights` 3개 헬퍼 추가 (spec 은 "4개" 라 적혀 있으나 명시 시그니처 3개에 맞춤). `_score` = `confidence` 기본 0.5, 비정상 값도 0.5 로 안전 fallback. `_pick_highlights` 는 mutate 없이 필터+점수 정렬+"3개 모두 동일 카테고리" 만 차순위 교체. 스모크 — `x×3, y, z` 에서 top3 [a,c,d] 원형 유지; `x×3, y` 에서 [a,b,d] 로 y 끌어올림. pytest 111 통과.
- 2026-04-25: Step 3 완료 (`a7b0f00`) — `agents/daily_brief.py` 에 렌더 헬퍼 6종 + `_WEEKDAY_KO` 상수 추가. `_classify_difficulty` 는 hard 키워드 우선→easy 키워드/60자 미만→기본 ⭐⭐ 순서로 휴리스틱 판정. `_render_highlights`/`_render_experiments`/`_render_wiki_changes` 모두 섹션 헤더 자체 포함 (Step 5 에서 `\n\n.join` 으로 합칠 예정). 테이블 셀 내 `|`·개행 escape 처리. 빈 입력/빈 필드에도 KeyError 없이 placeholder 로 fallback. pytest 111 통과.
- 2026-04-25: Step 4+5 완료 (`6a71f33`) — 번들 커밋 (nightly 호환 위해). 프롬프트 `prompts/daily_brief.md` 698B 로 교체 ("중복 추천 금지"/"주제 다양성"/"🧪 실험" 모두 제거, 3줄 불릿만 명세). `_build_user_for_date` 삭제, `_build_summary_user` + `_recent_highlight_urls` 신규. `_generate_one` 재작성: picks 3건 코드 선별 → LLM 은 🔥 3줄만 호출 (`max_tokens=3500→600`). fallback 메시지는 기존 테스트의 "LLM empty response"/"토큰 캡" 키워드 보존. dry-run 검증: items=9 picks=3 (카테고리 자동 다양화), user 프롬프트 preview 대폭 축소. pytest 111 통과.

- [x] Step 1: 진단 로깅
- [x] Step 2: 점수·필터·다양성 헬퍼
- [x] Step 3: 마크다운 조립 헬퍼
- [x] Step 4: 시스템 프롬프트 압축
- 2026-04-25: Step 6 완료 (이 커밋) — `tests/test_daily_brief_decoupled.py` 신규 8 테스트 (pick_highlights 3 · classify_difficulty 2 · render 2 · generate_one empty LLM 1). pytest 119 통과 (신규 8 + 기존 111). `test_empty_llm_response_keeps_other_sections` 가 Step 5 의 부분 실패 내성(🔥 fallback + 📌/🧪/🧭 정상 출력) 을 직접 검증.

- [x] Step 5: 메인 흐름 리팩터링
- [x] Step 6: 테스트
- [ ] Step 7: dry-run + 실제 검증

## 다음

- 위 Step 1 부터 차례대로 Claude Code 에 던짐
- Step 7 통과 후 nightly 자연 실행으로 이튿날 brief 결과 확인
- 안정화되면 후속 PR 들:
  - classifier 단계에서 난이도 미리 매기기 (brief 휴리스틱 제거)
  - `tag_freshness`, `personal_fit` 점수 추가
  - 🧭 위키 변화 — Curator 결과 파일 읽어 채우기
- 카테고리 enum 확장은 별도 작업 (Curator 1회 완전히 돈 후 검토)
