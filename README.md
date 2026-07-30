# 공간정보 기반 인허가 사전진단

자연어로 물으면 공간정보 위에서 법령 규제를 검토해 건축 허가 가능성을 판단하고,
VWorld 3D 지도에서 해당 필지로 이동해 건폐율·용적률 범위 안의 가상 건물 매스를 세운다.

## 구조

```
사용자 질의
    ↓
오케스트레이터 (LLM 도구 루프 · Gemini gemini-flash-lite-latest)   backend/app/orchestrator.py
    │
    ├─ ① 사전진단 에이전트        진단 (도구 20개 sub-오케스트레이션)   agents/prediagnosis.py
    │        · LLM 1회(주소·용도 추출) + 결정적 파이프라인
    │        · vworld·zoning·ordinance·setback_rules·site_constraints·
    │          road_access·building_register·land_conversion … (20개)
    │
    ├─ ② 지도제어(2D) 에이전트      2D 지도 명령                       agents/map_control.py
    │        · fly_to(카메라)·highlight_parcel(필지)·
    │          show_zone_pieces(용도지역)·show_panel(결과 패널)
    │
    ├─ ③ 3D(매스) 에이전트          건물 입체·치수선                    agents/map_control.py + lib/mapBridge.ts
    │        · extrude_mass(건축 가능 규모 3D 입체)·show_dimensions(치수선)·
    │          show_housing_model(주택/공장/상가/창고 모델)
    │        · 실제 3D 렌더링은 프론트 mapBridge.ts 가 VWorld 3D(ws3d/Cesium)에서
    │
    └─ ④ 지역추천 에이전트          탐색형 질의                        agents/area_recommender.py
             · "○○ 비도시 지역에서 농막 지을 데 찾아줘" 류
```

② 지도제어(2D)와 ③ 3D(매스)는 **코드상 `map_control.py` 한 모듈**이지만 기능적으로
2D 지도 묘화와 3D 건물 입체(매스)로 나뉜다. 둘 다 **LLM 없이** 진단 결과를 지도 명령으로
번역하고(판단은 이미 끝났으므로), 명령은 SSE로 프론트에 흘러가 `lib/mapBridge.ts` 가
VWorld 3D 위에서 실행한다.

사전진단 에이전트가 순서대로 호출하는 핵심 공간 도구:

| 단계 | 도구 | 소스 |
|---|---|---|
| 주소 → 좌표 | `geocode_address` | `tools/vworld.py` (VWorld 지오코더) |
| 좌표 → 필지 | `get_parcel` | `tools/vworld.py` (연속지적도) |
| 좌표 → 용도지역 | `get_land_use` | `tools/vworld.py` (용도지역지구도) |
| 규제 판정 | `lookup_zoning` | `tools/zoning.py` (국토계획법 시행령·조례) |
| 이격 조회 | `setback_rules` | `tools/setback_rules.py` (119개 지자체 별표) |
| 3D(매스) 산출 | `calc_massing` | `tools/massing.py` |

## 실행

```bash
# 백엔드
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export VWORLD_KEY=...            # 없으면 목 데이터로 동작
uvicorn app.main:app --reload

# 프론트엔드
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

## Docker Compose

실제 인증키는 Git에 올리지 않는 `.env`에만 저장한다.

```bash
cp .env.example .env
# .env에 실제 키와 운영 주소를 입력
docker compose up -d --build
```

기본 접속 주소는 `http://서버주소:5173`이다. 다른 호스트 포트를 쓰려면
`.env`의 `APP_PORT`를 변경한다.

```bash
docker compose ps
docker compose logs -f
docker compose down
```

`compose.yaml`, Dockerfile과 `.env.example`은 Git에 포함한다. 실제 `.env`,
API 키, 향후 데이터베이스 볼륨은 Git과 Docker 이미지에 포함하지 않는다.

전국 산지·임상·생태·DEM 가공 데이터는 별도 tar로 패키징하고, `.env`의
`SPATIAL_DATA_DIR`로 압축을 푼 `processed` 폴더를 지정한다. Compose는 이
폴더를 `/data/processed`에 읽기 전용으로 마운트한다. 생성·체크섬 검증·배포
절차는 [대형 공간데이터 패키징·배포](docs/SPATIAL-DATA-DEPLOYMENT.md)를 따른다.

`VWORLD_KEY` 는 [vworld.kr](https://www.vworld.kr) 에서 발급받고,
개발용으로 `localhost` 를 인증 도메인에 등록해야 한다.

### 운영 배포 (systemd)

운영 서버에서는 백엔드·프론트를 **systemd 서비스**로 띄운다. 코드 수정 후에는
수동으로 `uvicorn`/`node`를 실행하지 말 것 — 포트(8000/5173) 충돌로 서비스가
크래시-재시작 루프에 빠지고, 프론트는 `Requires=` 로 백엔드에 묶여 함께 재시작돼
진행 중인 SSE 채팅이 끊긴다("network error").

```bash
# 프론트: dist 를 서빙 + /api 를 백엔드로 프록시 (frontend/server.mjs)
sudo systemctl restart permit-copilot-backend      # uvicorn, :8000
npm --prefix frontend run build                    # 프론트 변경 시 dist 재빌드
sudo systemctl restart permit-copilot-frontend     # node server.mjs, :5173

systemctl status permit-copilot-backend permit-copilot-frontend
```

백엔드만 고쳤으면 백엔드 서비스만 재시작하면 되고, 프론트(dist)만 고쳤으면
재빌드 후 브라우저 하드 새로고침이면 된다. 공인 IP로 접속할 때 브라우저가
HTTPS를 강제하면(HSTS/HTTPS-First) HTTP 서버라 `ERR_SSL_PROTOCOL_ERROR` 가 날 수
있으니 `http://` 로 접속하거나 크롬의 "항상 보안 연결 사용"을 끈다.

### 운영 환경 (GCP · LLM)

| 구분 | 사양 |
|---|---|
| 클라우드 | Google Cloud Platform, 리전 `asia-northeast3`(서울) |
| 인스턴스 | `e2-standard-8` — 8 vCPU(AMD EPYC 7B12) · 31 GiB RAM · 500 GB 디스크 |
| OS | Rocky Linux 9.8 (kernel 5.14) |
| 실행 | systemd 서비스 2개(backend :8000 / frontend :5173) |
| LLM | **Google Gemini `gemini-flash-lite-latest`** (OpenAI 호환 모드) |

LLM은 `app/llm.py` 의 어댑터로 공급자를 바꿔 붙인다. 운영값은
`LLM_PROVIDER=openai`, `LLM_MODEL=gemini-flash-lite-latest`, `GEMINI_API_KEY` 이며,
OpenAI 호환 `/chat/completions` 엔드포인트(`generativelanguage.googleapis.com`)로
호출한다. LLM은 **자연어 → 구조 변환과 후속 자연어 답변에만** 쓰고, 판정·계산·묘화는
결정적 코드가 하므로 경량 모델로도 동작한다. 산지 SQLite(1.77 GB)를 상시 적재하므로
메모리 여유가 있는 사양을 쓴다.

### 데이터 저장소 (DB 서버 없이 파일 기반)

별도 DBMS(PostgreSQL 등)나 벡터DB 서버를 두지 않고, 파일 기반 저장소로 동작한다.

| 종류 | 구현 | 현행 규모 | 위치 |
|---|---|---|---|
| **벡터 색인**(법령·조례 근거 검색) | numpy TF-IDF 코사인 유사도(외부 임베딩·벡터DB 없음) | **9,333청크(국가 법령 1,748 + 조례 7,585)·어휘 35,194개** | `app/data/ordinance_index.*` |
| **공간 RDB**(산지·임상·생태) | SQLite + RTree, read-only 조회(`local_spatial.py`) | 산지 1,066,806개 · 임상도 3,382,312개 · 생태·자연도 1,599,058개 · 별도관리 24,944개 | `data/processed/*/*.sqlite` |
| **정형 데이터**(조례·규제) | JSON | 건폐율/용적률 200개 관할 · 이격 119개 지자체 · 공간레이어 설정 | `app/data/*.json` |
| **실시간 API**(공간정보) | 외부 조회 + TTL 캐시 | VWorld(지오코딩·필지·용도지역·농업진흥·재해위험지구), 국토부 건축HUB, 국가법령정보센터 | — |

벡터 색인은 임베딩 모델로 교체할 수 있게 `_query_vector()`/`search()`의
벡터화 경계를 분리해 두었다. 다만 교체할 때도 관할·시행일 필터와 정형 수치
판정 분리는 유지해야 한다.
산지·임상·생태 SQLite와 DEM 및 조례 벡터 색인은 대용량·재생성 가능
데이터라 Git·Docker 이미지에 넣지 않는다. 공간데이터는
`scripts/spatial_data_package.py`로 manifest와 별도 tar를 만들고 Compose
읽기 전용 볼륨으로 연결하며, 원천자료에서 재생성할 때는 각 import 스크립트를 쓴다.

법률·조례의 수집, 조문 단위 청킹, TF-IDF 색인, 관할·시점 필터, 정형 수치
판정과 근거 검색의 분리는
[법률·조례 수집 및 청킹 문서](docs/LEGAL-ORDINANCE-INDEX.md)에 상세히 기록한다.

인허가 단계는 `app/data/permit_rules.json`의 조건식을
`tools/permit_requirements.py`가 평가해 생성한다. 각 단계에는 `rule_id`,
법령 참조와 선행단계 `depends_on`이 붙고, Gemini는 계산 결과와 관련
법령·조례 청크를 읽어 설명만 한다.

법률 적용 관계는 `legal_conflict_rules.json`과 `legal_conflicts.py`가 종합
판정 전에 평가한다. 명시적 금지, 예외 입증 필요, 독립 법률 요건의 누적 적용을
구조화하며 Gemini가 우선순위를 임의로 결정하지 않는다.

## 조례 데이터

건폐율·용적률 수치는 코드에 하드코딩하지 않고 조례 데이터에서 읽는다. 세 층위다.

| 층위 | 출처 | 규모 |
|---|---|---|
| 법정 상한 | 국토계획법 시행령 제84·85조 (대통령령 제36220호, 시행 2026-03-24) | 폴백 |
| 검증 조례 | `app/data/ordinances.json` — 서울·부산·인천·성남·대구·아산 등 사람 대조 | **11개 관할** |
| 자동수집 조례 | `app/data/ordinances_auto.json` — 국가법령정보센터에서 도시계획조례 자동 수집 | **196개 관할** |

즉 전국 **약 200개 관할**의 도시계획조례 건폐율/용적률이 실제 적용된다(청주·경산·
강릉 등). 두 파일 모두 런타임에 로드되며, 검증 조례가 자동수집분보다 우선한다.
주소에서 인식된 지자체 조례가 있으면 그 값이, 없으면 법정 상한이 적용되고, 어느
쪽을 썼는지는 판정 결과의 `limit_source` (`ordinance` / `statutory`) 로 확인한다.
자동수집분은 원문 대조 전이므로 표본 검수가 필요하다(손상 관할은
`ordinances_needs_manual.json` 으로 분리).

```bash
python compare_ordinances.py              # 전체 비교표
python compare_ordinances.py 일반상업지역     # 특정 용도지역 상세 (매스까지)
python compare_ordinances.py --gaps        # 법정 대비 격차 큰 순
```

**조례를 반영하지 않으면 규모를 과다 산정한다.** 660㎡ 일반상업지역 기준:

```
법정 상한   80% / 1300%  →  연면적 8,580㎡  16층
서울특별시   60% /  800%  →  연면적 5,280㎡  13층   (-38%)
```

**미규정은 빈칸으로 둔다.** 서울·성남은 전역이 도시지역이라 관리지역·농림지역
조항이 조례에 아예 없다. 이런 항목은 법정 상한으로 폴백하되 조례를 근거로
인용하지 않는다 — 없는 조문을 인용하는 것이 틀린 수치보다 위험하다.

### 이격거리(대지 안의 공지) — 전국 조례 별표

이격거리도 코드에 하드코딩하지 않고 `app/data/setbacks.json` 에서 읽는다. 전국
**119개 지자체**의 건축조례 「대지 안의 공지」 별표를 국가법령정보센터(ELIS)에서
첨부 HWP로 내려받아 표 셀을 추출·파싱한 것이다(수집·파싱 스크립트:
`scripts/collect_setback_tables.py`, `scripts/parse_setbacks_grid.py`).

- `setback_rules.lookup(지자체, 용도, 용도지역, 연면적)` — first-match 규칙 평가로
  전면(건축선)·인접(대지경계) 이격을 돌려준다. 미수집 지자체는 `NOT_COLLECTED`.
- `setback_rules.applicable_setbacks(...)` — 이 필지의 용도지역·연면적 기준으로
  **실제 이격이 발생하는 용도와 수치**를 산출한다(예: 연면적 632㎡ 계획관리지역 →
  공장 전면 3m·인접 1.5m, 창고 전면 3m). 규모 조건 미달 용도는 자동 제외.
- 규칙은 규모(`min_gross`)·용도지역(`zone`/`zone_contains`) 조건을 지원한다.

이격은 고정값이 아니라 `용도 + 연면적 규모 + 용도지역` 조건 조합으로 결정된다.
아산은 검증값이고 나머지 118개는 별표 자동 파싱값(`review_status: auto_parsed`)이라
운영 투입 전 표본 검수가 필요하다. 파생 데이터(`setbacks_parsed.json`,
`setbacks_tables_raw.json`)는 재생성 가능하며, HWP 다운로드 캐시는 Git에 넣지 않는다.

## 알아둘 것

**매스는 이론값이다.** 건폐율을 꽉 채우고 용적률 상한까지 올린 최대 봉투로,
일조권 사선제한, 정북방향 이격, 대지 안의 공지, 주차대수 산정, 지구단위계획
지침이 반영되면 실제 규모는 이보다 작아진다.

**용도 판정표는 간이 버전이다.** `USE_MATRIX` 는 건축법 시행령 별표1 대분류
위주(단독·공동주택, 제1·2종근생, 업무·판매·숙박시설, 공장, 창고시설,
교육연구시설 등)로 다룬다. 세부 용도(예: 일반음식점 vs 휴게음식점, 학교 vs
학원·연구소)는 판정이 갈리므로 계속 확장이 필요하다. 일상어 용도("상가",
"학교", "원룸")는 `_AMBIGUOUS_USE_TERMS`·`_USE_KEYWORDS` 로 정식 용도에 매핑한다.

## OGC WMS/WFS 공간규제 연계

범용 WFS 클라이언트와 필지 중첩 판정 API가 포함되어 있다.

- `GET /api/spatial-layers`: 등록 레이어와 연결 준비 상태
- `POST /api/spatial-overlaps`: 필지와 규제구역의 교차 면적·비율
- 레이어 설정: `backend/app/data/spatial_layers.json`

현재 자동 판정 연결 상태:

- 농업진흥지역: VWorld WFS 실시간 조회
- 산지구분: 전국 원본(폴리곤 106만 개, SQLite 1.77 GB)을 로컬 RTree로 실시간 조회
- 1:5,000 임상도: 전국 338만 폴리곤을 로컬 RTree로 조회. 파일 수집일과
  구역별 실제 갱신연도(2017~2025)를 구분하고 산림조사서를 대체하지 않음
- 건축물대장: 국토부 건축HUB API(`getBrTitleInfo`)로 전국 표제부 실시간 조회
- 도로 접도: 연속지적도에서 지목 `도로`인 인접 필지를 찾아 사전검토
- 재해위험지구: VWorld WFS `lt_c_up201` 전국 실시간 중첩 조회. WMS는 지도
  표시용이며 판정은 WFS 벡터의 교차 면적·비율을 사용
- 생태·자연도: 국립생태원 2026 정기고시 FileGDB를 로컬 SQLite RTree로 변환해
  1~3등급과 별도관리지역의 필지별 중첩 면적·비율을 조회
- 국가유산: 토지이용 용도지구에서 확인된 보호구역을 1차 스크리닝하며,
  국가유산청 정밀 공간정보는 별도 연계 필요

개념 건축 가능 영역은 건폐율만 적용하지 않고 다음 보수 시나리오를 함께
계산한다.

- 용도별 부설주차장 기본대수와 지상주차 필요면적
- 대지 안의 공지(이격): 지자체 건축조례 별표에서 읽은 실제 값(`setbacks.json`).
  미수집 지자체는 0m로 두고 그 사유를 함께 표시한다
- 전용·일반주거지역의 정북방향 일조 이격
- 제약 반영 전후 건축면적과 축소율

결과 폴리곤은 필지 내부에서만 생성한다. 지하·기계식 주차, 정확한 건축선,
정북측 인접대지경계와 지자체 조례가 확인되면 다시 산정해야 하는 개념
배치이며 허가도면을 대체하지 않는다.

농업진흥지역과 산지구분은 등록되어 있으나 공식 서비스 URL과 레이어명을
확인하기 전에는 `NOT_CONFIGURED`로 반환된다. 외부 조회 실패를 규제 없음으로
처리하지 않는다. 자세한 설정은 `docs/spatial-ogc.md`를 참고한다.

## 검증이 필요한 지점

- **조례 원문 대조.** 조례 5건은 ELIS 원문 HTML을 텍스트로 변환해 읽은 값이다.
  운영 투입 전 최소한 서울 제48조와 부산 제50조는 사람이 원문과 대조할 것.
  (조사 과정에서 WebFetch 요약 모델이 같은 URL에 대해 준공업지역 용적률을
  400%와 200%로 다르게 반환한 사례가 있어, 이후 전부 원문 파싱으로 수집했다.)
- **부산은 자치구별 조례가 따로 있다.** 영도구·동래구·금정구·사상구·기장군에서
  별도 도시계획조례가 확인됐다. 부산 물건에 시 조례값을 그대로 쓰면 틀릴 수 있다.
  자치구 수치는 미수집이다.
- **조건부 수치는 단일값으로 자동 적용하면 안 된다.** 예: 부산 제2종일반주거지역
  "220% 이하(단, 대지면적 1천㎡ 초과 시 200% 이하)", 서울 일반상업지역
  "800%(단, 서울도심 600%)". 현재는 넓은 값을 쓰고 단서를 `note` 에 남긴다.


- `tools/vworld.py` 의 레이어 ID(`LP_PA_CBND_BUBUN`, `LT_C_UQ111`)와 응답 필드명
  (`lndpcl_ar`, `dgm_nm` 등)은 VWorld 데이터 API 문서와 대조해 확인할 것.
  목 모드에서는 이 경로를 타지 않는다.
- `components/MapCanvas.tsx` 의 Cesium 뷰어 핸들 획득
  (`window.vw.ws3dMap`) 은 VWorld 3D SDK 버전에 따라 다를 수 있다.
  브라우저 콘솔에서 `window.vw` 를 열어 실제 핸들 경로를 확인할 것.
