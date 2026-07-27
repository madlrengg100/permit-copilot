# WMS/WFS 공간규제 연계

백엔드는 등록된 OGC 레이어만 조회한다. 사용자가 요청에 임의 URL을 넣을 수
없으므로 외부 요청을 악용한 SSRF를 방지한다.

## 구성

- 레이어 등록부: `backend/app/data/spatial_layers.json`
- WFS/WMS 클라이언트: `backend/app/tools/ogc.py`
- 레이어 상태: `GET /api/spatial-layers`
- 필지 중첩 판정: `POST /api/spatial-overlaps`

## 레이어 활성화

1. 제공기관의 `GetCapabilities`에서 WFS/WMS 주소와 레이어명을 확인한다.
2. `.env`에 주소와 레이어명을 입력한다.
3. `spatial_layers.json`에서 해당 레이어의 `enabled`를 `true`로 바꾼다.
4. 백엔드를 재시작하고 `/api/spatial-layers`의 `wfs_ready`를 확인한다.

HTTP 엔드포인트가 불가피한 공공기관은 `.env`에 `OGC_ALLOW_HTTP=true`를
설정할 수 있지만 HTTPS가 우선이다.

## 중첩 요청 예시

```json
{
  "parcel_geometry": {
    "type": "Polygon",
    "coordinates": [[[127.0, 37.0], [127.1, 37.0], [127.1, 37.1],
                     [127.0, 37.1], [127.0, 37.0]]]
  },
  "layer_ids": ["agricultural_promotion", "forest_class"]
}
```

상태값은 다음과 같다.

- `OVERLAP`: 규제구역과 교차함
- `CLEAR`: 정상 조회됐으며 교차하지 않음
- `NOT_CONFIGURED`: 주소·레이어명이 아직 설정되지 않음
- `UNAVAILABLE`: 외부 서비스 오류, 형식 오류 또는 조회 상한 초과

`UNAVAILABLE`과 `NOT_CONFIGURED`를 규제 없음으로 해석해서는 안 된다.

## 전국 산지구분 데이터

산지구분은 브이월드 데이터셋 `30362`
`(연속주제)_산지관리/보전준보전산지`의 시도별 SHP를 사용한다.

1. 브이월드에 로그인한다.
2. 데이터셋 페이지에서 최신 `LSMD_CONT_UF801_5174_*.zip`을 선택한다.
3. ZIP을 다음 폴더에 복사한다.

```text
backend/data/source/forest/
```

4. 전국 RTree 공간DB를 생성한다.

```bash
cd backend
./.venv/bin/python scripts/import_forest_shp.py \
  --source data/source/forest \
  --output data/processed/forest/forest_class.sqlite \
  --source-date 2026-07
```

변환은 임시 DB에 적재를 완료한 후 운영 DB를 교체하므로 중간 실패 시 기존
데이터가 손상되지 않는다. 원본과 가공 DB는 Git에 포함하지 않는다.

2026년 7월 자료에는 행정구역 개편 전후 파일이 함께 보일 수 있다. 같은 지역의
중복 파일을 동시에 적재하지 말고 최신 행정구역 파일만 선택한다.
