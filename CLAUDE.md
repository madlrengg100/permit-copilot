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
보전산지는 농지법·산지관리법 조문으로 판정한다. **적용 법령을 먼저 정하고
룰셋을 돌린다** — 국토계획법 제76조제5항제3호에 따라 농림지역 중 농업진흥지역·
보전산지는 개별법이 별표를 대체하므로 누적이 아니다(`supersedes`). 대체가 없는
조합만 누적 적용한다.
근거 조문을 추가하면 `legal_rule_catalog.json`에도 참조를 함께 등록한다.
이 축은 허용 여부와 근거 조문만 만들고 건폐율·용적률·이격 수치는 만들지 않는다.

# Claude·Codex 공동 작업 규약

이 저장소는 Claude 세션과 Codex 세션이 같은 `main` 에 직접 커밋한다. 브랜치를
파지 않으므로 아래 절차를 지켜야 서로의 작업을 덮어쓰지 않는다.

작업 순서(양쪽 동일):
1. 시작 전 `git fetch` 와 `git status` — 상대의 미커밋 변경이 남아 있을 수 있다.
2. 상대 작업과 겹치는 파일 확인. 겹치면 상대 변경을 먼저 분리해 커밋한다.
3. 자기 변경만 선택적으로 커밋한다.
4. 관련 MD 갱신(`docs/CHANGELOG-claude.md` 는 항상, 해당하면
   `docs/LEGAL-ORDINANCE-INDEX.md`·`CLAUDE.md`).
5. 테스트 수행(`backend/.venv/bin/python -m pytest tests/ -q`).
6. 표식을 붙인다.
7. `origin/main` 동기화 후 푸시. **강제 푸시 금지.**

커밋 표식 — 작성자(`permit-copilot <dx2.claude@bimatrix.co.kr>`)는 양쪽이 같으므로
이것으로만 구분한다.

| 주체 | 제목 | 트레일러 |
|---|---|---|
| Claude | (접두어 없음) | `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` |
| Codex | `codex:` 접두어 | `Co-Authored-By: OpenAI Codex <noreply@openai.com>` |

2026-08-27 이전 커밋에는 이 규약이 적용되지 않았다. 트레일러가 없는 `2e34c08`
(임상도 무결성 검사)이 Codex 작업이며, 이미 푸시됐으므로 소급 표시하지 않는다.

지도 기능을 추가할 때는 **`frontend/src/lib/mapSurface.ts`의 `MapSurface` 하나만
호출부에 노출한다.** 3D(`mapBridge.ts`, VWorld ws3d/Cesium)와 WebGL 없는 환경용
2D(`map2dBridge.ts`, OpenLayers)가 이걸 함께 구현하며, `App.tsx`·`MapCanvas.tsx`·
`MapCompass.tsx`는 구체 구현을 알지 못한다. 3D 전용 기능은 `MapSurface` 에 넣지
말고 `capabilities` 로 알린다 — 화면이 그 값으로 버튼을 감춘다. `MapBridge` 를
직접 타입으로 받으면 WebGL 이 막힌 사용자에게서 그 화면이 통째로 깨진다.

공간규제 연결 상태를 수정하기 전에 `docs/spatial-ogc.md`와
`backend/app/data/spatial_layers.json`을 함께 확인한다. 재해위험지구는
VWorld WFS `lt_c_up201`, 생태·자연도는 2026 정기고시 로컬 SQLite가 현재
단일 원본이다.
