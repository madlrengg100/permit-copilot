# Claude 변경 기록 (Codex 협업용)

> Claude(Opus)가 수정한 지점을 남긴다. **Codex는 이 목록을 먼저 보고 같은 로직을
> 중복 구현하지 말 것.** 규제 수치는 데이터파일에서만(하드코딩 금지),
> 가능 모델 목록은 `orchestrator._model_options_for_diagnosis()`만 — CLAUDE.md 준수.

## 2026-07-31

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
