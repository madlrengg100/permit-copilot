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

용도구역 행위제한(“이 구역에서 이 용도가 되나”)은 반드시
`backend/app/data/district_use_rules.json`만 수정한다. `zoning.py`나
`prediagnosis.py`에 특정 시설·용도지역 조건 분기를 새로 만들지 않는다.
농림지역·자연환경보전지역은 국토계획법 시행령 별표로, 농업진흥·보호구역과
보전산지는 농지법·산지관리법 조문으로 판정하며 두 축은 누적 적용한다.
근거 조문을 추가하면 `legal_rule_catalog.json`에도 참조를 함께 등록한다.
이 축은 허용 여부와 근거 조문만 만들고 건폐율·용적률·이격 수치는 만들지 않는다.

공간규제 연결 상태를 수정하기 전에 `docs/spatial-ogc.md`와
`backend/app/data/spatial_layers.json`을 함께 확인한다. 재해위험지구는
VWorld WFS `lt_c_up201`, 생태·자연도는 2026 정기고시 로컬 SQLite가 현재
단일 원본이다.
