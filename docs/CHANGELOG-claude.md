# Claude 변경 기록 (Codex 협업용)

> Claude(Opus)가 수정한 지점을 남긴다. **Codex는 이 목록을 먼저 보고 같은 로직을
> 중복 구현하지 말 것.** 규제 수치는 데이터파일에서만(하드코딩 금지),
> 가능 모델 목록은 `orchestrator._model_options_for_diagnosis()`만 — CLAUDE.md 준수.

## 2026-08-24
### 전국 공간데이터 5종 반입 + 외부 API 4종 연결 (신규 서버)
- `backend/data/processed/` 에 산지구분(1,066,806건)·1:5,000 임상도(3,381,067건)·
  생태·자연도(1,599,058건)·별도관리지역(24,944건)·Copernicus DEM 을 배치했다. 산지구분과
  임상도는 원본 ZIP 에서 `import_forest_shp.py`·`import_forest_inventory.py` 로 변환했다.
  `/api/spatial-layers` 6개 레이어 전부 활성. `spatial-data-manifest.json` 을 실제 파일
  기준으로 재생성하고 `spatial_data_package.py verify` 로 5개 파일 크기·SHA-256 대조 통과.
- VWorld·Gemini(LLM)·공공데이터포털(건축HUB 건축물대장)·국가법령정보센터를 연결했다.
  `LAW_OPEN_API_OC` 는 가입 이메일 앞부분이 아니라 신청 시 지정한 활용 ID 다.
- 서버 이동으로 존재하지 않는 계정(madlrengg100) 경로가 6곳 남아 있었다. Docker 는
  compose 가 env 로 덮어써 가려졌지만 systemd 직접 실행에서는 env 가 빠지는 순간 조용히
  실패한다. spatial_layers.json(SQLite 4개)·terrain.py(DEM)·main.py(세션 디렉터리)를 정정.
- `requirements-dev.txt` 신설. tests/ 에 33개 테스트가 있는데 pytest 가 어느 requirements
  에도 없어 실행 자체가 안 되던 상태였다. 운영 이미지는 requirements.txt 만 설치한다.

### 법령명 추출 정규식 — 앞 조문의 '조' 가 뒤 법령명을 삼키던 문제
- "건축법 제11조 및 국토의 계획 및 이용에 관한 법률 제56조" 에서 두 번째 법령명이
  "조 및 국토의 계획 및 이용에 관한 법률" 로 잡혀 검색이 안 되고 현행 법령 검증에서
  통째로 빠졌다. 조문·항·호 표기를 먼저 구분자로 치환하고 그 구분자를 법령명 경계로 쓴다.
  '및' 은 법령명 안에도 쓰이므로(국토의 계획 및 이용에 관한 법률) 일괄 제거하지 않고
  이름 맨 앞에 올 때만 떼어낸다. '…법에 따른' 형태도 이제 추출된다. 회귀 테스트 3건.
- 인증 거부(OC·서버 IP 미등록)는 HTTP 200 + {"result","msg"} 로 오는데 'law' 키가 없다는
  이유로 '법령 못 찾음' 과 똑같이 처리되어 원인이 화면에서 사라졌다. `LawOpenAuthError` 로
  잡아 NOT_AUTHORIZED 상태와 API 원문 사유를 노출한다.

### 용도지역 범례를 토지이음 표기에 맞춤
- 토지이음은 "도시지역 / 제1종일반주거지역" 처럼 국토계획법 제36조 대분류와 세분을 함께
  적는데 연속주제도 WFS 는 세분만 준다. 범례 첫 줄에 대분류를 넣는다(`zone_tier1`,
  **표기 전용 — 판정 수치에는 쓰지 않는다**).
- WFS 는 면적으로 조각을 만들어 아주 작은 조각이 아예 생성되지 않는다. NED 지정목록에만
  있는 용도지역을 '저촉(면적 미미)' 로 목록에 덧붙이고, 표시 조건도 'WFS 조각 >= 2' 에서
  'WFS 조각 + NED 전용 >= 2' 로 넓혔다(조각 0개면 지도·색을 만들 수 없어 최소 1개 요구).
- **지도 조각은 여전히 WFS 것만 쓴다. share_pct 와 국토계획법 제84조 가중평균은 불변.**
- 전국 42개 필지를 토지이음(VWorld NED 지정목록)과 대조: 세분 기준 완전일치 39/42(92.9%)
  → 세 조치 후 42/42(100%), 불일치 0. 특정 PNU 하드코딩은 하지 않았다.

### 시행일자 표기를 YYYY-MM-DD 로 통일
- "(시행 20251117)" 처럼 구분자 없이 노출됐다. `tools/textfmt.kdate()` 로 공용화하고
  prediagnosis 에 복붙돼 있던 같은 변환 두 곳도 이 헬퍼로 정리했다.
- 날짜 **필드** 값에만 적용한다. 문장 전체를 정규식으로 훑으면 조례 원문의 다른 번호
  (별표 12345678 → 1234-56-78)까지 망가진다. 실재하지 않는 날짜(20251345)도 원문 유지.
- 데이터는 '시행 ' 뒤 8자리만 정규화(239건)하고 조례명·조문·표 설명 원문은 건드리지 않았다.
  재생성 시 되돌아가지 않게 `parse_setback_tables.py`·`parse_setbacks_grid.py` 도 수정.
- 저장값(`effective_date`)은 법령 API 원본 형식이라 8자리 그대로 두고 표시할 때만 변환한다.

## 2026-08-06
### 이격 기하·분할 의미·세션 복원 보강 (Codex 병행)
- setback 전면 이격 기하 계산, 도로접촉선-분할 의미 정합, 이격 기본 규칙, 세션 복원 시
  이격 상태 유지 등을 보강하고 테스트 추가(test_front_setback_geometry·
  road_contact_division_semantics·setback_default_rule·setback_session_restore). 프런트
  App·api·mapBridge 보강(분할 전후 전환 시 직전 이격 치수선 폐기 = clearDimensions(true)).
  llm.py request_timeout 인자. 전체 140 테스트 통과.

### '검토 의견 작성 중' 진행 표시 제거
- 검토 의견 계산 전 흘리던 tool_start{judgment}('검토 의견 작성 중') 진행 표시가 작성
  완료 후에도 화면에 남아 지저분했다. main.py 소비부에서 그 emission 을 제거 — 검토
  의견 message 는 그대로 방출된다(검증: 진행표시 없음·검토의견 방출 확인).

### '검토 의견 작성 중' 무한 대기 수정 — all-uses 판단 fallback 복구
- 용도 미지정(all-uses) 검토 의견 경로가 timeout 60s + TimeoutError 만 포착 + 결정적
  fallback 제거 상태라, LLM 이 지연/503/에러면 judgment 가 빈 채 render_pending_judgment
  가 None 을 반환 → main.py 소비부가 message 를 안 내보내 프런트가 '검토 의견 작성 중'
  에서 무한 대기했다(Codex 가 원인 못 찾던 증상).
- 수정: (1) all-uses 경로 timeout 60s→14s + Exception 광범위 포착 + `_all_uses_verdict_judgment`
  결정적 fallback 복구(빈 응답이면 반드시 채움). (2) main.py 소비부는 judgment 가 None 이어도
  '작성 중' 스피너가 남지 않게 최소 안내 message 를 반드시 방출. 검증: 425-4(all-uses)에서
  4.2s 작성중 후 6.4s 검토의견 방출.

## 2026-08-05
> 이 날짜 항목이 분할 뷰·범례·주제도 로딩의 **최종 상태**다. 아래 2026-08-03 의 일부
> 항목(안쪽 밀기·경계선 위 띄우기·화이트 범례·도로 편입 #AD1457/후퇴선 표기 등)은
> 이후 되돌리거나 아래 내용으로 대체됐으니 최신 코드 기준으로 이 항목을 따를 것.

### 이격 검수본 오버라이드 패턴 + 음성군 (Codex 병행)
- 자동 표 파싱은 병합 셀을 잘못 이을 수 있어, 원문 이미지/HWP를 사람이 대조한 관할은
  `setbacks_verified.json`(검수본)에 담고 `setback_rules._load()`가 자동파싱값보다 우선
  적용한다(재수집에도 보존). 음성군을 검수본으로 추가, test_eumseong_setbacks.py(총 119).

### 지구단위계획 런타임 근거 연결 + landuse 예외 처리 (Codex 병행)
- `tools/district_plan.py`: 수집·정제 중인 지구단위계획 공식 원문 근거를 진단 결과에
  연결(런타임). `district_plan_sources.json` 참조. orchestrator·prediagnosis 연동.
- `tools/landuse.py`: VWorld 토지이용계획 조회 실패 시 status=UNAVAILABLE payload 로
  안전 처리(용도지역 미조회를 오류 대신 명시적 상태로). 프런트 ChatPanel·mapBridge 보강.
- 테스트 추가: district_plan_evidence, landuse_response_types, permit_legal_evidence,
  concise_verdict_judgment.

### 지구단위계획 문서 파서 + 진단/치수선 테스트 보강 (Codex 병행)
- `scripts/parse_district_plan_documents.py`: 지구단위계획 PDF/HWP/HWPX를 페이지 단위로
  추출하고 스캔 페이지만 OCR(PyMuPDF·Pillow, requirements 추가). 대상 목록은
  `district_plan_sources.json`. 원문→processed JSON 파이프라인(런타임 아닌 오프라인 수집).
- 테스트 추가: test_district_plan_parser, test_map_height_dimension(높이 치수선),
  test_placement_exclusion(기존 건축물 배치제외 완화). 세션 마이그레이션 테스트는 대체·정리.
  전체 125 테스트 통과.

### 양평군 이격(대지 안의 공지) 반영 — 조문 내 이미지 표 (Codex 병행)
- 양평군 건축조례 제23조는 거리 값을 본문·HWP 별표가 아니라 **조문 안 이미지 표**로 담아
  자동수집이 `no_appendix`였다(전국 44곳 동일 유형). 원문 이미지(ELISIMG)를 사람이 확인해
  `setbacks.json`에 반영: 단독주택 전용주거 1/1m, 그 밖 1/0.5m 등. `review_status:
  "image_manually_verified"` + `source_images`(front/adjacent URL)로 출처 명시(수치 미생성).
  테스트 test_yangpyeong_setbacks.py 추가(총 116). LEGAL-ORDINANCE-INDEX 에 no_appendix·
  이미지 표 처리와 시행령 별표2 잔여 gap 문서화. `.cache_setback_images/` gitignore 처리.

### 분할 방법별 법령 근거 구조화 + 접이식 (Codex 병행)
- `_division_scenario_answer` 가 분할 방법마다 근거 조문을 붙인다: 규제 분리 → 건축법
  제57조·시행령 제80조(최소 분할면적)[+녹지 개발행위 시 국토계획법 제56조·시행령 제51조],
  도로 후퇴 → 건축법 제46조·시행령 제31조. 답변을 '## 분할 방법·관련 조례·법령 조문(근거)'
  / '## 분할 후 계산 결과' 헤더로 구조화. 테스트 test_division_view_routing.py 에 검증 추가.
- 프런트 ChatPanel: 그 '분할 방법·근거' 섹션과 '관련 조례·법령 조문'을 접이식(<details>)으로
  렌더(제목 변형 정규식 확장). 결과 패널 근거법도 접이식 정리.

### 분할 뷰 시각 문법 개편 (Codex 병행)
- `map_control.road_setback_pieces`: 접촉선을 **미터 좌표로 투영(shapely.ops.transform)**
  해 buffer·parallel_offset 를 정확히 계산. 편입면(파랑 #1565C0)과, 그 안쪽 긴 경계를
  **'도로후퇴선'(보라 #7B1FA2 평행선, kind="setback")**으로 분리해 그린다. 파란 라벨은
  '도로 편입 예정면적 약 N㎡'로 **면적만** 설명(후퇴 거리는 보라선 담당).
- `division_dimensions`: 분할 경계선을 면 색(빨강)과 분리해 **흰 점선(#FFFFFF,
  kind="division")**으로, 면적 라벨은 '면적 · 분할 대상/제외 N㎡'로. 세그·라벨에 `kind` 부여.
- 프론트 범례: show_zone_pieces 의 `legend_items`(각 항목 `symbol: "area"|"line"`)를 읽어
  면은 swatch, 선은 라인 마커로 구분해 그린다. 분할 범례는 `.zone-legend.is-division` 로 분기.

### 분할 전/후 건축물 토글
- `orchestrator._division_view_request(query)` 가 '분할 전/원본/나누기 전' → "before",
  '분할 후' → "after" 를 **일반 possible_models 표시보다 먼저** 결정적으로 라우팅한다
  (버튼도 같은 원문을 보내 자연어와 단일 경로). 테스트 `test_division_view_routing.py`.
- 분할 후 화면은 일반 가로·세로·높이·도로접촉·배수·이격 치수선을 내보내지 않고
  (map_presentation.show_building_dimensions=False), 분할 대상·제외·경계선·도로편입·
  도로후퇴선만 별도 오버레이로 얹는다.

### 분할 라벨 겹침 방지 + 라벨 박스색 = 면/선 색
- 면적 라벨이 declutter 대상에서 빠져 서로·작은 면 위에 겹쳤다. persist(분할) 라벨 전용
  겹침 방지 채널(`persistLabelAnchors`/`persistLabelDisposer`)을 추가해 정규 치수선과
  독립적으로 서로 밀어내고 카메라를 따라간다(세그·면적 라벨 모두 포함).
- 라벨 배경 박스 색을 해당 면/선 색과 일치(분할 대상 초록·제외 빨강·도로 편입 파랑 등).

### 초기 진단 렌더 속도 — 주제도 스태거
- 진단 결과가 뜰 때 지적도·용도지역이 같은 시점에 네트워크 로드·렌더돼 느렸다. 핵심
  결과(건물·필지·팝업)는 즉시 두고, MapCanvas 가 주제도만 시차로 뒤에 로드한다:
  **용도지역 1800ms → (경계선 수백 개로 가장 무거운) 연속지적도 3200ms.**

### 되돌린 것(참고)
- 도로접촉선을 '안쪽으로 들이기'·'경계선 위로 띄우기'로 겹침 해소하려던 시도, 범례
  '화이트 글래스', 분할 화면에서 show_dimensions 통째 필터(높이 치수선까지 사라짐) 등은
  모두 되돌렸다. 일반 진단 화면의 높이 치수선은 단순 수직 폴리라인으로 복원(주의: 수직선은
  clampToGround 불가라, 지형 타일 로드 전 절대고도로 그리면 밑동이 땅속에 묻힐 수 있음).

## 2026-08-03
### 분할 화면 '기본만' 정리 + 우하단 범례 겹침 해소·유리 스타일
- 분할 실행 화면에서 가로/세로·이격·도로접촉·배수 등 일반 치수선과 도로 편입 3㎡
  오버레이를 빼고(색 겹침·렌더 과부하), 분할 대상(초록)·제외(빨강) 조각 + 분할선·면적만
  남겼다. _execute_division 이 build_map_commands 결과에서 show_dimensions 를 걸러 그린다.
- 우하단 범례 2개(용도지역 걸침구분 + 규제 중첩)가 고정 offset(188px) 때문에 조각 수가
  늘면 겹쳤다. 둘을 .legend-stack(flex column-reverse, gap)로 묶어 높이와 무관하게 항상
  간격을 지키게 하고, 카드 배경을 반투명 유리(rgba 0.66 + backdrop blur)·둥근 모서리·그림자로
  다듬었다. 컨테이너는 pointer-events:none 으로 지도 클릭을 막지 않는다.

### 분할 후 건축물에 '도로 후퇴 편입분(약 3㎡)'을 실제 접한 변에 표시
- 미달도로(지적 추정폭<4m) 접한 변의 도로 후퇴 편입분을, 분할 실행('분할해서 지어줘'·
  '분할 후 건축물 보여줘') 시 지도에 도형으로 그린다. map_control.road_setback_pieces 가
  road_contact_geometry(접촉선) 중 그 도로 접촉길이에 가장 가까운 조각을 특정하고, 그
  선을 후퇴폭만큼 안쪽으로 부풀려 필지와 교차한 띠를 편입분으로 계산한다.
- 분할 제외(규제 분리, 빨강)와 구분되게 보라(#AD1457) '도로 편입(후퇴)' 조각 + '도로 편입
  약 N㎡ (측량 후 확정)' 라벨 + '도로 후퇴 N.Nm' 후퇴선으로 그린다. persist 지속 레이어라
  건물 모델을 세워도 남는다. 위치는 실제 접한 변(접촉선 기반)이라, 종전 외부 도구가 반대
  변에 잘못 찍던 문제를 바로잡는다(실제 위치·면적은 현황측량 후 확정).
  검증: 308-1 에서 서(왼쪽) 변에 2.8㎡ 조각·라벨·1.2m 후퇴선이 나오고 110 테스트 통과.

### 분할 오버레이 지속 레이어 — 건물 모델을 세워도 분할 대상·제외가 남는다
- '분할해서 지어줘' 뒤 모델 버튼을 누르면 분할 대상(초록)·제외(빨강) 면적과 분할선·면적
  라벨이 사라졌다. 원인: 모델 버튼이 그 용도의 이격을 백엔드에서 받아 show_dimensions
  (이격선)를 그리는데, showDimensions 가 앞선 치수선(=분할 면적 라벨·분할선)을
  clearDimensions 로 지웠다. clear_mass 도 zonePieces 를 지웠다.
- 분할 오버레이 전용 지속 레이어(divisionOverlayIds)를 추가. show_zone_pieces·
  show_dimensions 에 persist 플래그를 두어 분할 조각·분할선·면적 라벨은 이 레이어에
  담는다. clear_mass·clearDimensions·이격선 재표시로는 지워지지 않고, 전체 재구성
  (build_map_commands)이 내보내는 clear_division_overlay 로만 지운다 → 새 필지 진단·
  '분할 전 건축물 보기'에서만 정리된다. 건물 모델을 세워도 분할 경계가 계속 보인다.
  검증: 분할 실행 명령열이 clear_mass→clear_division_overlay→…→show_zone_pieces
  (persist)→show_dimensions(persist) 순으로 나오고, 110 테스트 통과.

### 분할 전/후 건축물 토글 — 역질문(버튼) + 자연어 실행
- 분할 실행이 진단을 분할 대지로 덮어써 원본(분할 전)을 다시 볼 수 없었다. _execute_division
  이 분할 전 원본을 diagnosis['_pre_division']에 보존하고 항상 원본에서 분할하도록 함.
- '분할 전 건축물 보여줘'(자연어) 또는 '분할 전 건축물 보기' 버튼(divide:before) → _show_
  predivision 이 원본(3,005㎡)으로 되돌려 다시 그리고 '분할 후 건축물 보기' 버튼 제시.
  '분할 후 건축물 보여줘'/버튼(divide:after) → 원본에서 재분할(2,918㎡). LLM 없이도 결정적
  라우팅(_is_division_request + '분할 전' 정규식). 프론트 onAction 에 divide:before/after 연결.
  검증: 분할(2,918)↔원본(3,005) 토글, 팝업·버튼 정상. 110 테스트 통과.

### 분할 실행 답변에 방법 설명 포함 + 이격 '미수집' 문구 정확화
- 분할 실행 시 짧은 확인 메시지만 나오고 방법 설명(규제 분리·도로 후퇴 목록)이 빠진다는
  피드백 → _execute_division 이 덮어쓰기 전 _division_scenario_answer(방법 목록+규모 추정+
  후속 인허가)를 캡처해 실행 답변 앞에 붙이고, 뒤에 '▶ 위 방법 중 …로 분할해 지도·팝업 재계산'
  확인을 잇는다. 개발부담금은 assessed_area_m2 로 정상 재계산됨(앞선 None 은 조회 키 오독).
- 이격 NOT_COLLECTED 문구(App.tsx 이격 라벨)도 site_constraints 와 동일하게 정확화 —
  '별표 미수집으로 확정 못함' → '단독주택 등은 별표2상 이격이 조례 위임(강제 없음)이라 통상
  0m, 아파트·대형시설만 조례 별표 확인'. 110 테스트 통과.

### 제미나이 503(과부하) ⚠ 하드에러 해소 — LLM 재시도 + 분할 실행 결정적 안전망
- 증상: '분할해서 지어줘'(버튼) 시 '⚠ Error code: 503 - This model is currently experiencing
  high demand'가 사용자에게 그대로 튀었다. 원인: gemini 무료 티어의 일시적 503(과부하)에
  LLM 클라이언트가 재시도를 안 했고(max_retries=0), _interpret_followup 이 503으로 실패→폴백→
  '분할해서 지어줘'가 분할 실행 아닌 재진단으로 샜으며 그 재진단의 extract LLM 호출이 또 503.
- 수정 ①: llm.py 두 클라이언트 max_retries 0→2 — SDK 백오프로 503·타임아웃 흡수(503은 응답이
  빨라 재시도 비용 작음). ②: _is_division_request 결정적 안전망 추가 — '분할'+실행어면 제미나이가
  503으로 다운돼도 분할 실행(_execute_division)으로 라우팅. 검증: 분할해서 지어줘→치수선 off·
  분할 대지 재렌더·면적 라벨·분할선·규모 재계산. 110 테스트 통과.

### 필지 분할 시나리오 1단계 — 성립 판정 + '분할해서 지어줘' 자연어 제어
- 배경: '분할 후 다시 지어줘'류를 하려면 먼저 '분할이 성립하는지'를 판정해야 하는데 없었다.
- tools/land_division.assess(신규): 기존 데이터로 분할 성립 여부·방법·유효 대지면적을 결정적
  추정(사전검토). 방법 — 규제 분리(용도지역 걸침 share/규제구역 부분 걸침), 도로 후퇴(미달도로
  4m<, cadastral_width_estimate 로 편입면적), 일반 분할(대지≥최소면적×2). 성립 조건 — 각 필지
  ≥ 법정 최소 대지면적, 맹지면 분할해도 접도 미충족(불가), 녹지·관리·농림은 개발행위허가 대상.
  prediagnosis 가 진단에 land_division 저장.
- 자연어 제어: _interpret_followup 에 assume_divided 추가 — 제미나이가 '분할해서 지어줘/분할하면
  되나/분할 후 다시 확인'을 해석 → orchestrator._division_scenario_answer 가 성립 판정+방법별
  유효면적+규모 추정(건폐율·용적률)+후속 인허가(개발행위허가→건축허가)를 답하고, 맹지·협소면
  왜 성립 안 하는지 설명. (멸실 가정 assume_demolished 와 같은 패턴.)
- 검증: 마룡리 308-1(용도지역 걸침→규제 분리), 마룡리 425-4·음성 읍내리 400(미달도로→도로 후퇴),
  창대리 647(일반 분할), 광주 퇴촌 광동리 400(맹지→불가), 홍천 희망리 100(협소→불가). 110 테스트 통과.
- 2단계(후속): 정확한 분할선 지오메트리로 축소 대지 3D 재배치.
- 2단계 실행(용도지역 걸침): '분할해서 지어줘'·버튼·긍정 화답 시 실제로 실행 —
  land_division.recompute_massing 로 건축 가능한(가장 큰) 용도지역 조각 지오메트리·면적에
  단일 용도지역 건폐율·용적률(걸침 가중 아님)로 매스를 재계산하고, 진단을 그 분할 대지로
  갱신 → 기존 치수선을 끄고(set_layers dimensions=False) 지도(분할선=대지 경계·건물)·팝업
  (대지면적·건폐율·용적률·규모)을 다시 그린다(orchestrator._execute_division). 검증: 308-1
  →자연녹지 2,918㎡·20%/100%·건축면적 584㎡로 재계산·팝업 갱신.
- 역질문/버튼: 분할 성립(FEASIBLE) 필지는 검토 의견 끝에 '필지 분할해서 지어드릴까요?' +
  '필지 분할해서 규모 보기' 버튼(action divide:apply, 프론트 onAction→'분할해서 지어줘')을
  붙이고 pending_offer='divide'로 긍정 화답도 실행에 연결(offer_show_models 패턴).
  분할 뷰는 치수선 없이 분할선·건물만 표시(show_building_dimensions=False).
- 남은 2단계: 도로 후퇴 스트립·일반 분할선 지오메트리(측량 확정 필요) 반영.
- 분할선 시각화: 팝업 숫자만 바뀌고 지도에 경계가 안 보인다는 피드백 → 분할 실행 시
  분할 대상(초록 #2E7D32)·분할 제외 부분(빨강 #C62828)을 show_zone_pieces 조각으로 얹어
  두 색이 맞닿는 선이 곧 분할 경계로 보이게 했다(원본 zone_shares 지오메트리 사용). 건물은
  분할 대상 위에 선다. 308-1→초록 자연녹지 2,918㎡(97.1%)·빨강 제1종주거 87㎡(2.9%).

### 규제 범례: 색을 '건축 가부 심각도'로 통일 + 색 의미 키 표시
- 증상: 우하단 '규제 중첩·건축 제약' 범례의 색이 규제 '종류'별 카테고리색이라, 사용자가
  색만 보고 '불가인지 조건부인지' 구분 못 하고 '무슨 의미냐'고 물었다. 용도지역처럼 지도
  영역으로도 안 그려져 더 헷갈렸다(이 규제 지구들은 지오메트리가 없어 목록으로만 안내).
- 수정: _restriction_color 를 심각도 3단계로 통일 — 빨강=원칙 불가/강한 제한(개발제한·맹지·
  보전산지·상수원·농업진흥·생태1등급·재해), 주황=조건부(협의·심의·허가로 가능: 수질보전·
  배출시설제한·문화재·군사·경관·가축·특별대책·수변·생태2등급 등), 회색=참고(생태3등급).
  범례 명령에 color_key(빨강=원칙 불가/주황=조건부)와 '지도 영역 아닌 지정 규제' 문구 추가,
  프론트가 색 키 스와치를 렌더(map_control.py·App.tsx·mapBridge.ts). 검증: 배출시설제한·
  수질보전특별대책→둘 다 주황(조건부), 색 키 표시. 110 테스트 통과.


### '우수 방류·도로 접촉 선만 보여줘'가 '치수선 끄기'로 오작동 — 레이어 제어 정규식 버그
- 증상: "우수 방류 치수선과 도로 접촉 선만 보여줘"(특정 선 SHOW)가 "치수선 끄기를 적용
  했습니다"로 나왔다(제미나이 문제 아님 — 결정적 정규식이 가로챔).
- 원인: 레이어 켜기/끄기 결정 블록이 진단·선 오버레이보다 먼저 돌며 매칭 시 즉시 return.
  ① '치수선' 문자에 dimensions 레이어가 걸리고, ② 끄기 키워드에 있던 바로 '접'이
  '도로 접촉'의 '접'에 오매칭돼 SHOW 요청이 OFF로 판정됐다.
- 수정(orchestrator.py): ① 특정 오버레이 선 요청(_requested_map_lines 비지 않음)이면
  dimensions 레이어 토글을 건너뛰어 아래 '선만' 오버레이(build_lines_only_commands)·제미나이
  map_lines 로 그 선만 그리게 흘려보낸다. ② 끄기 키워드의 '접'을 '접기|접어|접는'으로 좁혀
  '접촉'·'접도' 오매칭 제거. 검증: "우수 방류 치수선과 도로 접촉 선만 보여줘"→배수+도로 선
  표시(끄기 아님), "치수선 꺼줘/해제"는 여전히 dimensions 끄기. 110 테스트 통과.
- 후속 버그: '우수방류 치수선에 닿는 도로접촉 보여줘'가 복원(다시 켜)·학습제어(control_glossary)
  핸들러에도 걸려 '치수선 다시 켰습니다'로 나가고, 정정('아니 도로접촉 선 말이야')은 장황한
  텍스트만 냈다. 특정 오버레이 선 요청(_requested_map_lines 비지 않음)이면 복원 핸들러와
  control_glossary 루프를 건너뛰어 제미나이 map_lines 가 그 선만 그리게 했다(orchestrator.py).
  검증: ①배수 경로 ②배수+접한 도로 ③정정→도로접촉 선 모두 정확히 표시. 110 테스트 통과.
- 후속 버그2: '맞닿는 치수선 보여줘'(우수/도로 키워드 없어 _requested_map_lines 빈 배열)가
  결정적 dimensions 레이어 토글에 걸려 모호한 '치수선 켜기를 적용했습니다'로 나갔다. 문맥상
  '특정 선'을 뜻하는 SHOW 요청(닿|맞닿|접하|연결|방향|배수|우수|오수|도로|방류|건축선|이격
  + 보여/켜/표시)이면 dimensions 토글을 건너뛰어 제미나이 map_lines 로 그 선만 그리게 했다.
  순수 '치수선 꺼/켜/숨겨'는 결정적 토글 유지(테스트 보장). 110 테스트 통과.
- 후속 버그3: 결정적 정규식 제거 후엔 제미나이가 '우수방류 치수선 보여줘'류 특정 선 SHOW를
  map_lines 가 아니라 control{show,dimensions} 전역 토글로 잘못 담아 '치수선 표시를 다시 켰습니다'가
  나왔다(로그 followup_llm 확인). ① 질의가 특정 오버레이 선(우수·배수·도로접촉·건축선)을 명시하고
  끄기가 아니면 control 을 비우고 그 선을 map_lines 로 강제(결정적 보정). ② control·map_lines 필드
  설명을 보강 — 특정/문맥 선('맞닿는 선','그 선')은 control 아닌 map_lines, control{dimensions}는
  전역 치수선 토글에만. 검증: '우수방류 치수선만/…맞닿는 곳/…맞닿는 치수선 켜'→선 표시, '치수선
  꺼/켜'→전역 토글. (키워드 전혀 없는 '맞닿는 치수선 켜' 순수 문맥형은 아직 전역 토글로 감—후속.)
  110 테스트 통과.

### 표시 제어 명령('선 표시 꺼')이 설명으로 새던 것 — 프롬프트 강화
- 증상: '선 표시 꺼'가 명령 실행 없이 '~ 표시 기능은 …입니다' 설명 텍스트로만 나갔다
  (flash-lite가 control 명령을 followup_explanation 으로 오분류). '선 켜/선 꺼줘/눈금'은 정상.
- 참고: 의미 해석 자체는 됨 — 눈금·측정선·'배수 빠지는 선'·'도로 닿는 선'·'그 선'·'아까 그거'는
  제미나이가 올바르게 map_lines/control 로 처리(하드코딩 아님). 일부 명령만 flash-lite가 흔들렸다.
- 수정: _interpret_followup 프롬프트에 '선/치수선/눈금/표시/레이어를 켜/꺼/보여/숨겨/지워/표시해
  라고 하면 설명이 아니라 실행 명령 — 반드시 control 로 실행하고 설명만 하지 마라'를 명시.
  검증: '선 표시 꺼/켜','표시 지워','눈금 없애' 모두 set_layers 실행. 110 테스트 통과.

### '기존 건축물 멸실 가정으로 가능한 건축물 보여줘' 자연어 제어(제미나이 해석)
- 요구: 기존 건축물로 '실질 배치 불가'인 필지에서, 사용자가 "그 건물이 멸실·해체됐다고
  가정하고 지을 수 있는 건축물을 조건부 가능으로 보여줘"라고 하면 배치 불가를 해제하고
  매스·모델을 표시.
- 구현: _interpret_followup 스키마에 `assume_demolished` 필드 추가 — 제미나이가 멸실 가정
  요청을 해석해 intent=possible_models + assume_demolished=true 로 분류(정규식 아님). possible_models
  핸들러에서 배치 불가가 '기존 건물' 때문이고 협소(min_lot_area) 아님일 때만 placement_restricted를
  해제하고 map_presentation을 '조건부 가능(멸실 가정)'(show_building_mass=true)로 바꾼 뒤 매스를
  다시 그리고(_render_event) 가능 모델 버튼을 방출. 답변에 '멸실 가정·해체허가·소유권 정리 선행'
  전제를 명시. 협소 대지는 멸실로도 안 풀려 해제하지 않는다.
- 검증: 세종대로 110(기존건물·비협소) — 해제 전 모델 [] → 해제 후 3종+매스, 제미나이 intent=
  possible_models·assume_demolished=true. 110 테스트 통과.

### 사유지 침범(배수 우회) 판정의 소유구분 API 복구 — data.go.kr NSDI → VWorld NED
- 증상: 배수로가 지나는 필지의 소유구분(사유=승낙 필요·차단 / 국공유=통과 가능·우회)을
  land_ownership.lookup_ownership 이 조회하는데, 실제로는 늘 미상→지목 추정으로 폴백됐다.
- 원인: 엔드포인트가 data.go.kr NSDI(apis.data.go.kr/1611000 LandOwnershipService)였는데 이
  키에 미등록이라 전 필지 HTTP 400 `NO_OPENAPI_SERVICE_ERROR`(서비스 없거나 폐기됨)를 냈다.
- 수정: 토지이용계획과 같은 플랫폼인 **VWorld 국토정보(NED) getPossessionAttr** 로 교체
  (land_ownership.py). 기존 VWORLD_KEY·domain 사용(별도 활용신청 불필요), 소유구분명
  `posesnSeCodeNm`(개인·법인·종중=사유 / 국유지·시 도유지·군유지=국공유)을 정확히 파싱
  (변동원인 ownshipChgCauseCodeNm 오인 방지). 검증: 세종대로 110→국공유(시 도유지),
  마룡리 425-4·음성 읍내리 400→사유(개인). 110 테스트 통과.
- 효과: 배수 침범 판정이 실제 소유구분으로 확정 → 사유지는 승낙 필요(차단), 국공유지(도로·
  구거)는 통과 가능(우회)로 갈리고, '사유 확인 시에만' 내던 배수 유의사항이 실데이터로 동작.

### 검토 의견: 필지 분할은 끝이 아니라 시작(분할 후 개발행위·건축허가) 안내
- 부분 걸침 시 필지 분할 방법만 말하고, 분할한 대상 대지에 개발행위허가·건축허가(필요 시
  농지·산지 전용) 등 후속 인허가가 이어져야 실제로 지을 수 있다는 점을 빠뜨렸다. 검토 의견
  프롬프트(_all_uses_verdict_judgment_with_llm)에 그 후속 절차를 함께 짚도록 보강(orchestrator.py).

### 검토 의견 대기 구간에 '작성 중' 진행 표시
- 카드·지도가 먼저 뜬 뒤 검토 의견(flash LLM)이 채워지기까지 6~8초 빈 구간이 멈춘 것처럼
  보였다. render_pending_judgment 직전 tool_start(judgment) 이벤트를 흘려 '▸ 검토 의견 작성 중'을
  진단 진행 표시와 같은 방식으로 보여준다(main.py + App.tsx TOOL_LABEL.judgment). 최초 진단
  카드에서만 뜬다(pending_judgment 발생 지점). 팔로업은 flash-lite라 대기 구간이 없어 미표시.

### 같은 필지 '○○ 지을 수 있어?' 팔로업에도 가능 모델 버튼 방출
- 증상: 필지를 진단한 뒤 같은 필지에서 '단독주택 지을 수 있어?'(specific_use_feasibility)로
  물으면 가부는 답하는데 3D 모델 버튼이 안 떴다(사용자는 따로 '가능 모델 보기'를 눌러야 했다).
- 원인: 모델 버튼(_model_options_for_diagnosis) 방출이 최초 카드(emit_card=True) 블록에만
  있어, 같은 필지 팔로업(emit_card=False)은 재진단·판정만 하고 버튼을 건너뛰었다.
- 수정: coordinate_diagnosis 의 same_parcel/continuation 팔로업 답변 뒤에, 재진단된 용도가
  가능하고 준비된 모델이 있으면 최초 카드와 동일하게 '가능 모델' 버튼을 함께 낸다
  (orchestrator.py, _model_options_for_diagnosis 사용 — 신규 규칙 없음). 버튼 생성은
  massing 있음·배치 제한 아님·허용/조건부일 때만이라 자체 게이팅. 검증: 계양구 작전동 100
  단독주택→['단독주택형','공동주택형','상가 모델']. 110 테스트 통과.

### 진단 콜드 타임아웃 해소(도시계획도로 상한) + 유의사항 간결화
- 타임아웃: 맹지(도로 없음) 필지에서만 부르는 vworld.get_planned_roads(도시계획도로 WFS)가
  VWorld 지연 시 httpx 15초까지 물려 진단 전체가 20초(LLM 클라이언트 한도)를 넘겨
  '↻ 응답 시간 초과…Request timed out'을 냈다(경산 상방동 65-5 22초→타임아웃). 호출부에
  6초 상한(wait_for)을 걸고 초과 시 기존 토지이음 지정목록 폴백으로 떨어지게 해 콜드
  진단을 22초→5.5초로 낮췄다(prediagnosis.py). ※진단은 flash 안 씀 — 검토의견 모델과 무관.
- 유의사항 간결화(길다는 피드백): 매번 붙는 일반 면책 2줄을 1줄로 압축. 배수 유의사항은
  ① 농막·움막·태양광 등 특수·가설 시설(no_building_model)에는 오수·배수 요건이 사실상 없어
  붙이지 않고, ② 배수로가 사유지를 지난다는 상세 경고는 소유구분이 '사유'로 확인된 때만
  낸다(지목 기준 추정만으로는 지도에 확정 경로도 없어 특정 배수처·필지를 단정하지 않음).
  검증: 단독주택→면책+배수 일반 note, 농막→면책 1줄만. 110 테스트 통과.

### 검토 의견만 상위 모델(flash) 선택 적용 — 원론적 답변 해소(무료 티어)
- 배경: flash-lite 는 라우팅·추출엔 충분하나 여러 규제를 읽어 인과·해결방법으로 엮는
  검토 의견에서 지시를 흘리고 일반론으로 뭉갰다. 전량 상위 모델은 값싼 고빈도 호출까지
  느려지므로, '판독·추론이 무거운' 검토 의견 호출만 상위 모델을 쓰도록 분리.
- 구현: complete() 에 per-call model·reasoning_effort override 추가(llm.py, anthropic·openai
  양쪽). config.LLM_MODEL_HEAVY 신설 — gemini 무료 티어에선 flash-lite→gemini-flash-latest
  (역시 무료)로, 다른 provider 는 LLM_MODEL 그대로. env(LLM_MODEL_HEAVY)로 재정의 가능.
  검토 의견 두 호출(_verdict_judgment·_all_uses_..._with_llm)만 model=HEAVY 적용.
- flash 튜닝: gemini 2.5 flash 는 thinking 이 기본 ON 이라 max_tokens 안에서 thinking 이
  토큰을 먹어 답변이 잘렸다. reasoning_effort='low'(none 은 gemini 400)로 thinking 최소화,
  max_tokens 2400/3000 로 여유 확보, 검토 의견 wait_for 8→14s 로 상향(카드 뒤 지연 방출이라
  수용 가능). 라우팅·추출 등 나머지 호출은 flash-lite·기존 예산 그대로.
- 검증: 창대리 647 농막 2.3s — 가설건축물 축조신고·유역환경청 오수처리계획·20㎡ 농업목적을
  규제별 구체 방법으로. 음성 읍내리 단독주택 4.5s — 농지법 제34조·국토계획법 제56조·건축법
  제11조 조문과 이격 수치·경관심의·부서별 절차. 광주 퇴촌(개발제한+상수원+생태1등급+맹지)은
  규제별 해소 순서 제시. 부수효과: 농막을 농지전용이 아닌 가설건축물 축조신고로 정확히 판독.
  110 테스트 통과.

### 특수시설 건폐율·용적률 표시 + 수질보전 규제 인식 + 검토의견 방법 구체화(전국)
- 건폐율·용적률: 특수·가설 시설(농막·움막·태양광 등 no_building_model)도 용도지역
  건폐율·용적률 상한은 필지 기준값이라 팝업에 표시하도록 했다. 프론트에서 이 값이 매스
  (massing) 블록 안에 있어 매스 없는 특수시설엔 통째로 숨던 것을, 매스 밖으로 빼 항상
  표시(App.tsx). no_building_model 안내 문구도 '건폐율·용적률 산정 대상 아님' → '3D 모델·
  규모만 미표시, 용도지역 건폐율·용적률은 함께 표시'로 정정(prediagnosis.py). 검증:
  농막→자연녹지 20%/100%, 태양광→제2종일반주거 60%/250% 표시.
- 수질보전 규제 인식(전국): 수질보전특별대책지역·배출시설설치제한지역·특별대책지역·수변구역을
  _CONSTRAINT_KEYWORDS 에 추가 — 팔당 등 특별대책지역 필지가 규제·범례에서 빠지던 것 해소.
  검증: 양평 창대리 647 → 범례에 배출시설설치제한지역·수질보전특별대책지역 표시.
- 검토의견 방법 구체화: 규제별 note의 구체 조치(유역환경청 협의·오수처리계획, 현상변경 허가
  등)를 하나로 뭉뚱그리지 말고 규제별로 살려 쓰고, '각 부서와 각각의 절차를 거쳐야 합니다'류
  원론적 마무리로 때우지 말라는 지시를 프롬프트에 추가(orchestrator.py). 110 테스트 통과.
  한계: flash-lite가 여전히 일부 원론 마무리를 흘림. 후속: 농막은 농지전용이 아니라
  가설건축물 축조신고 대상(부담금 미부과)이라는 특례가 검토의견·부담금에 아직 미반영.

### 규제 지구 해석 = 역사문화환경보존지역 인식 + 시설 용도별 제약 + 검토의견 방법 중심(전국)
- 증상: 인천 강화 갑곳리 836-4는 역사문화환경보존지역에 100% 포함인데 '건축 가능'으로
  나왔다. 원인: zoning._CONSTRAINT_KEYWORDS 에 '문화재'만 있고 '역사문화환경'이 없어
  토지이용계획 API(VWorld NED, 이미 UOC800로 반환)가 준 지정을 제약으로 못 알아봤다.
  또 가축사육제한구역이 농막·단독주택에도 무조건 제약으로 걸려 판정을 조건부로 낮췄다.
- 수정 ① 인식(전국): '역사문화환경' 키워드 추가 — 지정문화유산 보존지역이면 현상변경
  허가·국가유산청 협의를 조건부 사유로 잡는다(zoning.py).
- 수정 ② 시설 용도별 제약(전국): _FACILITY_SCOPED 신설 — 가축사육제한구역은 축사·축산
  시설에만, 교육환경(학교정화)구역 금지시설 제한은 숙박·유흥 등에만 적용. _match_constraints
  가 검토 용도(requested_facility)를 받아 해당 시설이 아니면 그 지구를 제약에서 뺀다.
  lookup_zoning_rules·prediagnosis 로 facility 전달. 검증: 농막→가축 제외, 축사→가축 포함.
- 수정 ③ 검토 의견 방법 중심(하드코딩 스티칭 제거): 예전엔 LLM 문단 앞뒤로 '물어보신 ○○
  은(는)…', 맹지 문장을 문자열로 붙여 조사·맹지 중복·'답으로' 같은 어색함이 났다. 그
  스티칭을 걷어내고(orchestrator.py), 검토 용도·맹지·regulation.constraints 를 프롬프트가
  데이터로 읽어 제미나이가 ①검토 용도 중심으로 리드(다른 용도 나열 안 함) ②행위·절차·방법을
  육하원칙으로 ③규제 해소 방법(부분 걸침이면 필지분할, 전부면 협의·심의·허가, 맹지면 진입로
  확보)을 구체적으로 제시하게 했다. placement_restricted(실질 배치 불가) 안전장치는 유지.
  검증: 농막→'물어보신 농막은…조건부', 가축 미언급, 국가유산 협의·맹지 1회. 110 테스트 통과.

### 건축물대장 직접조회에 도로명 조인 적용(전국) + 규제 용도지구 범례
- 도로명 조인: 건축물대장은 토지 필지 PNU가 아니라 건물 대표지번(도로명)에 등록된 경우가
  많아, PNU만으로 조회하면 대장이 있어도 '없음'으로 나온다. 사전진단·추천 필터는 이미
  주소를 넘겨 조인(_juso_loc)했는데, orchestrator의 '건축물대장 직접 조회' 흐름만 PNU만
  써서 누락됐다(orchestrator.py:2291). address_query를 함께 넘겨 전국에서 조인되게 했다.
  전국 검수: 마룡리(양평)·읍내리(음성)·희망리(홍천)·동성로2가(대구) 등 서로 다른 시·도에서
  PNU 단독 CLEAR였던 필지가 도로명 조인으로 FOUND 복구됨을 확인.
- 규제 용도지구 범례: 토지이용계획이 잡은 용도지구·구역 규제(개발제한·군사·경관·고도지구·
  지구단위계획·문화재 등, zoning._match_constraints 가 만든 regulation.constraints)를
  우하단 범례에 라벨 조각으로 얹었다(map_control.py). 면적 조각·지오메트리가 없어 share
  없이 이름만, 성격별 색(개발제한 빨강·군사 남색·문화재 갈색·심의 파랑)으로 구분하고
  심의·협의 사유는 note→프론트 툴팁으로 보여준다(App.tsx·mapBridge.ts). 여기서 새 규칙을
  만들지 않고 진단이 이미 만든 constraints 를 그대로 옮긴다. 110 테스트 통과.

### 건축물대장 조회 흔들림 = 건축HUB 간헐 503 → 재시도로 안정화
- 증상: 같은 필지(마룡리 425-4)를 반복 조회하면 어떤 때는 기존 건축물이 잡히고 어떤 때는
  '건물 없음'으로 나와, 실질 배치 불가 판정과 추천 필터(기존건물 제외)가 흔들렸다.
  원인 규명: `_query_title` 5회 호출 시 3회 503(Service Unavailable)·2회 정상 —
  juso 폴백이나 도로명·PNU 조인 문제가 아니라 건축HUB API 자체가 간헐적으로 503을 낸다.
- 수정: `building_register._query_title`에 재시도 루프 추가(building_register.py).
  5xx 응답과 TimeoutException/ConnectError/ReadError를 최대 5회, 짧은 백오프(0.3s / 타임아웃은
  0.4·n)로 재시도한 뒤에야 실패로 올린다. 4xx(인증·파라미터)는 재시도 무의미해 즉시 전파.
  status_code는 getattr(…,200)로 읽어 목 응답과도 호환. 인증키 누수 방지 로직(상태코드만 보존) 유지.
  검증: 마룡리 425-4 조회 10/10 성공(재시도 전 2/5), 110 테스트 통과.

## 2026-08-01

### 검토 용도 = 질의 해석한 실제 시설 + 움막/농막 농지법 판정
- 증상: '움막 지을 수 있어?'가 검토 용도 '시설물'로 뭉개져 '조건부 가능'(틀림)으로 판정.
  움막은 농지법상 농지에 불가한데도. 태양광 등 특정 시설도 시설물로 일반화됐다.
- 수정 ① 라벨: 제미나이가 질의를 해석해 '전체 건축물(시설물)'인지 '특정 시설(움막·농막·
  태양광 설치 등)'인지 판단하고, 특정 시설이면 그 표현을 requested_facility 로 담아 카드
  '검토 용도'에 그대로 표기(extract_request 필드+프롬프트, _deterministic_request 는 건축
  동사 앞 시설 명사를 라벨로 포착, 표준 11용도·일반어 제외). building_use 판정 로직 불변.
- 수정 ② 판정: 농지(지목 farmland) 위 움막 → not_allowed(농지법상 설치 불가), 농막 →
  conditional(신고 후 20㎡). regulation 조립 직후 결정식으로만 적용(하드코딩 수치 없음).
  검증: 움막→검토용도 움막·불가(농지법 제32조), 농막→농막·조건부, 건물→시설물·전체검토.
  110 테스트 통과. 참고: 농막 조건부 시 3D 매스는 아직 용도지역 밀도로 계산(20㎡ 미반영).


### 지역 필지추천(recommend_areas)이 자연어 후속에서 도달 못하던 누수
- 증상: 필지 진단 뒤 "음성군에 농막 지을 필지 리스트 뽑아봐"라고 물으면 "그런 기능
  없다"고 회피. 실제로는 recommend_areas(시·군 비도시 용도지역 공간스캔→지번·지목
  후보 리스트)가 이미 있는데, 메인 도구루프에서만 도달 가능하고 후속(_interpret_followup)
  엔 그 intent가 없어 새어나갔다.
- 수정: recommend_areas emit을 단일원본 메서드 `_recommend_areas_events(region,use)`로
  추출(도구루프·후속 공용). _interpret_followup 에 `recommend_region`/`recommend_use`
  필드 + 프롬프트 추가 → 제미나이가 지역+조건 탐색형 후속을 그 능력으로 라우팅하고
  '기능 없음'으로 회피하지 않는다. 환각 방지: 지역 어간이 질문 원문에 있어야 실행.
  검증: 후속 "음성군에 농막 필지 리스트" → 후보 6곳(무극리 등) 4.0s. 참고: 후보 필지는
  공간스캔이 만들고, RAG(조례 벡터)는 근거 조문 검색용이지 필지 DB가 아니다(역할 분리).


### 건축물대장·토지이용·토지소유 조회 복구 + 콜드 17.7s→4s (http→https)
- 증상: 건축물대장 조회가 '조회 실패(UNAVAILABLE)'로 빠지고, 종합진단 콜드가 ~18초.
- 원인: 공공데이터포털 `apis.data.go.kr` 이 **http 로는 TCP 연결만 받고 HTTP 응답을
  주지 않아**(2026-08 확인) 15초 타임아웃으로 조회가 통째로 실패. https 는 0.1초 정상 응답.
  기존건물(멸실 후 재건축 판단)·도로명주소(juso) 폴백이 이 때문에 누수돼 있었다.
- 수정: apis.data.go.kr 호출 3곳을 https 로 — building_register.BASE_URL(건축물대장),
  config LANDUSE(토지이용계획 getLandUseAttr), land_ownership(토지소유 getLandOwnership).
  검증: 한내로 62→juso 폴백→FOUND 10동 공동주택, 세종대로 110→FOUND 2동 업무시설.
  콜드 total_ms 17.7s→~4s(15초 타임아웃 제거). 회귀 방지 테스트 추가(소스에 http://
  apis.data.go.kr 금지). 나머지 런타임 호스트(VWorld·law.go.kr·juso·Gemini) 응답 정상 확인.


### 이동('찾을 수 있어') 의도 인식 + 학습
- 버그: '고읍동 128-2 찾을 수 있어'가 이동이 아니라 종합진단으로 갔다. 결정적 이동
  핸들러(move_to_parcel)의 트리거가 '이동|가줘|찾아줘|보여줘'뿐이라 '찾을 수 있어'를
  못 잡았다. 교육 문장('…이동하라는 뜻이야')도 '뜻'이 개념 fast-path에 걸려 학습 훅을
  우회했다.
- 수정: _requests_move_phrase(표준 찾기 표현 + 학습된 nav_glossary)를 이동 핸들러
  트리거로 사용. '찾을수있/어디야/위치보여/데려가' 등 인식하되 건축·모델 요청은 제외.
  _interpret_followup 에 learn_nav_term 추가 → '띄워봐라고 하면 이동하라는 뜻이야'처럼
  가르치면 nav_glossary에 저장(세션 영속), 다음부터 그 표현+주소면 이동. 교육 문장은
  _is_teaching_statement 로 개념 fast-path에서 제외해 제미나이 학습 훅에 도달. 회귀
  테스트 2건 추가(109).

### 자연어 기능제어 + 패턴 학습 (제어 표현 이해·기억)
- 요구: '수치선 꺼'(치수선 동의어)처럼 규칙에 없는 표현도 이해하고, '다시 켜'는 방금
  끈 대상을 되살리고, 안 되던 표현을 사용자가 말하면 학습해 다음부터 바로 되게.
- 1단계(맥락): 모든 숨김/표시 실행 시 last_control(대상·켜짐) 기록 →'다시 켜/복원'은
  마지막에 '끈' 대상 복원(치수선 끄면 치수선, 모델 끄면 모델). _control_command/
  _control_result 한 곳에서 명령·문구 생성(조사 안전).
- 2단계(LLM 보조): 규칙이 못 잡은 제어 표현은 _interpret_followup 도구의 control
  {action,target,learn_term}으로 제미나이가 의미 해석(동의어·구어·오타). 추가 LLM 호출
  없음(기존 후속 해석에 얹음).
- 3단계(학습): learn_term 을 control_glossary(사용자말→대상)에 저장, 세션 스냅샷에
  포함돼 재시작·재접속 영속. 학습된 표현은 빠른 경로에서 LLM 없이 즉시 처리.
- 검증: '수치선 꺼' 첫 회 LLM 해석+학습, 2회차 LLM 0회 빠른 처리, '다시 켜' 치수선
  복원. 회귀 테스트 추가(107). CONVERSATION-STATE '자연어 제어·학습' 절 추가.

### offer 되물음이 '뭐야' 질문에서 우회되던 버그 (개념 fast-path 제외)
- 후속 질문은 run() 1330~1346 에서 user_query 에 좌표가 주입된다(`경도 … 위도 …`).
  그래서 '가능한 건축물이 뭐야?'도 coordinate_match 가 참이 되고 '뭐야'가 _concept_q 에
  걸려 개념 fast-path(2434)로 빠져 결정적 용도목록만 답 → 되물어 확인(offer) 흐름이
  아예 안 탔다(측정: 11ms, followup_llm 로그 없음).
- 수정: 개념 fast-path 조건에 `and not _asks_possible_use_list(original_query)` 추가(좌표
  주입 전 원문 기준). 가능-용도 목록 질문은 _interpret_followup(제미나이)로 흘려보내
  offer 되물음이 뜨게. '용적률/이격거리/후퇴가 뭐야'류 정의 질문은 _asks_possible_use_list
  가 False 라 그대로 개념 답변. 106 테스트 통과.

### 가능 모델 '되물어 확인(offer)' 자연어 흐름
- 요구: '가능한 건축물이 뭐야?'처럼 궁금해서 묻기만 하면 제미나이가 '가능한 모델을
  지도에 보여드릴까요?'라고 되묻고, 사용자가 화답하면 그때 모델을 띄우는 대화형 흐름.
- 구현: 후속 해석 도구에 `offer_show_models`(bool) 필드 추가. 탐색적 질문이면 제미나이가
  offer_show_models=true + answer 로 되묻고 intent=followup_explanation(모델 미표시).
  이 상태를 PNU별 `conversation_context.pending_offer="show_models"`로 저장 →
  compact(context)로 다음 턴 제미나이에 전달. 사용자가 긍정하면 제미나이가 맥락 읽어
  intent=possible_models 로 분류해 모델 표시. 표시 시작·화제 전환 시 pending_offer 해제.
- 판단·긍정 인식은 모두 제미나이(정규식 아님). 모델 목록 단일 원본
  `_model_options_for_diagnosis` 불변. '보여줘/표시해/모델 켜'는 되묻지 않고 즉시 표시.
  CONVERSATION-STATE.md '가능 모델 규칙'에 흐름 명시. 106 테스트 통과.

### '가능 모델 보여줘'가 이전 매스 복원으로 새던 문제 (라우팅 가드)
- `_is_building_restore_request` 가 '모델…보여'를 잡아 '**가능** 모델 보여줘'까지 복원
  요청(show_building_shape)으로 오인 → 직전 단일용도가 불허라 매스가 없으면 단독주택
  등 아무 모델도 안 떴다(possible_models 로 못 감).
- 수정: 1419행 restore 게이트에 `and not _asks_possible_use_list(original_query)` 가드.
  '가능/다른/허용 모델' 등 목록 요청은 restore 를 건너뛰고 LLM 후속해석→possible_models
  로 흘려보내 목록·매스를 새로 만든다. 순수 복원('모델 켜/다시 켜/이전 모델 보여줘/
  원래대로')만 결정적으로 처리. 새 하드코딩 없이 **기존 의미 판별기 재사용**(CLAUDE.md
  준수 — 프런트 정규식 추가 없음). 106 테스트 통과.

### possible_models: 불허 요청용도라도 지을 수 있는 용도로 매스 재생성
- 창고·판매시설 등 요청 용도가 그 지역서 불허면 verdict=not_allowed → 매스 미계산.
  그 뒤 '다른 건물?'(possible_models)로 배지·모델옵션은 떠도 3D 매스가 없어 '모델 켜'/
  모델클릭이 '건축 불가'·'유효 매스 없음'으로 실패하던 문제.
- 수정: possible_models 진입 시 verdict 가 not_allowed 이고 zone_use_overview 에 지을 수
  있는 용도(allowed/conditional)가 있으면, 그 첫 용도로 _diagnose_and_emit(emit_card=False)
  재진단해 매스를 만들고 지도 갱신(매스는 용도 무관 = 건폐율·용적률 envelope). 원본 불허
  판정은 pnu 별 보존. 초기 '창고?' 진단은 건축 불가 그대로 — '다른 건물?' 때만 매스가 나온다.

### '모델 켜/다시 켜' 인식 — 건물표시 복원 정규식 어간 확장
- _is_building_restore_request 가 '켜줘/보여줘/표시해'만 잡아 '모델 켜'가 LLM 으로 흘러가
  '모델링 끄는 기능 없음'으로 오해되던 문제. '켜/보여/표시' 어간까지 잡게 확장('모델 꺼/
  끄기'는 앞선 hide 정규식이 먼저 잡아 충돌 없음). 프런트 restoreBuildingShape 가 직전 모델
  종류(lastHousingModelType)를 복원하므로, 켜/꺼 토글 시 이전 모델도 그대로 복원된다.
  참고: 모델 켜/꺼는 의도 라우팅이 아니라 단순 UI 토글이라 결정적 정규식이 적합(테스트 유지).

### 후속 오버레이에 치수(가로·세로·높이)·면적(대지·건축) 추가 + footprint 정밀화
- '가로 세로 높이 대지면적 건축면적 보여줘' 다중 요청이 지도에 안 뜨던 문제. overlay_command/
  _OVERLAY_LABEL_KINDS 에 dimensions·area 종류 추가, overlay_command 가 세그먼트+labels(면적)
  둘 다 필터·반환. show_map_lines·_interpret_followup map_lines enum 에도 추가.
- '건축면적...보여'를 잡던 footprint 정규식이 다중 요청까지 가로채던 것을, '~만'(오직 그것만)
  일 때만 매치하게 정밀화 — '건축면적만 보여줘'는 footprint 토글(결정적, 테스트 유지),
  '건축면적 다 보여줘'는 오버레이. 새 가드 추가 아님(기존 정규식 정밀화).

### 3D 건물 높이 치수 — 가로·세로 모서리의 수직 3축(동일 노랑)
- 가로·세로 치수는 있는데 높이가 없던 것. 가로·세로 치수선이 만나는 남서 모서리에서 매스와
  같은 지면 기준(terrainHeight+0.5)으로 height_m 만큼 수직 높이선+라벨을 세워 3축 완성.
  map_control 이 height_m 세그먼트를, mapBridge 가 그 모서리에 수직 폴리라인으로 렌더.
  색은 가로·세로와 같은 노랑(3축 동일색).

### 도로: '지적 폭'(오표기) 제거 → 접도(2m) 여부 + 건물 높이 치수
- 굽은/가지친 도로 필지는 최소사각형이 커 '지적 폭'이 무의미(852m 통과도로를 폭처럼).
  → 화면에서 도로 필지 규모/외곽선 전부 제거하고, 정작 중요한 접도만 남김.
- `prediagnosis`: 카드가 접촉 길이 vs 건축법 접도 최소 2m 를 결정적으로 표기
  (2m 미만이면 '접도 최소 2m 미달 가능 — 현황측량 확정'). `_estimated_width_m` 는
  내부 후퇴폭 참고용으로만 유지, road_parcel_geometry·cadastral_length/area 제거.
- `map_control`: 긴 도로 외곽선 제거(접촉선만). 3D 치수에 건물 '높이 약 Xm·N층'
  라벨 추가(매스 옆면 중간 높이, label height 방식 — 프론트 무변경).
- 측정 해제 토스트('측정 표시를 지웠습니다') 미표시. 규제 범례 문구 '규제'→'환경·재해 중첩'.

### 검토의견 근거 보충: 영어 status 누수 제거 + 이격 자연화
- `_query_evidence` 가 road.get('status')(CADASTRAL_CONTACT 등 영어 코드)를 사용자
  문구에 그대로 붙이던 것 → 한국어 서술(summary/message/label)만. 이격 모두 0이면
  '이격 0.0m·0.0m·0.0m' 나열 대신 '모두 0m로 별도 후퇴선이 적용되지 않습니다'로.

### 예외 처리 전수 감사 — 조용히 삼키던 broad except 27곳에 로깅
- get_planned_roads 의 shape 스코프 NameError 가 broad except 로 조용히 삼켜진 사례 계기.
  전 코드 broad except 전수조사: 로그 없이 폴백하던 27곳에 logger.debug/warning(exc_info)
  추가(폴백 동작 유지, 코드 버그는 로그로 드러나게). 6개 파일에 모듈 logger 신설.
  함수-내부 import 전수 대조 → 스코프 버그는 vworld 한 곳뿐(수정됨), 나머지 안전.

### 환각 이동 방지 — 두 이동 경로 모두 결정적 가드
- 증상: 개념 후속질문('접촉 길이와 중심선 후퇴는 왜 중요한데')에서 제미나이가
  target_address 를 환각(예산 두리 111)으로 지어내 엉뚱한 필지로 튕김.
- `_target_in_query()` 가드: LLM이 낸 이동 주소의 지명/랜드마크 토큰이 질문 원문에
  실제로 있어야 이동(번지 숫자 강제 X — 어르신은 '○○초교 근처'로 말함). continuation
  target_address 와 move_to_place 양쪽에 적용. 이동 의도는 LLM, 가드는 환각 검증만.

### 랜드마크 이동 견고화 — move_to_place 에 geocode 폴백
- VWorld POI(search_places)가 '예산 삽교초등학교'(지역+시설명)에서 0건 → 실패하던 것을,
  geocode 폴백으로 보완(geocode 는 지역+시설명 견고 처리, 동명 학교를 지역명으로 구분).
  번지 없이 학교명만으로 그 위치 필지로 이동해 진단 유도.

## 2026-07-31

### 도시계획 도로 레이어 연계 — 접함 geometry + 집행여부(미집행=미개설) (전국)
- 도로대장 자체 API는 없지만 VWorld 도시계획 도로 레이어(lt_c_upisuq151)가 접촉 geometry와
  집행여부(exc_nam: 미집행/집행)를 준다. vworld.get_planned_roads(geometry) 신설 — 도로 없음
  (NO_CADASTRAL_ROAD/UNAVAILABLE)일 때만 단독 조회(gather 밖). 필지에 접하는 도시계획도로를
  집행여부로 갈라 판정: 개설=접도 근거 / 미집행=미개설이라 현재 접도 불가(조건부 유지).
  레이어가 비면 토지이음 지정목록 '접함'으로 폴백. 문구는 출처 인용 없이 사실 서술.
- 버그 수정: shape 가 get_zone_shares 안에서만 import 돼 get_planned_roads 에서 NameError→
  broad except 로 조용히 []가 되던 것(스로틀로 오인). 함수 내 지연 import 로 해결.
- 검증: 산32 소로2류 미집행(미개설) 정확 반영, 신수리 지적도로 그대로. unittest 106 OK.

### 도시계획도로(소로/중로/대로 등) 접함을 접도 근거로 반영 (전국 공통)
- 문제: road_access 가 연속지적도 '도로' 필지만 봐서, 토지이음에 도시계획시설 도로가 '접함'인
  필지도 NO_CADASTRAL_ROAD('맹지')로 떨어짐(백천동 산32: 토지이음 소로2류 접함인데 도로 없음).
  landuse.py 는 '접함'을 버려(active=False) 데이터가 있어도 안 씀.
- 수정: prediagnosis(모든 진단 공통 경로)에서 road 상태가 NO_CADASTRAL_ROAD/UNAVAILABLE 일 때
  designation_lookup.records 중 relation='접함'이고 이름이 광로/대로/중로/소로인 도시계획도로가
  있으면 status=PLANNED_ROAD_ABUTS('도시계획도로 접함·접도 검토')로 격상, planned_roads·summary
  기록. 개설 여부는 확인 대상이라 판정은 조건부 유지(다운그레이드 세트에 PLANNED_ROAD_ABUTS 추가).
- 주소 하드코딩 없이 flag 기반 전국 적용. 검증: 산32→소로2류 반영, 신수리100-2→지적도로라 그대로.
  unittest 106 OK.

### 용도지역 걸침 면적을 공부면적에 안분(토지이음 일치)
- 걸침 조각 면적이 기하면적 기준이라 조각 합이 공부면적(전체면적)과 어긋나고 토지이음
  걸침 면적과도 미세 차이(백천동 562-5: 자연녹지 62.0 vs 토지이음 61.4).
- prediagnosis 에서 zone_shares 의 area_m2 를 공부면적/기하면적 비로 스케일 → 조각 합=전체
  면적, 토지이음과 일치. share_pct(=조각/기하 비율)는 불변이라 제84조 가중평균 건폐율/용적률
  결과도 불변. 지도용 geometry 는 미변경. 검증: 640.7/61.4(토지이음 640.6/61.4), 56.5%/236.9% 유지.
- 확인된 사실: 주 용도지역은 WFS=토지이음 일치, base 건폐율/용적률(60/250·20/100)도 조례
  데이터 정상. 토지이음은 제84조 가중평균을 안 해주므로 우리 56.5%/236.9%가 실제 적용값(정확).

### 면적을 토지대장 공부면적으로(토지이음 일치) — 기하계산 대체
- 사유: area_m2 를 연속지적도 폴리곤의 측지 기하면적으로 계산해 토지이음(공부면적)과
  미세 불일치. 법적 기준은 대장 공부면적.
- 수정: `vworld.get_ledger_area_m2(pnu)` 신설 — VWorld NED 토지특성(getLandCharacteristics)
  의 lndpclAr(공부면적) 조회(기존 VWORLD_KEY, 새 키 불필요). get_parcel 이 이 값을 area_m2
  로 쓰고(공시지가와 병렬 gather), 실패 시 기하면적으로 폴백. geometry(지도·걸침·매스)는
  그대로 — 표시·규모·협소판정 면적만 공부면적. area_source 로 출처 명시.
- 검증: 한내로62 47096.1→47097.4, 신수리100-2 632.5→631.0, 백천동산32 22168.7→22169.0
  (모두 토지이음 lndpclAr 일치). 호출부 전부 단일필지라 지연 영향 없음. unittest 106 OK.
- 남은 불일치(안내): 용도지역 걸침%·건폐율/용적률은 VWorld WFS 경계 오차 기반 — 토지이음
  (getLandUseAttr) 권위값 승격이 다음 단계(별도 확인 후).

### 생태·자연도 등 규제 중첩 범례(우하단) — 죽어있던 restrictionLegend 배선
- 프런트에 규제 범례(restrictionLegend 상태·CSS·MapCanvas 핸들러·App 렌더)가 이미 있었으나
  백엔드가 show_restriction_pieces 를 안 보내 미사용 상태였음. regulatory_screen 의
  ecological_nature/ecological_separate_management/disaster overlaps 를 실어 우하단 범례로 표시.
- `map_control`: zone_pieces 뒤에 show_restriction_pieces 방출(같은 등급 라벨은 share/area 합산,
  `_restriction_color`로 등급·유형별 색). overlap 에 조각 도형이 없어 지도엔 안 깔고 범례만.
- 프런트: mapBridge 타입에서 piece.geometry 를 optional 로, 핸들러에 geometry 없으면 skip 가드,
  restrictionLegend 에 note(백엔드 제공) 반영(기존 하드코딩 "산지구분" 문구 제거). 빌드 완료.
- 검증: 백천동 산 32 → title "생태·자연도 중첩", 2등급 44.9+33.4=78.3% 병합, #EF6C00. unittest 106 OK.

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
