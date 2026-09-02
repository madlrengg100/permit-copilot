# 서버 이관 절차

새 서버로 옮길 때 이 문서만 따라가면 된다. 2026-08-24 이관에서 실제로 막혔던
지점을 함정으로 표시해 두었다.

현재 구성 기준: GCP VM · Rocky/RHEL 9 · systemd 직접 실행(Docker 아님).

## 0. 구성 한눈에

| 항목 | 값 |
|---|---|
| 백엔드 | uvicorn, **127.0.0.1:8000** (외부 비공개) |
| 프런트 | node `server.mjs`, **0.0.0.0:5173** (사용자 접속점) |
| 설정 원본 | **`/etc/permit-copilot/permit-copilot.env`** (root:600) |
| 설정 사본 | `<repo>/.env` (docker compose 용, git 제외) |
| 서비스 | `permit-copilot-backend.service` / `permit-copilot-frontend.service` |
| 세션 스냅샷 | `~/.permit-copilot-sessions` |

`/api` 는 프런트가 백엔드로 프록시한다. **열어야 할 포트는 5173 하나뿐이다.**

> ⚠️ **함정 — 설정 파일이 두 개다.** 실행 중 서비스가 읽는 것은 `/etc/...` 쪽이고
> `<repo>/.env` 는 compose 용 사본이다. 한쪽만 고치면 조용히 어긋난다(실제로 겪었다).
> ```bash
> diff <(sudo cat /etc/permit-copilot/permit-copilot.env) <repo>/.env
> ```

## 1. 코드·런타임

```bash
git clone git@github.com:madlrengg100/permit-copilot.git
cd permit-copilot/backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cd ../frontend && npm ci
```

`requirements-dev.txt` 에 pytest 가 있다. 운영 이미지(Dockerfile)는 `requirements.txt` 만 설치한다.

## 2. 인증키 — 전부 새 서버 IP 를 등록해야 한다

새 서버의 **공인 IP** 를 먼저 확인한다.

```bash
curl -s https://api.ipify.org
```

| 키 | 발급처 | 필수 | 함정 |
|---|---|---|---|
| `VWORLD_KEY` | vworld.kr | ✅ | 아래 참조 |
| `GEMINI_API_KEY` | aistudio.google.com | ✅ | `LLM_PROVIDER=openai` 여야 먹는다 |
| `DATA_GO_KR_SERVICE_KEY` | data.go.kr 건축HUB | ✅ | Encoding/Decoding 아무거나 |
| `LAW_OPEN_API_OC` | open.law.go.kr | ✅ | 아래 참조 |
| `JUSO_CONFM_KEY` | business.juso.go.kr | ⬜ | 없으면 건축물대장 폴백만 비활성 |

### VWorld — `VWORLD_DOMAIN` 은 '등록한 서비스 URL' 과 글자까지 같아야 한다

백엔드는 Referer 가 없어 `domain` 파라미터로 검증받는다. 등록값과 다르면
`INCORRECT_KEY` 가 난다. **간헐적으로 성공하기도 해서 원인을 오해하기 쉽다.**
반드시 여러 번 호출해 성공률로 판단한다.

```bash
K=<VWORLD_KEY>; D=<VWORLD_DOMAIN 후보>
for i in 1 2 3 4 5; do curl -s -G "https://api.vworld.kr/req/data" \
  --data-urlencode "service=data" --data-urlencode "request=GetFeature" \
  --data-urlencode "data=LP_PA_CBND_BUBUN" --data-urlencode "key=$K" \
  --data-urlencode "domain=$D" --data-urlencode "format=json" \
  --data-urlencode "size=1" --data-urlencode "geomFilter=POINT(127.489 36.628)" \
  | grep -o '"status" : "[A-Z]*"'; done
```

5/5 OK 여야 한다. 지도 타일은 브라우저 Referer 로 따로 검증되므로 **지도가 떠도
백엔드가 거부당할 수 있다** — 둘은 별개 경로다.

### 지도가 안 뜬다 — 키를 의심하기 전에 WebGL 부터 본다

화면에 이 문구가 뜨면 **서버·키 문제가 아니다.**

```
지도를 준비하지 못했습니다.
Cannot read properties of undefined (reading 'scene')
```

VWorld 엔진이 Cesium 위젯 생성 실패를 삼킨 뒤 `undefined.scene` 을 읽고 죽는
것이라, 화면 문구가 진짜 원인을 가린다. 콘솔을 위로 한 줄만 올리면
`RuntimeError: The browser supports WebGL, but initialization failed` 가 있다.

브라우저 콘솔에서 확인한다.

```js
document.createElement('canvas').getContext('webgl2') ??
document.createElement('canvas').getContext('webgl')
```

`null` 이면 WebGL 이 없는 것이다. 조치 순서는 화면 안내와 같다 —
그래픽 가속 켜고 재시작 → `chrome://gpu` 확인 →
`chrome://flags/#ignore-gpu-blocklist` → VDI·원격데스크톱이면 로컬 PC 에서 접속.

서버 쪽을 굳이 확인하려면 위 `VWORLD_DOMAIN` 절의 5회 호출로 충분하다.
그게 5/5 OK 인데 지도만 안 뜬다면 원인은 브라우저다.

**주의:** 초기화 전에 WebGL 프로브 컨텍스트를 만드는 식으로 "미리 검사"하면
안 된다. 그 컨텍스트가 살아 있는 동안 Cesium 이 자기 컨텍스트를 못 얻어서
멀쩡한 브라우저에서도 지도가 죽는다. 검사는 실패한 뒤에만 한다.

### 국가법령정보센터 — OC 는 이메일 앞부분이 아니다

`LAW_OPEN_API_OC` 는 **OPEN API 신청 시 직접 지정한 활용 ID** 다(마이페이지 →
API인증키관리에서 확인). 가입 이메일과 무관할 수 있다. 서버 IP 등록도 필요하다.

```bash
curl -s -G "https://www.law.go.kr/DRF/lawSearch.do" \
  --data-urlencode "OC=<OC>" --data-urlencode "target=law" \
  --data-urlencode "type=JSON" --data-urlencode "search=1" \
  --data-urlencode "query=건축법" | head -c 200
```

`"법령명한글":"건축법"` 이 나오면 성공. `OC=test`(공개 데모)로 바꿔 성공하면
네트워크·요청형식은 정상이고 **계정 문제로 좁혀진다.**

### LLM — provider 와 preset 조합

`LLM_BASE=gemini` 프리셋은 **`LLM_PROVIDER=openai`** 일 때만 `OPENAI_BASE_URL`·
`OPENAI_API_KEY` 로 풀린다(`config.py` FREE_TIER_PRESETS). `anthropic` 이면 빈
`ANTHROPIC_API_KEY` 로 Anthropic 을 호출해 실패한다.

> ⚠️ **함정 — 모델이 살아 있는지 반드시 확인한다.** 검토 의견은 `LLM_MODEL_HEAVY`
> (gemini 기본값 `gemini-flash-latest`)로 호출하는데, 무료 티어에서 이 모델이 **응답을
> 아예 안 주는 시기**가 있다. 2026-08-24 에는 짧은 프롬프트에도 90초 무응답이었고
> `gemini-flash-lite-latest` 는 1.8초였다. 그 상태면 검토 의견이 매번 14초 타임아웃 →
> 결정적 fallback 으로 떨어져, LLM 이 쓴 문장을 사용자가 거의 못 본다(실측 8건 중 7건).
> 프롬프트 길이 문제가 아니므로 프롬프트를 줄여도 소용없다.
>
> ```bash
> for m in gemini-flash-latest gemini-flash-lite-latest; do
>   printf "%-26s " "$m"
>   curl -s -m 45 -w "%{http_code} %{time_total}s\n" -o /dev/null -X POST \
>     https://generativelanguage.googleapis.com/v1beta/openai/chat/completions \
>     -H "Authorization: Bearer $GEMINI_API_KEY" -H "Content-Type: application/json" \
>     -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"안녕\"}],\"max_tokens\":100}"
> done
> ```
>
> `HTTP 000` 이 나오면 그 모델은 못 쓴다. 동작하는 모델을 `LLM_MODEL_HEAVY` 에 지정한다.
> 배포 후에는 로그로 재확인한다 — `journalctl -u permit-copilot-backend | grep 'judgment timeout'`

## 3. 공간데이터 (약 23.9 GB)

`docs/SPATIAL-DATA-DEPLOYMENT.md` 참조. 요약하면 tar 를 옮겨 푸는 게 가장 빠르다.

```bash
python backend/scripts/spatial_data_package.py pack \
  --source backend/data/processed --output artifacts/spatial-data.tar --version <ver>   # 구서버
python backend/scripts/spatial_data_package.py extract \
  --package artifacts/spatial-data.tar --destination backend/data                        # 신서버
```

`--destination` 은 `processed` 의 **부모**다. 해제하면서 SHA-256 을 전부 대조한다.

원본에서 다시 만들려면 `artifacts/README.md` 와 `backend/data/source/*/WHAT-GOES-HERE.md`
를 따른다. 임상도 변환만 25 분 이상 걸린다.

> ⚠️ **함정 — SQLite 메타데이터를 손대면 manifest 가 어긋난다.** `source_date` 를
> 고치는 것만으로도 해시가 바뀐다. 데이터 수정 후에는 manifest 를 다시 만든다.

## 4. env 작성

`.env.example` 을 복사해 채운 뒤 `/etc/permit-copilot/permit-copilot.env` 로 둔다.
아래 6개는 `.env.example` 에 없지만 **systemd 실행에는 반드시 필요하다.**

```dotenv
SESSION_STATE_DIR=<repo>/../.permit-copilot-sessions
FOREST_SQLITE_PATH=<repo>/backend/data/processed/forest/forest_class.sqlite
FOREST_INVENTORY_SQLITE_PATH=<repo>/backend/data/processed/forest_inventory/forest_inventory.sqlite
ECOLOGICAL_NATURE_SQLITE_PATH=<repo>/backend/data/processed/ecological_nature_map/ecological_nature.sqlite
ECOLOGICAL_SEPARATE_SQLITE_PATH=<repo>/backend/data/processed/ecological_nature_map/separate_management.sqlite
TERRAIN_DEM_PATH=<repo>/backend/data/processed/terrain/dem/cop30_korea.tif
```

> ⚠️ **함정 — Docker 에서는 가려지는 버그다.** compose 가 이 값들을 넘겨주므로
> Docker 로 돌리면 문제가 안 보인다. systemd 직접 실행에서는 코드 기본값으로 떨어지는데,
> 기본값이 과거 서버 계정 경로면 조용히 실패한다(2026-08-24 에 6곳 발견). 새 서버 경로가
> 코드 기본값과 다르면 이 env 를 반드시 넣는다.

```bash
sudo install -d -m 700 /etc/permit-copilot
sudo install -m 600 /dev/stdin /etc/permit-copilot/permit-copilot.env < <채운 env>
sudo cp /etc/permit-copilot/permit-copilot.env <repo>/.env
sudo chown $USER:$USER <repo>/.env && chmod 600 <repo>/.env
```

## 5. APP_TOKEN 과 프런트 빌드

`/api/chat` 은 LLM 을 호출하므로 공개 IP 에 열어두면 누구나 토큰(=비용)을 태울 수 있다.

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"   # APP_TOKEN 에 기입
cd frontend && VITE_APP_TOKEN="<같은 값>" npm run build
```

> ⚠️ **함정 — 프런트는 컴파일 타임에 토큰이 박힌다.** `APP_TOKEN` 만 바꾸고 재빌드하지
> 않으면 **모든 요청이 401** 이 된다. 백엔드 env 를 바꿀 때마다 프런트도 다시 빌드한다.
> 번들에 박히므로 페이지를 연 사람은 값을 꺼낼 수 있다 — 자동화된 무단 호출 차단용이다.

## 6. systemd

`deploy/*.service` 는 현재 서버 기준으로 맞춰져 있다. 새 서버에서는
`User`·`Group`·`WorkingDirectory`·`ExecStart` 경로와 node 실행 경로(`which node`)를 고친다.

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now permit-copilot-backend permit-copilot-frontend
```

## 7. GitHub 푸시 권한

토큰은 대화·이력에 남으므로 SSH 키를 권한다.

```bash
ssh-keygen -t ed25519 -N "" -C "permit-copilot@<IP>" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub      # → 저장소 Settings > Deploy keys, "Allow write access" 체크
git remote set-url origin git@github.com:madlrengg100/permit-copilot.git
ssh -T git@github.com          # "successfully authenticated" 확인
```

> ⚠️ 등록 후 GitHub 에 표시되는 지문이 `ssh-keygen -lf ~/.ssh/id_ed25519.pub` 와
> 같은지 확인한다. 다르면 다른 키가 등록된 것이다.

## 8. 검증 체크리스트

```bash
systemctl is-active permit-copilot-backend permit-copilot-frontend
curl -s http://127.0.0.1:8000/api/config                  # mock_mode:false, llm_provider 확인
curl -s http://127.0.0.1:8000/api/spatial-layers          # 6개 레이어 전부 wfs_ready:true
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' -d '{"session_id":"t","message":"안녕"}'   # 401 이어야 정상
cd backend && .venv/bin/python -m pytest tests/ -q        # 전건 통과
```

마지막으로 실제 필지 진단을 한 번 돌려 아래가 모두 채워지는지 본다.

- 산지구분·임상도·생태자연도 중첩 (공간 SQLite)
- 표고·경사도 (DEM)
- 기존 건축물대장 (공공데이터포털)
- 현행 법령 검증 목록 (국가법령정보센터)

검증용 필지: **세종특별자치시 부강면 등곡리 산 115-1**(임야·보전산지·경사도),
**경기도 양평군 양평읍 양근리 638**(용도지역 걸침·수질보전특별대책지역).
