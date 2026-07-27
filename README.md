# 공간정보 기반 인허가 사전진단

자연어로 물으면 공간정보 위에서 법령 규제를 검토해 건축 허가 가능성을 판단하고,
VWorld 3D 지도에서 해당 필지로 이동해 건폐율·용적률 범위 안의 가상 건물 매스를 세운다.

## 구조

```
사용자 질의
    ↓
오케스트레이터 (Claude Opus 4.8 · tool-use 루프)   backend/app/orchestrator.py
    ├─ prediagnose      → 사전진단 에이전트          backend/app/agents/prediagnosis.py
    ├─ render_on_map    → 지도제어 에이전트          backend/app/agents/map_control.py
    └─ restudy_massing  → 매스 재산출 (후속 질의용)
```

사전진단 에이전트는 자체 tool-use 루프를 돌며 공간 도구를 순서대로 호출한다.

| 단계 | 도구 | 소스 |
|---|---|---|
| 주소 → 좌표 | `geocode_address` | `tools/vworld.py` (VWorld 지오코더) |
| 좌표 → 필지 | `get_parcel` | `tools/vworld.py` (연속지적도) |
| 좌표 → 용도지역 | `get_land_use` | `tools/vworld.py` (용도지역지구도) |
| 규제 판정 | `lookup_zoning` | `tools/zoning.py` (국토계획법 시행령) |
| 매스 산출 | `calc_massing` | `tools/massing.py` |

지도제어 에이전트는 LLM 없이 진단 결과를 지도 명령으로 번역한다 —
판단은 이미 끝났고, 무엇을 그릴지는 확정적이기 때문이다. 명령은 SSE 로 프론트에
흘러가 `lib/mapBridge.ts` 가 VWorld 3D 위에서 실행한다.

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

`VWORLD_KEY` 는 [vworld.kr](https://www.vworld.kr) 에서 발급받고,
개발용으로 `localhost` 를 인증 도메인에 등록해야 한다.

## 조례 데이터

건폐율·용적률 수치는 코드에 하드코딩하지 않고 `app/data/ordinances.json` 에서
읽는다. 이 파일은 두 층위를 담는다.

| 층위 | 출처 | 시점 |
|---|---|---|
| 법정 상한 | 국토계획법 시행령 제84·85조 (대통령령 제36220호) | 시행 2026-03-24 |
| 서울특별시 | 도시계획조례 제44·48조 | 시행 2026-07-13 |
| 부산광역시 | 도시계획조례 제49·50조 | 시행 2026-04-08 |
| 인천광역시 | 도시계획조례 제64·65조 | 시행 2026-07-13 |
| 경기도 성남시 | 도시계획조례 제66·67조 | 시행 2026-02-24 |
| 대구광역시 | 도시계획조례 제75·80조 | 시행 2026-05-11 |

주소에서 지자체가 인식되면 조례값이, 아니면 법정 상한이 적용된다. 어느 쪽을
썼는지는 판정 결과의 `limit_source` (`ordinance` / `statutory`) 로 확인한다.

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

## 알아둘 것

**매스는 이론값이다.** 건폐율을 꽉 채우고 용적률 상한까지 올린 최대 봉투로,
일조권 사선제한, 정북방향 이격, 대지 안의 공지, 주차대수 산정, 지구단위계획
지침이 반영되면 실제 규모는 이보다 작아진다.

**용도 판정표는 간이 버전이다.** `USE_MATRIX` 는 건축법 시행령 별표1 대분류
9종만 다룬다. 세부 용도(예: 일반음식점 vs 휴게음식점)는 판정이 갈리므로
확장이 필요하다.

## OGC WMS/WFS 공간규제 연계

범용 WFS 클라이언트와 필지 중첩 판정 API가 포함되어 있다.

- `GET /api/spatial-layers`: 등록 레이어와 연결 준비 상태
- `POST /api/spatial-overlaps`: 필지와 규제구역의 교차 면적·비율
- 레이어 설정: `backend/app/data/spatial_layers.json`

현재 자동 판정 연결 상태:

- 농업진흥지역: VWorld WFS 실시간 조회
- 산지구분: 전국 원본을 적재한 로컬 SQLite RTree 조회
- 도로 접도: 연속지적도에서 지목 `도로`인 인접 필지를 찾아 사전검토
- 재해위험지구: 공공데이터포털에는 서비스가 있으나 정확한 전용 WFS
  식별자/엔드포인트 미확보로 비활성화. 다른 용도지구가 섞인 VWorld 레이어를
  재해 레이어로 오인하지 않도록 `NOT_CONFIGURED`로 처리
- 생태·자연도: 별도 공공데이터포털 서비스 활용신청 전까지 미연계로 표시
- 국가유산: 토지이용 용도지구에서 확인된 보호구역을 1차 스크리닝하며,
  국가유산청 정밀 공간정보는 별도 연계 필요

개념 건축 가능 영역은 건폐율만 적용하지 않고 다음 보수 시나리오를 함께
계산한다.

- 용도별 부설주차장 기본대수와 지상주차 필요면적
- 대지 안의 공지 1m 가정(실제 값은 지자체 건축조례 확인)
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
