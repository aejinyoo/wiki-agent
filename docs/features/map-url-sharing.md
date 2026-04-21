# map-url-sharing

**상태**: 계획 · **업데이트**: 2026-04-21

## 요약
Naver Map / Google Maps 공유 링크를 전용 어댑터로 수집하는 `lib/fetchers/map.py` 신설. 맛집·카페·장소 아카이빙에 사용. 기존 generic fetcher는 지도 링크에서 JS 렌더링 / 앱 딥링크 리다이렉트로 쓸모 있는 본문을 못 뽑음.

**스코프**:
- 신규 fetcher: `lib/fetchers/map.py` + `__init__.py::_DISPATCH` 등록
- `_infer_source` 에 `Map` 소스 추가 (naver.me, naver.com/place/, maps.app.goo.gl, google.com/maps 등)
- 최소 추출 필드: **장소명, 주소, 좌표(lat/lng), 카테고리(맛집/카페/…), 지도 URL**
- classifier 프롬프트에 Map 소스 분기 — 일반 텍스트 분류 대신 "장소 카드" 포맷으로 요약 (주소 + 한 줄 인상 + 왜 저장했는지)
- 신규 wiki 카테고리 `places` 추가 또는 기존 `trend-reports` 재활용 판단 → 초안은 **신규 카테고리**

**Out of scope**:
- 지도 임베드 렌더 (wiki-site 에서 iframe/정적 맵 이미지) — 별도 후속
- 방문 체크인·별점 관리
- 다국어 주소 정규화

## 진행

_(착수 전)_

## 다음
- [ ] **M1** URL 패턴 조사 — naver.me 단축, naver.com/place/{id}, map.naver.com/?lng=...&lat=..., maps.app.goo.gl, google.com/maps/place/... 각각 응답 구조 확인
- [ ] **M2** fetcher 구현 — naver는 HTML 메타/og 우선, google은 ?q= 파라미터 파싱 + og 태그
- [ ] **M3** `WikiItem` 메타 확장 판단 — `place_name`, `address`, `lat`, `lng`, `place_category` 를 별도 필드로 vs. `metadata` dict 로. 지도 전용이면 dict 가 단순
- [ ] **M4** classifier 프롬프트 분기 — Map 소스일 때 `body_ko` 대신 "장소 요약" 섹션 생성
- [ ] **M5** wiki-site 렌더 — 지도 링크를 "클릭 가능한 주소 + 작은 지도 아이콘" 으로 표시 (임베드는 후속)

## 결정 (옵션)

### 2026-04-21: 맛집 아카이빙을 트리거로 지도 어댑터 분리 (기획 단계)
- 왜 지금: IG 맛집 공유가 누적되면서 "위치 기반 검색" 욕구가 커짐. IG placeholder 로는 주소·위치가 잡히지 않아 generic fallback 으로는 불가.
- 대안 고려: IG 캡션만으로 버티기 → 주소 누락·오타 잦음. 별도 앱(ex. Mymap) 이중관리 → 스위치 비용. 전용 어댑터 선택.

## 링크
- 유사 구조 참조: `lib/fetchers/instagram.py` (og 메타 파싱)
- 영향 파일(예상): `lib/fetchers/map.py` (신규), `lib/fetchers/__init__.py`, `lib/validate.py::_infer_source`, `prompts/classifier.md`, `agents/classifier.py::_compose_body`
