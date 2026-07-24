import os

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

# 층고 가정 (연면적 -> 층수 환산 및 3D 매스 높이)
FLOOR_HEIGHT_M = 3.3
