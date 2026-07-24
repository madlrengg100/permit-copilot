# 공간정보 기반 인허가 사전진단 시스템 — 아키텍처 및 기능 명세

문서 버전 1.0 · 작성일 2026-07-23 · 대상 코드베이스 `permit-copilot`

> **이 파일은 docx 변환용이다.** 내용은 `SYSTEM-SPEC.md` 와 같고 다이어그램만
> Word 에서 깨지지 않는 고정폭 도해·표로 바꿨다.
>
> 이 문서는 실제 소스를 전수 확인하고 작성했다. 기재된 수치·필드명·동작은 구현에서
> 확인한 것이며, 미구현·미수집 항목은 [11장](#11-알려진-한계와-미구현-항목)에 분리해 기록했다.

---

## 1. 시스템 개요

### 1.1 목적

자연어 질의 한 건으로 **필지 특정 → 법령·조례 기반 건축 가능성 판정 → 3D 시각화**까지
수행하는 인허가 사전검토 시스템.

| 구분 | 내용 |
|---|---|
| 입력 | `"충청남도 예산군 삽교읍 두리 100에 공장 지을 수 있어?"` (자연어) 또는 지도 클릭 |
| 처리 | 공공 공간정보 조회 → 용도지역·지목 판정 → 조례/법정 상한 적용 → 규모 산출 |
| 출력(텍스트) | 판정(가능/조건부/불가) · 근거 법령 · 건폐율/용적률 · 건축면적/연면적/층수 |
| 출력(지도) | 필지 이동·강조 → 건축 가능 규모 3D 생성 → 결과 패널 부착 → (걸침 시) 용도지역 조각 표시 |

### 1.2 설계 원칙

| 원칙 | 구현 |
|---|---|
| **LLM은 언어 처리만** | 판정·계산·묘화는 전부 결정적 코드. LLM은 ①도구 선택 ②주소·용도 추출 ③답변 작성에만 사용 |
| **규제 수치 하드코딩 금지** | 건폐율·용적률은 코드가 아닌 `ordinances.json`(시행령·조례 원문 확인값)에서 조회 |
| **모르는 값은 지어내지 않음** | 미수집 항목은 `null` + 사유 기록. 비도시지역은 조례 미확보 시 **판정 불가** 반환 |
| **결과는 추정치임을 상시 고지** | 화면 상시 고지 문구 + 답변 말미 단서 + 프롬프트 규칙으로 삼중 적용 |

### 1.3 기술 스택

| 계층 | 기술 | 버전 |
|---|---|---|
| 프론트엔드 | React + TypeScript + Vite | Node 24.18.0 |
| 3D 지도 | VWorld 3D WebGL (ws3d, Cesium 기반) | WS3DRelease3 |
| 백엔드 | Python + FastAPI + uvicorn | Python 3.9.25 / FastAPI 0.128.8 |
| 공간 연산 | shapely 2.0.7, pyproj 3.6.1 | |
| LLM 클라이언트 | Anthropic SDK 0.117 / OpenAI SDK 2.46 | |
| **LLM (현재 운영)** | **Google Gemini `gemini-flash-lite-latest`** | OpenAI 호환 엔드포인트 경유 |

> **"OpenAI SDK"는 통신 규약이지 사용 모델이 아니다.** Gemini·Groq·Cerebras·OpenRouter는
> 모두 OpenAI 형식의 `/chat/completions` 를 제공하므로, OpenAI SDK로 base_url만 바꿔
> 호출한다. 현재 설정은 `LLM_PROVIDER=openai` + `LLM_BASE=gemini` 이며 **실제 응답 모델은
> Gemini**다. 이 구조 덕분에 코드 수정 없이 환경변수만으로 provider를 교체할 수 있다([5.4](#54-llm-어댑터)).

---

## 2. 전체 아키텍처

```text
┌────────────── 프론트엔드 · React + Vite · :5173 ──────────────┐
│ ChatPanel      MapCanvas       MapBridge      ResultPanel+범례 │
│ 질의·대화로그   VWorld 3D 로드   지도 명령 실행  건물 위 부착     │
└───┬───────────────────────────────────▲──────────────────────┘
    │ POST /api/chat                    │ SSE: map_commands
    ▼                                   │
┌────────────── 백엔드 · FastAPI · :8000 ───────────────────────┐
│ main.py ──► Orchestrator ──► 사전진단 에이전트 ──► 도구 계층    │
│ SSE·인증     LLM 도구 루프    결정적 파이프라인   vworld/zoning │
│                  │                              ordinance/    │
│                  └──► 지도제어 에이전트           jimok/massing │
│                       (LLM 미사용)                footprint    │
└─────┬─────────────────┬────────────────┬─────────────────────┘
      ▼                 ▼                ▼
  LLM              VWorld OpenAPI    ordinances.json
  Anthropic /      지오코더·검색·     시행령 +
  OpenAI 호환       2D데이터          지자체 조례
```

**배포 형태**: 프론트 포트(5173)만 외부 노출. `/api` 는 Vite 프록시가 8000으로 중계하므로
`VITE_API_BASE` 는 비워 둔다(단일 오리진).

---

## 3. 처리 흐름

### 3.1 질의 1건의 전체 시퀀스

```text
사용자 ── "두리 100에 공장 지을 수 있어?" ──► 프론트엔드
                                             │ POST /api/chat
                                             ▼
① Orchestrator ──► LLM   도구 선택 (SYSTEM + TOOLS)
               ◄──       prediagnose(query=…)        → tool_start
                                             ▼
② 사전진단 ──► LLM       주소·용도 추출 (submit_request 강제)
           ◄──           {address, building_use, inferred}

   ── 이하 LLM 미사용 · 결정적 파이프라인 ──
   VWorld : geocode ─► get_parcel ─► get_land_use
            ─► get_zone_shares (필지 폴리곤 교차)
   계산   : jimok.classify ─► zoning.lookup ─► massing.calc
                                     → diagnosis_step ×6~7
                                     → diagnosis
                                             ▼
   지도제어 ──► [clear_mass, fly_to, highlight_parcel,
                 show_zone_pieces, extrude_mass, show_panel]
                                     → map_commands
                                             ▼
                       MapBridge.execute() — 지도 묘화

③ Orchestrator ──► LLM   결과 종합 답변
               ◄──       최종 텍스트          → message → done
```

**LLM 호출 = 질의당 3회**(①도구 선택 ②주소·용도 추출 ③답변). 초기 구현은 9회였고,
무료 티어 일일 한도를 질의 2~3건으로 소진했다. 근거는 [5.2](#52-사전진단-에이전트).

### 3.2 지도 클릭 진입 경로

지도 클릭 시(드래그 5px 초과는 무시) `GET /api/parcel-at` 으로 경계를 먼저 그린 뒤,
좌측 채팅에 *"필지를 선택했습니다. 무슨 건물을 짓고 싶은가요?"* 를 띄우고 입력창에
`"선택한 필지에 "` 를 채운다. 사용자가 용도를 입력하면 좌표를 포함한 질의로 변환해 전송한다.

---

## 4. HTTP 인터페이스 명세

### 4.1 엔드포인트

| 메서드 | 경로 | 파라미터 | 응답 | 인증 |
|---|---|---|---|---|
| GET | `/api/config` | — | `{vworld_key, mock_mode, llm_provider, llm_model}` | 없음 |
| GET | `/api/parcel-at` | `lon`, `lat` (float, 필수) | 필지 객체(§6.1) | 없음 |
| GET | `/api/address-search` | `q` (str, 최소 2자) | `{items: [{title, road, parcel, address, lon, lat}]}` | 없음 |
| GET | `/api/parcels` | `west, south, east, north` (float) | `{geometries: [...]}` | 없음 |
| POST | `/api/chat` | body `{session_id, message}` | SSE 스트림 | `X-App-Token` |
| DELETE | `/api/session/{id}` | path `id` | `{reset: true}` | 없음 |

`/api/chat` 은 `text/event-stream`, 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

### 4.2 SSE 이벤트

| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `tool_start` | `{tool}` | 도구 실행 직전 |
| `diagnosis_step` | `{step, input}` | 사전진단 각 단계 |
| `diagnosis` | 진단 전체 dict | 진단 완료 |
| `map_commands` | `{commands: [...]}` | 지도 명령 생성 |
| `message` | `{text}` | 모델 답변 |
| `error` | `{tool, message}` 또는 `{message}` | 도구 실패 / 스트림 예외 |
| `done` | `{}` | 성공·실패 무관 종료 시 항상 |

`diagnosis_step` 값: `extract_request` · `geocode_address`(주소 경로만) · `get_parcel` ·
`get_land_use` · `check_zone_overlap` · `lookup_zoning` · `calc_massing`(조건부)

> **구현 참고**: 진행 이벤트는 `run_prediagnosis` 가 완료된 뒤 일괄 방출된다(콜백이 리스트에
> 적재만 함). 단계별 실시간 스트리밍이 아니다.

### 4.3 인증 및 보안

- `APP_TOKEN` 설정 시 `/api/chat` 에 `X-App-Token` 헤더 일치 요구. 불일치 → **401**.
  미설정 시 완전 개방되며 기동 로그에 경고 배너 출력.
- **`/api/chat` 만 보호된다.** LLM API를 호출하므로 비용 소진 방어가 목적. 나머지 조회 API와
  `/api/config`(VWorld 키 반환)는 인증 없이 열려 있다.
- CORS: `ALLOWED_ORIGINS` 목록, 메서드·헤더 전체 허용, `allow_credentials` 미설정(False).

### 4.4 오류 메시지 변환 (`friendly_error`)

SDK 원문 오류를 조치 가능한 문장으로 변환한다. 판정 순서:

| 순서 | 조건(문자열 포함) | 안내 |
|---|---|---|
| 1 | `authentication_error`, `Incorrect API key` 등 | 키 미설정/오류 + 서비스명·모델·환경변수명 |
| 2 | `insufficient_quota`, `RESOURCE_EXHAUSTED` | 사용 한도 + 서비스별 확인처 |
| 3 | `model_not_found` | 모델 사용 불가 |
| 4 | `credit balance is too low` | 크레딧 부족 |
| 5 | `rate_limit`, `429` | 요청 한도 |
| 6 | `VWorld` | 공간정보 조회 실패 + 키·도메인 확인 안내 |

**서비스명을 하드코딩하지 않는다.** Gemini 사용 중 "OpenAI 결제 확인" 을 안내하던 결함이
있었으며, `_provider_label()` 이 실제 호출 중인 서비스명을 반환하도록 수정했다.

---

## 5. 백엔드 컴포넌트

### 5.1 오케스트레이터

세션 1건 = `Orchestrator` 인스턴스 1개(프로세스 메모리, TTL 없음). 대화 이력과 직전 진단 보유.

**도구 3종**

| 도구 | 입력 | 역할 |
|---|---|---|
| `prediagnose` | `query`(필수) | 사전진단 실행 + 지도 반영 |
| `render_on_map` | 없음 | 직전 진단을 지도에 재반영 |
| `restudy_massing` | `far_target_pct`(필수), `bcr_target_pct` | 재조회 없이 밀도만 바꿔 규모 재산출 |

**SYSTEM 프롬프트 규칙(요약)**

- 라우팅: 주소·좌표 질의 → `prediagnose` 우선 / 조건 변경 후속 질의 → `restudy_massing` /
  주소 없는 일반 법령 질문 → 도구 없이 답변 / 모호한 주소 → 되묻고 **임의 생성 금지**
- 답변: 결론 우선 → 근거 수치 제시 → **법정 상한 이론값이며 조례·일조·이격·주차로 축소됨을 필수 명시**
- 용도 열거 질의는 `regulation.zone_use_overview` 근거로 가능/조건부/불가 전부 나열하되
  **9개 대분류 개요이며 별표1 전체가 아님**을 함께 밝힌다
- 용적률 초과 요청(`exceeds_far_limit`) 시 요청 규모의 층수·연면적을 제시하지 말고
  적용 불가 결론과 건폐율 기준 최대 건축면적만 제시
- 산출 결과는 **'건축 가능 규모'** 로 호칭. `'매스'`(설계 실무 용어)는 사용 금지

**턴 루프**: 최대 8턴. 도구 호출이 없으면 종료. 도구 예외는 턴별로 포착되어 오류 문자열이
모델에게 전달되므로 대화가 끊기지 않는다.

**의도적 최적화**: `prediagnose` 완료 후 `render_on_map` 을 모델에게 재차 묻지 않고 즉시
실행한다. 지도 반영은 확정 절차라 판단시켜도 결과가 같고 LLM 호출만 증가한다.

### 5.2 사전진단 에이전트

**도구 루프를 쓰지 않는 이유** — 조회 순서가 고정(주소→좌표→필지→용도지역→규제→규모)이고
분기가 하나뿐(불허 시 규모 산출 생략)이다. 매 단계 LLM에 물으면 호출이 6~7회로 늘고
지연·비용·실패 지점이 함께 증가한다. 판정 수치는 어차피 도구가 계산하므로 정확도 손실이 없고,
오히려 모델이 순서를 건너뛰거나 인자를 틀릴 여지가 사라진다.

**파이프라인**

| # | 단계 | 구현 |
|---|---|---|
| 1 | 주소·용도 추출 | `extract_request()` — **유일한 LLM 호출**, `submit_request` 도구 강제 |
| 2 | 좌표 확보 | 좌표 직접 입력 시 생략, 아니면 `vworld.geocode()` |
| 3 | 필지 조회 | `vworld.get_parcel()` — PNU·지번·지목·면적·경계 |
| 4 | 용도지역 조회 | `vworld.get_land_use()` — 4개 레이어 전수 |
| 5 | **걸침 확인** | `vworld.get_zone_shares()` — 필지 폴리곤 교차 면적 안분 |
| 6 | 지목 판정 | `jimok.classify()` — 전용허가 필요성 |
| 7 | 규제 조회 | `zoning.lookup_zoning_rules()` — 허용 여부 + 밀도 상한 |
| 8 | 규모 산출 | `massing.calc_massing()` — 불허가 아니고 건폐율이 있을 때만 |
| 9 | 요약 조립 | `_summarize()` — **LLM 없이 값에서 조립** |

**`building_use` 누락 보정** — Gemini의 OpenAI 호환 모드는 `required` 를 강제하지 않아 낮은
확률로 `building_use` 를 빠뜨린다. 그대로 두면 진단 전체가 `KeyError` 로 죽으므로, 질의 원문에서
용도를 되찾는 결정적 보정(`_guess_use`)을 둔다: 정식 용도명 부분일치 → 일상어 키워드
(창고→창고시설, 빌딩→업무시설 등 17종) → 기본값 `업무시설`.

**`compact()`** — 모델에게 전달할 축약본. `request` 전체, `parcel.geometry`,
`land_use.zone_shares[*].geometry` 를 제거한다(좌표 수천 개가 컨텍스트를 잠식).

### 5.3 지도제어 에이전트

**LLM 미사용 순수 변환기.** 진단이 판단을 끝냈으므로 '무엇을 어떻게 그릴지'만 결정하며 규칙은 확정적이다.

명령 순서: `clear_mass` → `fly_to` → `highlight_parcel` → `show_zone_pieces`(조건부) →
`extrude_mass`(조건부) → `show_panel`

**카메라 고도** — 필지 크기 비례. 고정 고도는 작은 필지를 점으로, 큰 필지를 화면 밖으로 만든다.

```
altitude = clamp(√면적 × 2, 60, 700)   [지면 위 높이, m]
```
근거: 부각 35°에서 시거리 ≈ 1.74h, 수직화각 60°면 화면 폭 ≈ 2h.
필지가 화면 1/4을 차지하려면 h ≈ 2·side.

**카메라 방위각** — 필지 최장변이 화면 가로로 놓이도록 계산. 길고 좁은 필지를 짧은 쪽에서
정면으로 보면 낮은 건물도 가느다란 탑처럼 보인다.

**불허 필지에는 건물을 그리지 않는다** (`verdict != "not_allowed"`). 지을 수 없는 곳에 건물을
표시하면 가능한 것으로 읽힌다.

**용도지역 조각 색상** — 지적편집도 관례(주거 노랑 / 상업 분홍 / 공업 보라 / 녹지 초록 /
관리 연두, 21개 지역 정의). 단, 걸친 지역들이 같은 색 계열이면(자연녹지·생산녹지 등 색상환
40° 이내) 반투명 렌더 시 구분이 불가능하므로, 뒤 조각을 대비색(파랑→보라→주황→노랑)으로
교체한다. 색의 의미는 우측 범례가 설명한다.

### 5.4 LLM 어댑터

Anthropic과 OpenAI 호환 엔드포인트를 동일 인터페이스로 흡수한다.

**`LLM_PROVIDER` 는 '통신 규약' 선택이지 '회사' 선택이 아니다.** `openai` 를 지정해도
`LLM_BASE` 에 따라 실제 응답 모델은 달라진다:

| 설정 | 통신 규약 | 실제 모델 |
|---|---|---|
| `LLM_PROVIDER=anthropic` | Anthropic Messages API | Claude |
| `LLM_PROVIDER=openai` (LLM_BASE 없음) | OpenAI `/chat/completions` | GPT |
| **`LLM_PROVIDER=openai` + `LLM_BASE=gemini`** ← 현재 | OpenAI `/chat/completions` | **Gemini** |
| `LLM_PROVIDER=openai` + `LLM_BASE=groq` | 〃 | Llama (Groq) |

이 구조 덕분에 판정 로직·프롬프트·도구 정의를 그대로 둔 채 provider를 교체할 수 있다.
Anthropic 크레딧 소진 → OpenAI 결제 불가 → Gemini 무료 티어로 두 차례 전환하는 동안
에이전트 코드는 한 줄도 바뀌지 않았다.

| | Anthropic | OpenAI 호환 |
|---|---|---|
| 도구 정의 | `{name, description, input_schema}` 원형 | `{type:"function", function:{…}}` 변환 |
| system | 별도 인자 | `messages[0]` 삽입 |
| 도구 결과 | user 메시지 1개에 `tool_result` 블록 | 호출당 `role:"tool"` 메시지 1개 |

**`_tool_call_to_dict()` 주의** — 응답의 tool_call을 이력에 되돌릴 때 필드를 선별하면 안 된다.
Gemini는 `extra_content.google.thought_signature` 동반을 요구하며, 누락 시
`400 Function call is missing a thought_signature` 로 거부한다. 원본을 통째로 직렬화한다.

**무료 티어 프리셋** — `LLM_BASE` 에 이름만 지정하면 엔드포인트·모델·키 환경변수가 자동 적용.

| `LLM_BASE` | 엔드포인트 | 기본 모델 | 키 환경변수 |
|---|---|---|---|
| `gemini` | generativelanguage.googleapis.com/v1beta/openai/ | `gemini-flash-latest` | `GEMINI_API_KEY` |
| `groq` | api.groq.com/openai/v1 | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| `cerebras` | api.cerebras.ai/v1 | `llama-3.3-70b` | `CEREBRAS_API_KEY` |
| `openrouter` | openrouter.ai/api/v1 | `llama-3.3-70b-instruct:free` | `OPENROUTER_API_KEY` |

---

## 6. 도구 계층 명세

### 6.0 공개 함수 일람

| 모듈 | 함수 | 역할 |
|---|---|---|
| `vworld` | `geocode(address)` | 주소 → 좌표 (ROAD → PARCEL 순차) |
| | `search_addresses(query, size=8)` | 주소 자동완성 후보 |
| | `get_parcel(lon, lat)` | 점 → 필지(PNU·지번·지목·면적·경계) |
| | `get_parcels_bbox(w,s,e,n)` | 범위 내 지적선 목록(2D 모드용) |
| | `get_land_use(lon, lat)` | 점 → 용도지역·지구 (4개 레이어) |
| | `get_zone_shares(geometry)` | **필지 폴리곤 → 용도지역별 면적 안분** |
| | `outer_rings(geometry)` | Polygon/MultiPolygon → 외곽 링 통일 |
| | `geodesic_area_m2(geometry)` | 측지 면적 계산(pyproj Geod) |
| `zoning` | `lookup_zoning_rules(zone, use, districts, jurisdiction)` | 허용 판정 + 밀도 상한 |
| | `uses_for_zone(zone)` | 지역 → 용도별 허용 상태 역인덱스 |
| `ordinance` | `resolve_limits(zone, jurisdiction)` | **조례/법정 상한 결정(우선순위 적용)** |
| | `detect_jurisdiction(address)` | 주소 → 조례 보유 지자체 |
| | `separate_ordinance_warning(address, juris)` | 별도 조례 자치구 경고 |
| | `jurisdictions()` / `statutory_limits()` | 데이터 접근자 |
| | `compare(zone)` / `largest_gaps(n)` | 법정 대비 조례 비교 (**CLI 전용**) |
| `massing` | `calc_massing(area, bcr, far, far_target)` | 건축면적·연면적·층수·높이 |
| `footprint` | `inset_for_area(geojson, ratio)` | 경계 안쪽 오프셋 배치 도형 |
| `jimok` | `classify(jimok)` | 지목 → 전용허가 필요성 |
| | `normalize(code_or_name)` | 한 글자 코드 → 지목명 |

### 6.1 `vworld.py` — 공간정보 조회

**호출하는 VWorld API**

| 함수 | service | 레이어/파라미터 |
|---|---|---|
| `geocode` | address | `getcoord`, ROAD → PARCEL 순차 시도 |
| `search_addresses` | search | road → parcel 카테고리, HTML 태그 제거·중복 제거 |
| `get_parcel` | data | `LP_PA_CBND_BUBUN`, `POINT(lon lat)` |
| `get_parcels_bbox` | data | `LP_PA_CBND_BUBUN`, `BOX(...)`, size 1000 |
| `get_land_use` | data | 용도지역 4개 레이어, `POINT`, geometry=false |
| `get_zone_shares` | data | 용도지역 4개 레이어, `BOX`(필지 외접), geometry=true |

**필지 반환 필드**: `pnu`, `jibun`, `jimok`, `area_m2`, `area_source`, `jiga_won_per_m2`, `geometry`

**구현상 확인된 VWorld 특성**

| 특성 | 대응 |
|---|---|
| `service=data` 는 등록 도메인 검증. 백엔드 호출은 Referer가 없어 `INCORRECT_KEY` | `domain` 파라미터로 `VWORLD_DOMAIN` 전송. **이 값은 접속 URL이 아니라 인증키 등록 서비스URL** |
| 오류도 **HTTP 200** 으로 반환 | `response.status == "ERROR"` 직접 확인 |
| 연속지적도에 **면적 필드 없음** | 경계에서 pyproj `Geod` 측지면적 계산 + `area_source` 로 출처 명시 (법적으로는 토지대장 면적 우선) |
| 지목이 지번 끝에 한글로 붙고 띄어쓰기 불규칙 (`'737 대'`, `'100-10 도'`, `'1유'`) | 공백 분리가 아닌 **끝 한글 추출** 정규식 |
| 용도지역 레이어가 4종으로 분리 | `UQ111`(도시) `UQ112`(관리) `UQ113`(농림) `UQ114`(자연환경보전) **전수 조회**. 과거 UQ112가 비도시 전체를 포함한다고 오인해 농림지역 조회가 실패한 이력 |
| 필지 폴리곤을 `geomFilter` 에 직접 넣으면 URL 한도 초과 | 서버에는 **외접 BOX**로 후보만 요청, 정밀 교차는 shapely로 로컬 계산 |

**`get_zone_shares` (걸침 감지)** — 필지 폴리곤과 용도지역 폴리곤의 교차 면적을 안분한다.
`buffer(0)` 로 자기교차 보정, 1% 미만 스침은 데이터 오차로 간주해 제외, 면적 내림차순 정렬.
반환: `[{zone, area_m2, share_pct, geometry}]`

### 6.2 `zoning.py` — 용도별 허용 판정

`USE_MATRIX` — 9개 건축물 용도 × `allowed`(조례 없이 원칙 허용) / `permitted`(조례가 정하는
범위에서 조건부). 어느 쪽에도 없으면 불허.

`uses_for_zone(zone)` — 역인덱스. "이 필지에 뭘 지을 수 있어?" 열거 질의에 사용하며
결과가 항상 `regulation.zone_use_overview` 로 전달된다.

`CONSTRAINT_NOTES` — 7개 용도지구/구역(지구단위계획·경관·고도·방화·개발제한·문화재보호·
학교환경위생정화)의 실무 주의사항. **해당 지구가 있으면 판정을 조건부로 하향**한다.

수치를 이 파일에 두지 않는 이유: 손으로 옮긴 표에서 용적률 하한 7건이 틀렸던 이력이 있다.

### 6.3 `ordinance.py` — 조례·법정 상한 조회

**우선순위**

`resolve_limits(zone, jurisdiction)` 판정 순서 (위에서부터 먼저 적용):

**핵심 규칙 두 가지**

1. **`regulated` 판정** — 항목이 존재해도 `bcr_max_pct`·`far_max_pct` 가 모두 `null` 이면
   '조례 미규정'이다. 데이터셋이 미규정 항목도 사유를 담은 placeholder로 보관하므로,
   이를 조례 적용으로 취급하면 **존재하지 않는 조문을 근거로 인용**하게 된다.
2. **비도시지역 안전장치** — `계획관리·생산관리·보전관리·농림·자연환경보전지역` 은 실제 밀도를
   시·군 조례가 정한다. 조례를 확보하지 못하면 법정 상한으로 대체 계산하지 않고 **판정 불가**를
   반환한다(과다 산정 방지).

용적률 **하한**은 조례가 규정하지 않으므로 사실상 항상 법정값이다.

### 6.4 `massing.py` — 규모 산출

```
건축면적 = 대지면적 × 건폐율 / 100
연면적   = 대지면적 × 용적률 / 100
층수     = 연면적 / 건축면적           (건폐율을 꽉 채운 이론 층수)
floors   = ceil(층수)                  ← 3.33층은 4층(최상층 33%)
높이     = floors × 3.3m
```

`far_target_pct` 가 법정 상한을 초과하면 **계산에는 상한값을 쓰고** `exceeds_far_limit=true`,
`requested_far_pct` 로 요청값을 보존한다. 초과 요청의 층수·높이는 프롬프트·요약·`flat_only`
플래그에 의해 화면과 답변 모두에서 제시되지 않는다.

`top_floor_ratio`(최상층 잔여 비율)를 함께 반환해 3D에서 최상층을 부분 층으로 표현한다.

### 6.5 `footprint.py` — 건축면적 배치

`inset_for_area(geojson, area_ratio)` — 필지 경계에서 **안쪽으로 오프셋**해 목표 면적
(대지면적 × 건폐율)이 되는 도형을 찾는다.

1. 필지 대표점 기준 **AEQD 국지 투영**(미터 단위)으로 변환
2. 음의 버퍼 거리를 **이분 탐색 48회** — 목표 면적을 만족하는 최대 오프셋 수렴
3. 원본 필지와 교차시켜 EPSG:4326으로 역변환

중심점 균등 축소가 아니라 음의 버퍼이므로, **오목한 필지에서도 결과가 항상 원 필지 내부**에 있다.

> **주의**: 이는 면적 총량을 형상으로 표현한 것이며 **법정 이격거리 계산이 아니다.**
> 정북방향 일조 이격·대지 안의 공지·도로 후퇴선은 반영되지 않는다.

### 6.6 `jimok.py` — 지목 기반 전용허가 판정

VWorld는 지목을 한 글자 코드로 준다(공간정보관리법 시행령 제58조, 28종). `JIMOK_CODE` 로
코드→명칭 변환 후 4개 범주로 분류한다.

| 범주 | 지목 | 선행 절차 | 추가 확인 레이어 |
|---|---|---|---|
| `farmland` | 전·답·과수원 | 농지전용허가(농지법) | 농업진흥지역 |
| `forest` | 임야 | 산지전용허가(산지관리법) | 보전산지구분, 경사도(DEM) |
| `buildable` | 대·공장용지·창고용지 등 7종 | 불필요 | — |
| `other` | 그 외 | 지목변경 필요 여부 확인 | — |

**적용 범위 한정**: `requires_conversion` 은 **'절차가 필요한가'** 이지 **'전용이 가능한가'** 가
아니다. 가능 여부는 농업진흥지역 지정·보전산지 구분·경사도 등 별도 레이어 조회가 필요하다.

모든 분기가 **동일한 키 집합**을 반환한다(소비 측에서 분기별 키 확인을 강제하지 않기 위함).

---

## 7. 데이터 명세 — `ordinances.json`

```
_meta.statutory_reference.limits   국토계획법 시행령 제84·85조 법정 상한 (21개 용도지역)
_meta.sources[]                    조례명 · 조례번호 · 시행일 · 조문 · ELIS URL
<지자체명>.<용도지역>               지자체 도시계획조례 실제 적용값
<지자체명>._source                  근거 조례 메타
```

**수집 현황** (전부 ELIS 자치법규정보시스템 **원문 HTML**에서 직접 확인)

| 지자체 | 조례 규정 건수 / 21개 지역 | 수집 범위 |
|---|---|---|
| 서울특별시 | 16 | 도시지역 중심 |
| 부산광역시 | 17 | 도시지역 중심 |
| 인천광역시 | 21 | 전체 |
| 대구광역시 | 21 | 전체 |
| 경기도 성남시 | 16 | 도시지역 중심 |
| 충청남도 아산시 | 5 | **비도시 5종만**(계획·생산·보전관리, 농림, 자연환경보전) |
| 충청남도 예산군 | 5 | **비도시 5종만**(동일) |

충남 2곳은 비도시지역 검토를 위해 관리·농림·자연환경보전지역만 수집했다. 해당 지자체의
**도시지역 용도지역을 질의하면 법정 상한으로 폴백**된다.

**값이 없으면 지어내지 않고 `null` + 사유를 기록한다.**

조례는 법정 상한 이내에서 더 강하게 정할 수 있다. 실례로 서울 일반상업지역은 법정 1300% 대비
조례 800%(**−500%p**)이며, 미반영 시 규모가 크게 과다 산정된다.

**별도 조례 자치구 경고** — 부산 영도·동래·금정·사상·기장은 별도 조례를 둔다. 수치를 수집하지
않았으므로 추정하지 않고 경고만 표시한다.

---

## 8. 프론트엔드 명세

### 8.1 VWorld 3D 엔진 부트스트랩

공식 문서가 아닌 **번들 소스 분석**으로 확인한 사항 위에 구현되어 있다.

| # | 사실 | 대응 |
|---|---|---|
| 1 | `webglMapInit.js.do` 는 부트스트랩이며 `document.write()` 로 엔진을 붙인다. `appendChild` 주입 시 `document.write` 는 무시되어 엔진이 로드되지 않음 | 부트스트랩 동작을 코드로 재현 |
| 2 | 엔진이 jQuery 전역에 의존하나 부트스트랩은 로드하지 않음. jQuery 3에서 제거된 `.size()` 사용 | jQuery 주입 + `.size()` shim |
| 3 | `vw.MapController.initMap()` 부재(`MapController` 는 이벤트 상수) | `new vw.Map()` → `setMapId()` → `start()` |
| 4 | `vw.BasemapType` 은 `GRAPHIC` 하나뿐. `"PHOTO"` 는 렌더 후 깨짐 | `GRAPHIC` 고정 |
| 5 | `ws3d.viewer` 는 **재정의 불가 속성** — 두 번 초기화 시 예외. React StrictMode가 effect를 2회 실행 | 모듈 레벨 메모이즈 Promise로 1회 보장, 실패해도 재시도 안 함 |
| 6 | `vw.Direction` tilt는 **도(degree)** 이고 Cesium pitch로 직결. 기본값 `+60` 은 **하늘을 봄**. `scene.camera` 는 라디안 | `-45` 사용, 단위 혼용 주의 |

**초기 시점 고정(8초)** — 엔진 내부에 카메라를 움직이는 주체가 여럿(Lookat/Drive/Fly 애니메이터,
초기 비행, 지형 로드 후 재배치)이라 하나를 막으면 다른 것이 움직인다. `requestAnimationFrame`
으로 매 프레임 되돌리고, 사용자 조작 또는 지도 명령 수신 시 즉시 해제한다.

### 8.2 지도 명령 (`MapCommand`)

| 명령 | 필드 |
|---|---|
| `clear_mass` | — |
| `fly_to` | `lon, lat, altitude, tilt, heading?` |
| `highlight_parcel` | `geometry, pnu, label, color` |
| `show_zone_pieces` | `pieces[{zone, share_pct, area_m2, color, geometry}]` |
| `extrude_mass` | `geometry, footprint_geometry, top_footprint_geometry, anchor, height_m, floors, full_floors, top_floor_ratio, flat_only, footprint_ratio, color, opacity, label` |
| `show_panel` | `anchor, verdict, verdict_label, color, address, zone, districts, building_use, site_area_m2, bcr_max_pct, far_max_pct, legal_basis, constraints, zone_use_overview, massing` |

**`fly_to` 는 지연 실행된다** — 지형 상대 Entity가 먼저 생성돼야 카메라 목표가 실제 건물 위치와
일치하므로, 루프에서 보류했다가 마지막에 실행한다. 카메라는 건물 **높이의 절반**을 겨냥한다.

### 8.3 카메라 계산

```
depression = 90 − tilt                          # tilt 55 → 부각 35°
backOff    = altitude / tan(depression)         # 기울인 카메라는 앞을 보므로 후퇴 필요
latBackOff = backOff · cos(heading) / 111320
lonBackOff = backOff · sin(heading) / (111320 · cos(lat))
```

고정 오프셋을 쓰면 고도·각도 변경 시마다 대상이 화면 위아래로 밀린다.

**지형고 2단계 보정** — 같은 지점의 표고가 첫 조회 140m, 재조회 −55m로 나오다 타일 안정 후
수렴한다. 일단 그린 뒤 표고가 0.2m 이내로 8프레임 안정되면 재정렬하며, `cameraGeneration`
카운터가 새 명령 도착 시 진행 중인 보정을 무효화한다.

### 8.4 좌표·높이 규약 ⚠

**높이 기준이 API마다 다르다.** 이 시스템에서 결함이 가장 많이 발생한 지점이다.

| 대상 | 기준 |
|---|---|
| Cesium 카메라 고도 | **해수면 절대** — 지형고를 직접 가산 |
| 백엔드 `fly_to.altitude`, `anchor.height` | **지면 위** |
| `toScreen()` | **해수면 절대** |
| `toScreenAboveGround()` | 지면 위 → 내부에서 지형고 가산 |
| 필지·건물·마커·지적선 | `RELATIVE_TO_GROUND` / `clampToGround` |

> **기록해 둘 실패 사례**: `setDistanceFromTerrain()` 은 이름과 달리 '지형으로부터의 거리'가
> 아니다. 번들에서 `createPolygons(poly, getDistanceFromTerrain()==0, {height: getDistanceFromTerrain(), …})`
> 로 쓰이며 — **0이면 지면 클램프, 0이 아니면 그 값이 바닥의 절대고도**다. 이름만 보고 상대값으로
> 해석해 지형고를 가감하다 건물이 땅에 묻히거나 283m 상공의 기둥이 되는 증상이 반복됐다.
> **엔진 API는 이름이 아니라 소스로 확인할 것.**

### 8.5 화면 구성 요소

| 구성 | 동작 |
|---|---|
| ChatPanel | 예시 질의 버튼, 진행 상태 표시, **상시 고지 문구**(입력창 위 고정) |
| 주소 자동완성 | 입력 중 `GET /api/address-search` 로 후보 제시(도로명 → 지번 순, 최대 8건) |
| 결과 패널 | 건물 위 화면 좌표에 부착. `camera.changed` + `postRender` 구독으로 카메라 추종 |
| **용도지역 범례** | 우측 하단. 걸침 필지에서만 표시(색·지역명·비율·면적 + 참고용 단서) |
| 2D/3D 전환 | VWorld 컨트롤이 Cesium `SceneMode.2D` 와 비호환(`morphTo2D` 가 렌더러 정지)하여 **SceneMode는 3D 유지, 카메라만 수직화**. 2D에서 주변 지적선 표시 |
| 지도 클릭 | 5px 초과 이동은 드래그로 간주해 무시. 경계 선행 표시 후 콜백 |
| 내 위치 | SDK 버튼이 실패를 알리지 않아 직접 구현. **IP 기반 위치**(`navigator.geolocation` 은 HTTPS/localhost에서만 동작) |

---

## 9. 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` |
| `LLM_BASE` | — | 무료 티어 프리셋명 (`gemini` 등) |
| `LLM_MODEL` | provider별 | 프리셋 사용 시 자동 설정 |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` … | — | provider별 키 |
| `VWORLD_KEY` | — | 미설정 시 mock 모드 |
| `VWORLD_DOMAIN` | `http://localhost:5173` | **인증키 등록 서비스URL**(접속 URL 아님) |
| `APP_TOKEN` | — | `/api/chat` 보호. 외부 노출 시 필수 |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS |
| `VITE_APP_TOKEN` | — | 프론트 전송 토큰(`APP_TOKEN` 과 일치 필요) |
| `VITE_API_BASE` | `""` | 비우면 Vite 프록시 경유 |

**실행**

```bash
# 백엔드
cd backend && set -a && . ../.env && set +a
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 프론트
cd frontend && npm run dev -- --host 0.0.0.0 --port 5173
```

프롬프트·도구 설명 변경 시 **백엔드 재시작 필요**(`--reload` 미사용). 프론트는 Vite dev 서버라
새로고침으로 반영된다.

---

## 10. 검증 사례

| 대상 | 결과 |
|---|---|
| 서울 강남구 테헤란로 152 | 역삼동 737, 13,156.5㎡, 일반상업지역 → 서울 조례 60%/800%(법정 1300% 대비 −500%p) → 건축면적 7,893.9㎡ · 연면적 105,252㎡ · 13층 · 46.2m |
| 충남 예산군 삽교읍 두리 100 | 1,482㎡, 지목 답, 생산녹지지역 → 조건부 가능, 20%/100% |
| 충남 예산군 삽교읍 두리 97 | 6,863㎡ · **걸침 필지** → 자연녹지 52.8%(3,623㎡) / 생산녹지 47.2%(3,240㎡). 최대 면적 부분 기준 판정 + 제84조 안내 + 지도 조각 표시 |
| 충북 음성군 생극면 팔성리 100 | 691.1㎡, 계획관리지역 → **판정 불가**(음성군 조례 미수집, 설계 의도대로 동작) |

---

## 11. 알려진 한계와 미구현 항목

### 11.1 데이터 커버리지

- **비도시 지자체 조례 미수집** — 음성군·경산시·영천시는 주소 별칭에 등록되어 있으나
  `ordinances.json` 에 데이터가 없어 `detect_jurisdiction()` 이 `None` 을 반환하고
  **판정 불가**가 된다(법정 상한 대체 계산을 의도적으로 하지 않음).
- **개발행위허가 기준 미반영** — 비도시지역의 실질적 관문인 진입도로 폭·경사도·표고·입목축적
  기준이 포함되지 않았다.
- **`USE_MATRIX` 는 간이 판정표** — 건축법 시행령 별표1 전체가 아니라 **9개 대분류**만 다룬다.
  이 사실은 답변과 화면 고지에 명시된다.

### 11.2 판정의 성격

- 산출값은 **밀도 규제만 반영한 이론값**이다. 일조권 사선제한, 정북방향 이격거리, 대지 안의 공지,
  도로 후퇴선, 주차대수 산정은 반영되지 않았다.
- 면적은 **지적도 경계의 측지 계산값**이며 토지대장 공부면적과 다를 수 있다(법적으로는 대장 우선).
- 지목 판정은 **절차 필요성 플래그**까지다. 전용 가능 여부는 별도 레이어 조회가 필요하다.
- 걸침 필지 비율은 **용도지역도(1:5,000 축척) 기준 추정치**이며 지적선과 수 m 어긋날 수 있다.
  경계가 판정을 좌우하는 필지는 **토지이용계획확인서의 지형도면 고시**가 최종 기준이다.

### 11.3 3D 표현

- 도심 고층 밀집 지역에서는 기존 3D 건물 모델에 가려 건축 가능 규모가 잘 보이지 않는다.
  (과거 200m 띄우기 방식은 위치는 맞고 높이는 가짜여서 오해를 유발해 제거했다.)
- 건물 형상은 필지 경계를 건폐율만큼 오프셋한 것으로, 실제 배치·형태와 다르다.
- 걸침 필지의 부분별 규모 산정(제84조 가중평균/부분별 적용)은 계산에 미반영이며,
  판정은 최대 면적 부분 기준이다.

### 11.4 운영·기술 부채

| 항목 | 내용 |
|---|---|
| 세션 저장소 | 프로세스 메모리, TTL·용량 제한 없음. 재시작 시 대화 소실. 단일 인스턴스 전제 |
| Python 버전 | 3.9.25에서 동작하나 **3.11+ 권장**. 모든 모듈이 `from __future__ import annotations` 에 의존하며, 애노테이션 외부에서 `X | Y` 를 쓰면 즉시 실패 |
| 진행 이벤트 | `diagnosis_step` 이 실시간이 아니라 진단 완료 후 일괄 방출 |
| 오류 안내 | 프리셋 사용 시 `friendly_error` 가 실제 키 환경변수명(`GEMINI_API_KEY` 등) 대신 `OPENAI_API_KEY` 를 안내 |
| 미사용 코드 | `ordinance.statutory_meta()`, `config.ANTHROPIC_MODEL`, `config.LAYER_ZONING`, `run_prediagnosis(max_turns=)` |
| CLI 결함 | `compare_ordinances.py` 가 사용하는 `ordinance.compare()` 는 비도시지역 미규정 항목에서 `KeyError: 'source'` 발생 |
| FastAPI | `@app.on_event("startup")` 은 deprecated — `lifespan` 으로 이전 필요 |
| 조회 API 오류 처리 | `/api/parcel-at` 등 GET 3종은 `VWorldError` 를 그대로 500으로 노출 |
| 버전 관리 부재 | git 저장소가 아니어서 변경 이력 추적이 불가능하다 |
