# 필지 대화 상태와 모델 표시 규칙

## 단일 요청 프로토콜

프런트는 `/api/chat`에 다음 정보만 보낸다.

- `message`: 사용자가 입력한 원문
- `selected_parcel`: 현재 선택한 필지의 PNU·좌표·주소
- `continuation`: 현재 패널의 같은 필지 후속 질문이면 `true`

프런트에서 질문에 좌표나 판정 지시문을 문자열로 덧붙이지 않는다. 삭제한 구식
형식인 “지도에서 선택한 위치…”, “새 필지이므로 종합 판정…”, “사용자가 원하는
건축물 용도는…”을 다시 만들지 않는다.

## 상태 규칙

- 처음 보는 PNU: 종합판정을 한 번 출력한다. **단, 그 질문이 '선만' 묻는 질문(도로접촉·
  건축선·이격·배수로를 그려/보여달라거나 접도·진입로 유무를 묻고, 건축 가능여부·규모는
  함께 묻지 않음)이면** 종합판정 카드·3D 매스·가능여부 팝업을 내지 않고 요청한 선만
  그린다. 판정은 `orchestrator._is_line_only_query()`(원문 기준), 지도는
  `map_control.build_lines_only_commands()`(clear_mass·fly_to·highlight_parcel + 요청 선).
- **최초 진단 출력 순서:** 종합판정 카드·지도·가능 모델을 먼저 흘리고, '검토 의견'
  (가능/불가 판단 문단)은 무거운 상위 LLM(gemini-flash-latest)이라 지금 계산하지 않고
  `pending_judgment` 마커로 미룬다. 소비 지점(`backend/app/main.py`)이 마커를 받으면
  렌더 직전에 `tool_start(judgment)` 이벤트를 먼저 흘려 프런트에 '검토 의견 작성 중'
  진행 표시(`frontend/src/App.tsx`의 `TOOL_LABEL.judgment`)를 보인 뒤,
  `orchestrator.render_pending_judgment()`로 판단 문단을 계산·방출한다. 이 진행 표시는
  마커를 남기는 **최초 진단(emit_card=True)에서만** 나오고, 빠른 경로(flash-lite) 팔로업은
  대기 구간이 없어 표시하지 않는다.
- 같은 PNU 후속: 종합판정 카드를 반복하지 않고 질문에 대한 답만 출력한다.
- A → B → A → B: PNU별 진단과 최근 질문을 각각 보존하고 해당 필지 상태를 복원한다.
- 백엔드 재시작: 세션 상태를
  `/home/jh12535320/.permit-copilot-sessions`에서 복원한다.
- 브라우저의 선택 PNU와 백엔드 진단 PNU가 다르면 새 필지로 처리한다.
- 모델 숨김·표시는 지도 표시 상태만 바꾸며 건축 가능 판정을 변경하지 않는다.
- **지역 필지추천(recommend_areas) 후속 도달:** "○○(시·군)에 농막·창고 지을 필지
  리스트 뽑아줘"처럼 지역+조건 탐색형 질의는 후속에서도 제미나이가
  `_interpret_followup.recommend_region`(+`recommend_use`)으로 라우팅해 그 지역 비도시
  용도지역을 공간스캔한 후보 리스트를 낸다. '기능 없음'으로 회피하지 않는다. emit 단일
  원본은 `orchestrator._recommend_areas_events()`(도구루프·후속 공용). 후보 필지는 공간
  스캔이 만들며 RAG(조례 벡터)는 근거 조문 검색용일 뿐 필지 DB가 아니다.
  시군구·읍면동만 있고 번지가 없는 지역 탐색 질의는 결정적 안전망
  `orchestrator._region_search_request()`가 recommend_areas 로 보내 엉뚱한 현재 필지
  진단·무응답을 막는다. 다만 의도 표현을 정규식에 나열하는 방식은 취약하므로,
  표현 나열식 하드코딩은 지양하고 사실 추출 + LLM 판단이 방향이다.
- `다른 건물`, `가능 모델`, `뭘 지을 수 있어`는 직전 단일 용도의 재판정이 아니라
  현재 필지의 허용 용도 전체를 묻는 상태다. 팝업의 검토 용도는
  `가능한 건축물 전체`로 바꾸되 직전 단일용도 진단 원본은 보존한다.
- 모델을 선택하면 팝업의 검토 용도, 판정 배지, 이격거리와 PNU별
  `active_building_use`를 선택한 실제 용도로 갱신한다.

## 가능 모델 규칙

- **단일 원본:** 모델 버튼 허용 여부와 목록은
  `backend/app/orchestrator.py::_model_options_for_diagnosis()` 한 곳에서만 만든다.
- 프런트는 SSE `message.data.options`를 그대로 표시한다. 프런트에 용도별 모델
  판정표, 질문 정규식, 새 진단 추측 로직을 다시 만들지 않는다.
- 새 필지에서 건축 가능성 종합진단을 요청하면 진단 결과의
  `zone_use_overview.allowed + conditional` 중 준비된 모델을 표시한다.
- 건축 불가 판정이면 모델 버튼과 3D 모델·건축 치수선을 표시하지 않는다.
- 같은 필지 후속에서는 사용자가 건축물 종류·가능 모델을 명시적으로 요청할 때만
  모델 목록을 다시 표시한다.
- 같은 필지에서 '○○ 지을 수 있어?'(`intent=specific_use_feasibility`) 팔로업으로
  재진단(`emit_card=False`)했을 때도, 재진단된 용도가 가능하고 준비된 모델이 있으면
  최초 카드와 동일하게 '가능 모델' 버튼을 함께 낸다. 카드는 다시 띄우지 않지만 모델
  제시는 빠지지 않는다. 버튼 생성은 여전히 `_model_options_for_diagnosis()` 한 곳만
  담당한다(매스 있음·배치제한 아님·허용/조건부일 때만). 프런트에 모델 판정을 넣지 않는
  원칙은 그대로다.
- **되물어 확인(offer) 흐름:** 사용자가 어떤 건축물이 가능한지 '궁금해서 묻기만'
  하고 표시를 명령하진 않으면(예: `가능한 건축물이 뭐야?`, `어떤 건물 지을 수 있어?`)
  제미나이가 `offer_show_models=true`로 두고 `answer`로 `가능한 건축물 모델을 지도에
  보여드릴까요?`라고 되묻는다(모델은 아직 표시하지 않음). 이 상태는 PNU별
  `conversation_context.pending_offer="show_models"`로 저장된다. 다음 턴에 사용자가
  긍정하면(응/네/보여줘 등) 제미나이가 그 맥락을 읽어 `possible_models`로 분류하고
  그때 모델을 표시한다. 되물음 판단·긍정 인식은 모두 제미나이가 하며(정규식 아님),
  표시가 시작되거나 화제가 바뀌면 `pending_offer`를 해제한다. `보여줘/표시해/모델 켜`
  처럼 이미 표시를 명령했으면 되묻지 않고 바로 표시한다.
- 직전 요청 용도가 불가여도 사용자가 명시적으로 다른 가능 모델을 물으면,
  `zone_use_overview.allowed + conditional`에서 준비된 대체 모델을 표시한다.
  이 예외는 최초 불가 진단 하단에 모델을 표시한다는 뜻이 아니다.
- 모델 파일이 준비되지 않은 용도는 허용되더라도 버튼을 만들지 않는다. 버튼은
  허용 목록과 준비 모델의 교집합이다.
- 규제 의미, 적용 여부, 이유, 절차, 추가 확인사항을 묻는 질문에는 모델을 붙이지 않는다.

## 자연어 기능제어와 학습

- **표준 표현은 빠른 규칙**으로 즉시 처리(치수선/지적도/용도지역/경사도/팝업 켜기·끄기,
  모델 숨김·복원). 속도·안정을 위해 LLM 이전에 결정적으로 실행한다.
- **마지막 제어 대상(last_control)**: 숨김/표시 실행 시 필지 대화 상태에 대상·켜짐여부를
  남긴다. `다시 켜/복원/원래대로`는 **마지막에 '끈' 대상**을 되살린다(치수선을 껐으면
  치수선을, 모델을 껐으면 모델을). 항상-모델 복원 금지.
- **규칙이 못 잡은 표현은 제미나이**가 `_interpret_followup` 의 `control`
  {action,target,learn_term}으로 의미 해석한다(동의어·구어·오타). 추가 LLM 호출은 없다.
- **학습(control_glossary)**: 사용자가 새 표현을 쓰면(예: `수치선`) 제미나이가 learn_term
  으로 넘기고, 사용자말→대상 매핑을 `control_glossary` 에 저장한다. 세션 스냅샷에 포함돼
  재시작·재접속에도 유지된다. 학습된 표현은 다음부터 **빠른 경로에서 LLM 없이 즉시** 처리.
- 제어 대상별 지도명령·안내문구의 단일 원본은
  `orchestrator._control_command()` 와 `_control_result()` 다. 새 정규식·문구를 다른 곳에
  중복 생성하지 않는다.
- **이동('찾을 수 있어') 의도**: 주소와 함께 온 찾기/이동 표현은 진단이 아니라 그 필지로
  이동한다(`move_to_parcel`). 표준 표현 + 학습된 표현 판정은 `_requests_move_phrase()`
  단일 원본. 건축·모델 요청(건물/모델/지을/가능/진단)은 이동에서 제외한다.
- **이동 표현 학습(nav_glossary)**: 사용자가 '띄워봐라고 하면 이동하라는 뜻이야'처럼
  가르치면 제미나이가 `learn_nav_term` 으로 넘기고 `nav_glossary` 에 저장(세션 영속).
  이후 그 표현이 주소와 함께 오면 이동한다.
- **교육 문장 라우팅**: '~하라는 뜻이야/~도 되게 해/~하면 응답해야지'처럼 시스템에 의미를
  가르치는 문장은 개념 정의 질문이 아니다. `_is_teaching_statement()` 로 개념 fast-path에서
  제외해 제미나이 학습 훅(control.learn_term / learn_nav_term)에 도달시킨다.

## 필지 분할과 분할 전/후 뷰

- **분할 성립 판정**은 `tools/land_division.assess(diagnosis)` — 규제 분리(용도지역/규제구역
  걸침)·도로 후퇴(미달도로 편입)만 다룬다(일반 분할은 손으로 그려야 해 제외).
- **분할 실행**은 `orchestrator._execute_division()` — 분할 대지로 규모·부담금을 재계산하고,
  분할 후 화면에는 일반 가로·세로·높이·도로접촉·배수·이격 치수선을 내보내지 않는다
  (map_presentation.show_building_dimensions=False). 대신 분할 전용 오버레이만 얹는다:
  - 분할 대상(초록 면)·분할 제외(빨강 면) 조각(`show_zone_pieces`, persist)
  - 분할 경계선 = 흰 점선(kind="division", 면 색과 분리된 시각 문법)
  - 도로 편입 예정면(파랑 #1565C0) + 도로후퇴선(보라 #7B1FA2 평행선, kind="setback")
  - 라벨은 persist 전용 declutter 채널로 서로 겹치지 않게 배치, 박스 색은 면/선 색과 일치.
- **분할 전/후 토글**: `_division_view_request(query)` 가 '분할 전/원본/나누기 전'→"before",
  '분할 후'→"after" 를 **일반 possible_models 표시보다 먼저** 결정적으로 라우팅한다. 버튼
  (`divide:before`/`divide:after`)도 같은 원문을 보내 자연어와 단일 경로. 분할 전 원본은
  `diagnosis['_pre_division']` 에 보존해 왕복 전환한다.
- 범례는 `show_zone_pieces.legend_items`(각 항목 `symbol: "area"|"line"`)로 면·선을 구분해
  그린다(`zone-legend.is-division`). 지도 오버레이 명령·색의 단일 원본은
  `map_control.road_setback_pieces()`·`division_dimensions()` 다 — 색/문구를 프런트에 중복 정의하지 않는다.

## 책임 분리

- 프런트: 원문과 구조화 상태 전달, 백엔드가 보낸 UI·모델 결과 표시만 수행
- 백엔드: PNU별 상태, 새 필지/후속 판단, 데이터 조회, 판정·답변·모델 버튼 결정
- 제미나이: 백엔드가 제공한 현재 PNU의 구조화 진단 데이터를 자연어로 해석
- 법정 수치와 판정: 코드의 조건식 및 수집된 법령·조례 데이터가 결정하며 LLM이 만들지 않음

## 변경 후 필수 검증

1. 백엔드 전체 `unittest`
2. 프런트 `npm run build`
3. 새 PNU 첫 질문에서 `diagnosis`가 정확히 한 번 발생
4. 같은 PNU 후속에서 `diagnosis` 없이 `message`만 발생
5. 최초 진단에서 가능 모델 표시
6. 백엔드 재시작 후 같은 세션·PNU 후속 상태 복원
7. 최근 로그에서 선택 PNU와 진단 PNU 일치 확인

## 중복 구현 금지

수정 전에 먼저 이 표의 단일 원본을 찾는다.

| 기능 | 단일 원본 |
|---|---|
| 세션 저장·재시작 복원 | `backend/app/main.py`의 `_get_session`, `_save_session` |
| PNU별 A↔B 상태 | `backend/app/orchestrator.py`의 `set_selected_parcel` |
| 새 필지/후속 전달 | `/api/chat`의 `selected_parcel`, `continuation` |
| 가능 모델 목록 | `backend/app/orchestrator.py`의 `_model_options_for_diagnosis` |
| 검토 의견 지연 방출·판단 문단 렌더 | `orchestrator.render_pending_judgment` + `main.py`의 `pending_judgment` 소비 |
| 팝업 검토 범위 전환 | `set_panel_context` 지도 명령과 `frontend/src/App.tsx` 병합 처리 |
| 모델 클릭 후 실제 용도 판정 | `/api/session/{id}/setback-for-use` |
| 모델 버튼 렌더링 | `frontend/src/components/ChatPanel.tsx` |
| 3D 모델 지도 표시 | `frontend/src/lib/mapBridge.ts` |

같은 기능을 다른 파일에 새 정규식·보정 함수·임시 상태로 추가하지 않는다. 단일
원본이 부족하면 그 원본을 수정하고 회귀검사를 추가한다.
