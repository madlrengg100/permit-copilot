# 대형 공간데이터 패키징·배포

전국 산지·임상·생태·DEM 데이터는 Git 저장소와 Docker 이미지에 넣지 않는다.
가공 결과만 별도 tar로 배포하고, Docker Compose는 압축을 푼 `processed` 폴더를
백엔드 컨테이너의 `/data/processed`에 읽기 전용으로 연결한다.

## 패키지 구성

패키지에는 런타임에 필요한 다음 파일과 SHA-256 manifest만 포함한다.
원본 ZIP, SQLite의 `-wal`·`-shm`, 변환 중간 파일은 제외한다.

- `forest/forest_class.sqlite`
- `forest_inventory/forest_inventory.sqlite`
- `ecological_nature_map/ecological_nature.sqlite`
- `ecological_nature_map/separate_management.sqlite`
- `terrain/dem/cop30_korea.tif`

## 생성

저장소 루트에서 실행한다. `.tar`는 빠르게 묶을 때, `.tar.gz`는 전송량을
줄일 때 사용한다. tar와 배포용 manifest는 `artifacts/`에 생성되어 Git에서 제외되고,
검증된 manifest 사본은 `backend/data/spatial-data-manifest.json`으로 Git에 기록한다.

```bash
python backend/scripts/spatial_data_package.py pack \
  --source backend/data/processed \
  --output artifacts/permit-copilot-spatial-data-2026-07.tar \
  --manifest artifacts/spatial-data-manifest.json \
  --version 2026-07
```

manifest만 다시 만들 수도 있다.

```bash
python backend/scripts/spatial_data_package.py manifest \
  --source backend/data/processed \
  --output backend/data/spatial-data-manifest.json \
  --version 2026-07
```

## 서버 배치와 검증

```bash
python backend/scripts/spatial_data_package.py extract \
  --package artifacts/permit-copilot-spatial-data-2026-07.tar \
  --destination /srv/permit-copilot-data
```

해제 과정에서 경로 이탈과 심볼릭 링크를 차단하고 모든 파일의 크기와
SHA-256을 검증한다. 이미 해제한 데이터는 다음과 같이 재검증한다.

```bash
python backend/scripts/spatial_data_package.py verify \
  --root /srv/permit-copilot-data \
  --manifest /srv/permit-copilot-data/spatial-data-manifest.json
```

`.env`에는 패키지의 `processed` 폴더를 지정한다.

```dotenv
SPATIAL_DATA_DIR=/srv/permit-copilot-data/processed
```

그다음 `docker compose up -d --build`를 실행한다. Compose가 다음 런타임
경로를 자동으로 백엔드에 전달한다.

| 데이터 | 컨테이너 경로 |
|---|---|
| 산지구분 | `/data/processed/forest/forest_class.sqlite` |
| 임상도 | `/data/processed/forest_inventory/forest_inventory.sqlite` |
| 생태·자연도 | `/data/processed/ecological_nature_map/ecological_nature.sqlite` |
| 별도관리지역 | `/data/processed/ecological_nature_map/separate_management.sqlite` |
| DEM | `/data/processed/terrain/dem/cop30_korea.tif` |

데이터 갱신은 새 버전 폴더에 먼저 풀고 검증한 뒤 `SPATIAL_DATA_DIR`만
바꾸어 재기동한다. 이전 버전 폴더를 남기면 즉시 롤백할 수 있다.
