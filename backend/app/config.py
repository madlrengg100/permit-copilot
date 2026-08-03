import os
from urllib.parse import unquote

# --- LLM ---
#
# provider 는 환경변수로 고른다. 에이전트 코드는 app/llm.py 어댑터를 통해
# 두 provider 를 동일하게 다루므로, 여기만 바꾸면 전환된다.
#   LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY 필요
#   LLM_PROVIDER=openai     OPENAI_API_KEY 필요
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5",
}
LLM_MODEL = os.getenv("LLM_MODEL", _DEFAULT_MODEL.get(LLM_PROVIDER, "claude-opus-4-8"))

# 하위 호환 (기존 import 자리)
ANTHROPIC_MODEL = LLM_MODEL

# OpenAI 호환 엔드포인트.
#
# Groq / Cerebras / OpenRouter / Gemini 는 모두 OpenAI 형식의 /chat/completions
# 를 제공하므로, base_url 과 키만 바꾸면 provider="openai" 그대로 붙는다.
# 아래는 무료 티어 프리셋 — LLM_BASE 에 이름을 넣으면 자동 적용된다.
FREE_TIER_PRESETS = {
    # 무료 한도가 가장 넉넉하다(일 1,500건). 신용카드 불필요.
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        # 버전 고정 ID(gemini-2.5-flash 등)는 신규 사용자에게 차단된 경우가 있다.
        # -latest 별칭은 계속 사용 가능한 모델을 가리킨다.
        "model": "gemini-flash-latest",
        "key_env": "GEMINI_API_KEY",
        "signup": "https://aistudio.google.com/apikey",
    },
    # 매우 빠름. 일 1,000건. 신용카드 불필요.
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "signup": "https://console.groq.com/keys",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
        "key_env": "CEREBRAS_API_KEY",
        "signup": "https://cloud.cerebras.ai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_env": "OPENROUTER_API_KEY",
        "signup": "https://openrouter.ai/keys",
    },
}

LLM_BASE_NAME = os.getenv("LLM_BASE", "").strip().lower()
_preset = FREE_TIER_PRESETS.get(LLM_BASE_NAME)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "") or (_preset["base_url"] if _preset else "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "") or (
    os.getenv(_preset["key_env"], "") if _preset else ""
)
# 프리셋을 쓰면서 LLM_MODEL 을 지정하지 않았으면 프리셋 모델을 쓴다
if _preset and not os.getenv("LLM_MODEL"):
    LLM_MODEL = _preset["model"]

# 검토 의견처럼 여러 규제 데이터를 읽어 인과·해결방법으로 엮는 '판독·추론' 호출만
# 한 단계 위 모델을 쓴다(라우팅·추출·분류 같은 값싼 호출은 LLM_MODEL 유지 → 비용·지연 최소).
# gemini 무료 티어에선 flash-lite → flash(gemini-flash-latest, 역시 무료)로 올린다.
# 다른 provider 는 LLM_MODEL 그대로. env(LLM_MODEL_HEAVY)로 재정의 가능.
LLM_MODEL_HEAVY = os.getenv("LLM_MODEL_HEAVY", "").strip() or (
    "gemini-flash-latest" if LLM_BASE_NAME == "gemini" else LLM_MODEL
)

# --- VWorld ---
# https://www.vworld.kr 에서 발급. 도메인 등록 필요(로컬은 localhost).
VWORLD_KEY = os.getenv("VWORLD_KEY", "")
VWORLD_BASE = "https://api.vworld.kr/req"

# 인증키 신청 시 등록한 '서비스URL'.
#
# 2D데이터 API(service=data)는 등록 도메인을 검증한다. 브라우저 호출은 Referer 로
# 자동 확인되지만, 백엔드에서 호출하면 Referer 가 없어 INCORRECT_KEY 로 거부된다.
# 이 값을 domain 파라미터로 함께 보내면 통과한다. (지오코더는 검증하지 않는다)
VWORLD_DOMAIN = os.getenv("VWORLD_DOMAIN", "http://localhost:5173")

# 키가 없으면 목(mock) 응답으로 동작 — API 키 없이도 전체 로직 검증 가능
USE_MOCK = not VWORLD_KEY

# 공공데이터포털은 Encoding/Decoding 키를 모두 표시한다. 환경변수에는 어느
# 형식이 들어와도 HTTP 클라이언트가 사용할 원문 키로 한 번만 정규화한다.
DATA_GO_KR_SERVICE_KEY = unquote(os.getenv("DATA_GO_KR_SERVICE_KEY", ""))
LAW_OPEN_API_OC = os.getenv("LAW_OPEN_API_OC", "")
# 행안부 juso.go.kr 도로명주소 검색 승인키. 건축물대장이 토지 필지 PNU가 아닌
# 건물 대표지번에 등록된 경우(대단지·구축 아파트 등) 주소로 정확한 지번을 얻는다.
JUSO_CONFM_KEY = os.getenv("JUSO_CONFM_KEY", "")

# VWorld 데이터 레이어 ID
LAYER_PARCEL = "LP_PA_CBND_BUBUN"   # 연속지적도(부번)
# 용도지역 레이어는 국토계획법의 4개 대분류별로 분리되어 있다. 일부만 조회하면
# 나머지 대분류 지역이 통째로 "용도지역 정보 없음"이 된다. 전부 조회해서
# 나오는 쪽을 쓴다. (UQ112 가 비도시지역 전체라고 오해했다가 농림지역 필지가
# 조회 실패하던 전례가 있다 — UQ112 는 관리지역만 담는다.)
#   LT_C_UQ111  도시지역   (주거/상업/공업/녹지)
#   LT_C_UQ112  관리지역   (보전관리/생산관리/계획관리)
#   LT_C_UQ113  농림지역
#   LT_C_UQ114  자연환경보전지역
LAYER_ZONING_URBAN = "LT_C_UQ111"
LAYER_ZONING_NONURBAN = "LT_C_UQ112"
LAYER_ZONING_AGRI = "LT_C_UQ113"
LAYER_ZONING_NATURE = "LT_C_UQ114"
LAYERS_ZONING = (
    LAYER_ZONING_URBAN,
    LAYER_ZONING_NONURBAN,
    LAYER_ZONING_AGRI,
    LAYER_ZONING_NATURE,
)
LAYER_ZONING = LAYER_ZONING_URBAN   # 하위 호환

# --- 토지이용계획(토지이음) ---
# 공공데이터포털 국토교통부 '토지이용계획정보 서비스'. 필지(PNU) 하나로
# 용도지역·용도지구·지구단위계획 등 '지역지구 등 지정여부' 전체를 조회한다.
# 키는 data.go.kr 에서 발급(활용신청 → 서비스키). 없으면 조회를 건너뛴다.
LANDUSE_KEY = os.getenv("LANDUSE_KEY", "").strip()
# 엔드포인트는 서비스 버전에 따라 다를 수 있어 환경변수로도 바꿀 수 있게 둔다.
LANDUSE_BASE = os.getenv(
    "LANDUSE_BASE",
    "https://apis.data.go.kr/1611000/nsdi/LandUseService/attr/getLandUseAttr",
)

# 층고 가정 (연면적 -> 층수 환산 및 3D 매스 높이)
FLOOR_HEIGHT_M = 3.3
