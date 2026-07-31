# Claude 변경 기록 (Codex 협업용)

> Claude(Opus)가 수정한 지점을 남긴다. **Codex는 이 목록을 먼저 보고 같은 로직을
> 중복 구현하지 말 것.** 규제 수치는 데이터파일에서만(하드코딩 금지),
> 가능 모델 목록은 `orchestrator._model_options_for_diagnosis()`만 — CLAUDE.md 준수.

## 2026-07-31

### 검토의견도 실질 배치 불가 반영(전국, flag 기반)
- 전체용도 검토의견 프롬프트가 첫 문장을 무조건 "건축 가능/조건부 허용"으로 강제해, 배치 불가
  (기존건물·협소)인데도 가능한 것처럼 나오던 문제. placement_restricted 또는
  map_presentation.verdict=not_allowed이면 배치 불가 결론으로 시작하도록 프롬프트 조건화
  (LLM이 flag·existing_buildings를 해석; 텍스트 하드코딩 아님). 결정적 폴백
  `_all_uses_verdict_judgment`도 동일 분기 추가. 주소 하드코딩 없이 전국 적용.

### 건축물대장 juso 주소 폴백(대단지·구축 아파트 기존건물 검출)
- 문제: VWorld `get_parcel(좌표)`는 좌표가 떨어진 **토지 필지 PNU**를 주는데, 건축물대장은
  **건물 대표지번**에 등록될 수 있어(예: 한내로 62 한신아파트 → 토지 1095 vs 대장 1093-4)
  PNU 조회가 0건 → 기존건물 미검출 → 배치 불가가 안 걸리던 문제. 전국 대단지에서 재발 가능.
- 수정: `building_register`에 juso.go.kr 도로명주소 검색을 폴백으로 추가. **PNU로 0건일 때만**
  `_juso_loc(address)`로 건물 대표지번을 얻어 표제부를 재조회(`_query_title` 공통 추출로 중복
  없음). `lookup(pnu, address=...)` 시그니처만 확장, 나머지 호출부는 그대로. config에
  `JUSO_CONFM_KEY` 추가, prediagnosis가 `matched_address` 전달. 주소 하드코딩 없이 전국 적용.
- 검증: 한내로 62 → 15동 검출(source '주소 보정')·실질 배치 불가, 작전동 947 → PNU로 6동(폴백
  안 탐), 신수리 100-2 빈땅 → 0건(오검출 없음)·조건부. unittest 106 OK.

### 배치 제한 신호 일반화(placement_restricted): 협소 + 기존건물
- 협소(min_lot_area)와 기존 건축물(existing_buildings.has_buildings)은 둘 다 '신축 배치
  제한'이라 `prediagnosis`에서 단일 `placement_restricted` 신호로 통일. 흩어진 min_lot_area
  게이트 4곳(map_control 매스·배지, orchestrator _model_options·set_panel_context)을 이 신호로
  일반화(새 파일·새 로직 없음, 기존 '실질 배치 불가' 재사용).
- 검증: 작전동 947(기존건물)·100-2(협소) → 배지 '실질 배치 불가'·매스X·모델X,
  신수리 100-2(빈땅) → 조건부·매스·모델 정상. 자연어(검토의견)는 LLM이 그대로.

### 후속에서 '다른 주소 건물 가능?' → 이동+진단 (LLM 판단, 정규식 X)
- `_interpret_followup`에 `target_address` 추가(제미나이가 '다른 주소로 가서 건축 가능한지'
  판단 시 그 주소를 담음). continuation 블록에서 target_address면 `_diagnose_and_emit`로
  이동·재진단. 도로명도 처리, 언급뿐이면 이동 안 함(A: 한내로 62→서울 이동+카드, B: '도로
  접촉 있어?'→이동 X 검증).

### 협소 필지(법정 최소 대지면적 미만) — 배치 제한 안내 + 모델 숨김
- 사유: 22.9㎡ 필지가 조건부 가능+모델로 나오는데 현실적으로 협소해 배치 불가.
- `data/min_lot_area.json`(건축법 시행령 제80조 값)·`tools/min_lot_area.py::check(zone,area)`.
- `prediagnosis`: 대지면적<법정 최소면적이면 `diagnosis.min_lot_area` 설정.
- `orchestrator._model_options_for_diagnosis`: min_lot_area 있으면 모델 숨김.
- `orchestrator.render_pending_judgment`: 검토 의견 앞에 협소 사유("조건부 가능이나 배치 제한").
- 배지·매스도 협소 반영(기존 '실질 배치 불가' 메커니즘 재사용):
  `map_control`: `layout_infeasible`에 `diagnosis.min_lot_area` OR 추가 → 배지 '실질 배치 불가';
  매스 게이트에 `not min_lot_area` 추가 → 협소면 매스 안 세움.
  `orchestrator` set_panel_context: 협소면 '조건부 가능' 배지로 덮지 않음(배치 불가 유지).
  `min_lot_area._category`: 카테고리 목록을 데이터파일에서 읽음(함수에 안 박음).
- 검증: 작전동 100-2 → 배지 '실질 배치 불가'·매스X·모델X·검토의견 협소, 정상 필지 무영향, unittest OK.

### 모델 클릭: 3D 렌더 실패가 검토용도 갱신을 막지 않게
- `frontend/App.tsx` 모델 클릭 핸들러: `showHousingModel`이 '유효한 매스 없음'으로 throw하면
  같은 try 안의 `setback-for-use`(검토용도·이격 갱신)까지 중단되던 문제. 3D 렌더만 별도
  try로 감싸, 협소·배치불가 필지에서도 검토용도는 클릭한 용도로 갱신되게 함.

### '다른 건물 가능?'(possible_models) 배지도 검토 범위에 맞게 갱신
- 문제: "판매시설 불가"(배지 불가) 뒤 "다른 건물 가능?"을 물으면 검토용도만
  "가능한 건축물 전체"로 바뀌고 **배지는 '건축 불가'로 남아** 문구(조건부)와 어긋났다.
  원인 = `set_panel_context`가 verdict를 안 실어 보냄.
- 수정(최소 3곳):
  - `orchestrator.py` possible_models: `set_panel_context`에 verdict 실음 —
    zone_use_overview에 allowed/conditional 용도가 있으면 conditional(조건부 가능/#F9A825).
  - `frontend/lib/mapBridge.ts`: set_panel_context 타입에 verdict/verdict_label/verdict_color(optional) 추가.
  - `frontend/App.tsx`: set_panel_context 처리 시 배지(color·verdict_label)도 갱신.
  - 프론트 빌드 완료(dist 갱신). 모델 옵션·판정 로직은 그대로(모델은 계속 보임).
- 검증: 작전동 100-2 판매시설(불가)→다른건물 → set_panel_context 배지 "조건부 가능/#F9A825".

### 진단 스트리밍: 지도·종합판정 먼저, '검토 의견'은 이어서(체감 속도 개선)
- 병목 측정: 진단 파이프라인 ~4s(이미 병렬) + 카드의 검토 의견 LLM ~5s. 그런데 모든
  이벤트를 리스트로 모아 끝에 방출해 지도가 준비돼도 판정 LLM까지 기다렸다 한꺼번에 떴다.
- 해결(마커 방식, 호출처 9곳 미변경):
  - `_diagnose_and_emit` emit_card: 검토 의견을 즉시 계산하지 않고 종합판정 카드·가능 모델을
    먼저 events에 넣은 뒤 `{"event":"pending_judgment","data":{"query"}}` 마커만 남긴다.
  - `Orchestrator.render_pending_judgment(query)`(신규): 단일/전체 용도 판단 문단을 계산해
    `## 검토 의견` message 이벤트로 반환(타임아웃·결정적 폴백 포함).
  - `main.py` produce 루프: pending_judgment 마커를 가로채 render_pending_judgment 호출→방출.
    마커는 프론트로 넘기지 않는다.
- 검증: 프록시 경유로 지도+종합판정+가능모델 1.8s, 검토의견 9.8s(이전 전량 ~10s). unittest 106.
- 주의: 검토 의견이 카드 내 섹션→별도 message 버블로 분리됨(위치만 바뀜, 내용 동일).

### 침범 배수로 라벨·유의사항에 방류 목적지(구거/도로/하천) 명시
- 사용자가 "끝 필지가 도로가 아닌데 왜 그리로?" 혼동 — 실제 끝점은 가장 가까운 공공
  배수처(구거 등)인데 라벨이 목적지를 안 보여줬다. `_encroachment_route`가 방류 끝점이
  닿는 공공 배수처(`outlet`={jimok,address})를 담고, map_control 라벨을 "우수 방류→구거 ·
  사유추정지(답·전) 통과 …", prediagnosis 유의사항을 "가장 가까운 공공 배수처인 구거
  (…두리 824)까지 …"로 명시. `_OUTLET_DISPLAY`로 지목→표시명(도/구/천→도로/구거/하천).
- 검증: 두리 96-7 → 목적지 구거(두리 824, 86.6m·구거가 최근접, 북측 도로 117.9m보다 가까움).

### 건축선 색 충돌 해소 (침범 배수로 빨강과 겹칠 때)
- `map_control._build_dimensions`: 사유지 침범 배수로가 빨강(#C62828)으로 그려질 때
  (`drainage.encroachment.crosses_private`)만 건축선을 보라(#7E57C2)로 바꾼다. 침범이
  없으면 건축선은 기본 빨강(#E53935) 유지. 한 화면에 두 빨강이 겹쳐 구분 안 되던 문제.
- 검증: 두리 96-7(단독주택, 침범)→건축선 보라, 신수리 100-2(정상)→건축선 빨강 유지.

### 문서 현행화 (README · ARCHITECTURE · SYSTEM-SPEC)
- 이번 세션 신기능을 사용자용 3종 문서에 반영(코드 변경 없음, 문서만).
  - README: 도구 표에 road_access(배수)·land_ownership·building_register, '최근 추가
    기능' 섹션, DATA_GO_KR_SERVICE_KEY·LAND_OWNERSHIP_API_URL env.
  - ARCHITECTURE: 4.3 선 오버레이/build_lines_only, 4.4 도구 21개(+land_ownership),
    road_access 배수 설명, 7절 env.
  - SYSTEM-SPEC: 5.3 선 오버레이·선만 렌더·LLM 의도해석, 6.0 함수 일람, 9절 env.
- CHANGELOG-claude.md(이 파일)·CONVERSATION-STATE.md 도 이번 세션 내내 갱신 유지.

### 후속질문의 선 의도는 제미나이 해석으로(하드코딩 제거)
- 후속(continuation) 경로에서 어떤 선을 그릴지를 키워드 정규식(`_requested_map_lines`)
  대신 **`_interpret_followup`(LLM)의 `map_lines`** 로 판단한다. 의도 기반이라 "접한 길
  보여줄래?"·"빗물 어디로 빠져?"·"건물 후퇴선 어디쯤?" 같은 표현도 각각 road/drainage/
  building_line 으로 해석. 추가 LLM 호출 없음(_interpret_followup 은 이미 호출됨).
- `_interpret_followup` tool 스키마에 `map_lines`(enum road/building_line/drainage) 추가 +
  프롬프트 지침(단어 아닌 의도로 판단, 아니면 빈 배열).
- 새 필지 첫 질문 라우팅은 안전하게 현행 유지(정규식) — 핵심 추출/재작성 파이프라인 미변경.
- 검증: 패러프레이즈 4종 정확 분류, 비선질문(공시지가)은 선 없음. unittest 106 통과.

### '선만' 묻는 질문은 카드·매스·팝업 없이 선만 (새 필지 첫 질문 포함)
- "건축선 그려줘"·"도로 접촉 있어?"처럼 선만 청하는 질문은, 새 필지 첫 질문이어도
  종합판정 카드·3D 매스·가능여부 팝업을 내지 않고 요청한 선만 그린다.
- `orchestrator._is_line_only_query(query)`: 선 요청이면서 가능여부·규모를 함께 묻지
  않는 질문 판정(원문 기준 — 좌표 주입 후 문장엔 '건축 가능 여부 진단'이 섞여 오탐).
- `orchestrator.ask`: coordinate_diagnosis 앞에 line-only 라우팅 추가. 같은 필지 진단이
  있으면 `build_lines_only_commands`, 없으면 `_diagnose_and_emit(lines_only=...)`로 조용히
  진단 후 선만. 답은 `_natural_followup_answer`(카드 아님).
- `_diagnose_and_emit(lines_only=[...])`: 진단은 돌리되 _render_event·모델·카드 대신
  build_lines_only_commands 만 방출.
- `map_control.build_lines_only_commands(diagnosis, kinds)`: build_map_commands 결과에서
  clear_mass·fly_to·highlight_parcel만 남기고 overlay_command(요청 선) 추가.
- 검증: 새 필지 첫 "도로 접촉 있어?"→도로접촉선만/팝업·매스·카드 없음, "창고 지을 수
  있어?"→정식 진단 회귀 정상. CONVERSATION-STATE.md에 예외 명문화. unittest 106 통과.

### 배수 침범 판정 근거를 '소유구분(국/공/사유)'으로 (토지소유정보 API)
- 사유지 여부의 1차 근거를 지목 proxy → **실제 소유구분**으로 격상(건물 유무는 보조).
- `backend/app/tools/land_ownership.py`(신규): `lookup_ownership(pnu)` — 공공데이터포털/NSDI
  '국토교통부_토지소유정보' 속성 API로 소유구분(posesnSeCodeNm) 조회 → "국공유"/"사유"/None.
  응답 형태가 기관마다 달라 방어적 파싱(필드명 posesn·소유 힌트 + 값 토큰). 엔드포인트는
  `LAND_OWNERSHIP_API_URL` env 로 덮어쓰기 가능. 키는 기존 `DATA_GO_KR_SERVICE_KEY` 재사용.
- `road_access.assess`: encroachment 통과필지(최대 3)에 소유구분(1차)+건물(보조) 조회.
  `crosses_private` 재계산 — 국공유면 통과가능(차단 아님), 사유·미상이면 차단.
- `map_control`/`prediagnosis`: 라벨·유의사항을 소유구분 인지형 3갈래 —
  사유(확인)/사유추정(미상, "소유구분 확인")/국공유(통과가능, 파랑). 건물有는 "사실상 우회".
- **운영**: data.go.kr '토지소유정보' 오픈API(속성정보) 활용신청 필요(사용자 진행 중).
  미연동/조회불가면 ownership=None → 지목 proxy 로 폴백(사유추정). unittest 106 통과.

### 배수 사유지 침범 시나리오 (가장 가까운 공공 배수처까지 빨강 경로 + 건물 유무 2갈래)
- 인접에 공공 배수처가 없을 때만, 배수로가 남의 사유지를 지나야 하는 상황을 시각화한다.
- `backend/app/tools/road_access.py`:
  - `_PUBLIC_DRAIN_JIMOK`(도/도로/구/구거/천/하천), `_WIDE_DRAIN_PAD`(약 180m).
  - `_encroachment_route(parcel, candidates, target_pnu, inverse)` — 넓게 조회한 필지들에서
    가장 가까운 공공 배수처까지 '개념' 직선 경로를 만들고, 그 선이 지나는 **사유지 필지 검출**
    (`crosses_private`, `crossed_parcels`[jimok/address/pnu/cross_length_m]).
  - assess: `_route`(파랑) 없고 `public_outlet is False`면 wide fetch → encroachment 계산.
    통과 사유지(최대 3필지)에 대해 **`building_register.lookup(pnu)`로 건물 유무만 확인**
    (`has_building`; 소유권 아님 — 소유구분은 토지대장 필요, 미연동).
- `backend/app/agents/map_control.py` `_build_dimensions`: encroachment.route_geometry 를 **빨강**
  세그먼트로. 라벨 2갈래 — 건물 有 "⚠ 건물 있는 사유지 통과·사실상 우회", 건물 無 "⚠ 사유지
  통과·토지사용승낙/우회". overlay_command(drainage) 도 포착("우수 방류" 접두어 유지).
- `backend/app/agents/prediagnosis.py` 유의사항: 같은 2갈래로 안내(지목=사전검토, 건물=건축물대장,
  소유권=토지대장 확정 명시). 검증: 합성 맹지 시나리오로 assess/map_control 양쪽 확인.

### 자연어로 특정 선만 지도에 다시 그리기 (도로접촉·건축선·이격·배수로)
- 사용자가 "도로 접촉 있어?/진입로 있어?/건축선 그려줘/우수 배수로 어디로" 처럼 물으면
  카메라·3D 매스는 그대로 두고 **그 선만** 지도에 다시 얹는다.
- `backend/app/agents/map_control.py`:
  - `overlay_command(diagnosis, kinds)` — `_build_dimensions` 세그먼트를 라벨로 필터해
    road/building_line/drainage 만 골라 `show_dimensions` 로 반환(카메라·매스 없음).
  - `_may_show_building_dimensions()` — 건축선·이격선은 가능/조건부 판정일 때만(불가·확정
    용도제한이면 제외). build_map_commands 게이팅과 동일 규칙.
  - `_OVERLAY_LABEL_KINDS` — 라벨 접두어 매핑(도로 접촉/건축선·전면이격·정북일조·인접이격/우수 방류).
- `backend/app/orchestrator.py`:
  - `_requested_map_lines(query)` — 자연어에서 그릴 선 종류 추출(백엔드 단일 규칙).
  - continuation 후속 경로(`continuation and self.diagnosis`)에서 `overlay_command` 방출.
  - LLM tool `show_map_lines`(kinds) 추가 — 비-continuation LLM 루프 경로 커버. 둘 다 overlay_command 공용.
- 검증: 신수리 100-2 — 도로접촉 38.5m, 진입로, 건축선(이격 후)+전면이격 3m, 우수 방류→도로 모두 표시.

## 2026-07-30

### 우수·오수 배수 방류처 감지 + 가상 배수로 (진행 중)
- `backend/app/tools/road_access.py`
  - `_DRAINAGE_OUTLET_JIMOK` (구거 '구'/'구거', 하천 '천'/'하천'), `_drainage(roads, adjacent_nonroads)`:
    인접 지목 도로측구·구거·하천을 공공 배수처로 감지 → `drainage`(public_outlet/outlets/note).
    없으면 "사유지 우회·토지사용승낙 필요" note.
  - `_drainage_route(parcel, outlet_geometries, inverse)`: 필지 내부점→가장 가까운 방류처까지
    '개념' 배수로 LineString(WGS84) → `drainage.route_geometry`.
  - assess 루프에서 `drainage_geometries`(구거·하천 형상) 수집. 두 반환(NO_CADASTRAL_ROAD,
    CADASTRAL_CONTACT) 모두 `"drainage": drainage_info` 사용.
- `backend/app/agents/map_control.py`: `_build_dimensions`에 배수로 세그먼트 추가(완료).
- 프론트: 기존 `show_dimensions` 세그먼트 재사용(신규 명령 없이, 완료).
- 원칙: 사전검토(개념)일 뿐, 실제 경로·방류지점은 **설계사무소 현장확인·현황측량**으로 확정.

### 기존 건축물 철거·개축 분기 (카드 유의사항, 결정적)
- `backend/app/agents/prediagnosis.py::format_diagnosis_answer` 유의사항 블록:
  - `existing_buildings.status == "FOUND"` 이면 → 신축 시 철거·멸실 선행 안내.
  - `regulation.constraints` 에 '개발제한/보전' 감지되면 → 개축·재축 한정·신축 제한 분기.
  - `road_access.drainage.public_outlet is False` 이면 → 배수 우회 안내.
- compact()는 blacklist 방식이라 existing_buildings·road_access를 이미 LLM에 넘김(프롬프트 미수정).

### 이격(대지 안의 공지) — 용도별 실제 수치
- `backend/app/tools/setback_rules.py`: `applicable_setbacks(jur, zone, gross, exclude_use)`,
  `setback_uses`, `describe_rules`, `_when_text`. 전부 `setbacks.json`(119개 지자체)에서 읽음.
- `prediagnosis.py` 카드 이격 문구: 이 필지 규모 기준 용도별 실제 이격 수치 표기.

### 용도 매핑 · 판정표
- `prediagnosis.py`: `_AMBIGUOUS_USE_TERMS`/`_USE_KEYWORDS`/`_SHOP_TERMS`에 상업시설·상업·학교·
  교육연구시설·주택 추가. 불가 경고 라벨 "○○ 건축 불가"로 통일.
- `backend/app/tools/zoning.py`: `USE_MATRIX`에 **교육연구시설** 추가(국토계획법 별표 기반).
  조건부 사유에 실제 조례 조문 인용 + 설계사무소 문의 안내.

### Docker 완전판 (산지 SQLite 볼륨)
- `backend/app/tools/ogc.py`: `${VAR:-기본값}` env 치환 지원.
- `backend/app/data/spatial_layers.json`: forest local_path `${FOREST_SQLITE_PATH:-절대경로}`.
- `compose.yaml`: backend 산지 볼륨 마운트(`${FOREST_DATA_DIR}`→/data/forest) + FOREST_SQLITE_PATH.
- `.env.example`: FOREST_DATA_DIR 안내.

### 문서
- README / docs(ARCHITECTURE, SYSTEM-SPEC, -docx): 조례 커버리지(약200/119), 데이터 저장소
  (TF-IDF 벡터·SQLite RTree), GCP·Gemini, 에이전트 4개(3D(매스)), 도구 20개로 현행화.
