# Claude 변경 기록 (Codex 협업용)

> Claude(Opus)가 수정한 지점을 남긴다. **Codex는 이 목록을 먼저 보고 같은 로직을
> 중복 구현하지 말 것.** 규제 수치는 데이터파일에서만(하드코딩 금지),
> 가능 모델 목록은 `orchestrator._model_options_for_diagnosis()`만 — CLAUDE.md 준수.

## 2026-08-03

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
