# 작업 전 필수 확인

대화·필지 상태·가능 모델 동작을 수정하기 전에
`docs/CONVERSATION-STATE.md`를 먼저 읽고 그 규칙을 유지한다.

특히 프런트에서 사용자 질문에 좌표, 주소, “새 필지면 종합판정” 같은 자연어
지시문을 합성하지 않는다. 질문 원문과 구조화된 `selected_parcel`,
`continuation`만 백엔드로 보낸다. 같은 목적의 라우팅이나 예외 정규식을
프런트와 백엔드 양쪽에 중복해서 만들지 않는다.

가능 모델 목록은 반드시
`backend/app/orchestrator.py::_model_options_for_diagnosis()`만 수정한다.
`frontend/src/App.tsx`에 모델 허용 판정이나 질문 정규식을 추가하지 않는다.

법률·조례 수집, 청킹, 정형 수치 판정과 근거 검색을 수정하기 전에
`docs/LEGAL-ORDINANCE-INDEX.md`를 읽는다. 조례 TF-IDF 청크는 근거 검색용이며
건폐율·용적률·이격 수치를 만들지 않는다. 수치는 `ordinances*.json`,
`setbacks.json`과 결정적 조건식에서만 가져온다.

공간규제 연결 상태를 수정하기 전에 `docs/spatial-ogc.md`와
`backend/app/data/spatial_layers.json`을 함께 확인한다. 재해위험지구는
VWorld WFS `lt_c_up201`, 생태·자연도는 2026 정기고시 로컬 SQLite가 현재
단일 원본이다.
