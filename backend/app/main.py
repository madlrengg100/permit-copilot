"""FastAPI 진입점. 질의를 받아 오케스트레이터를 돌리고 SSE 로 이벤트를 흘린다."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import LLM_BASE_NAME, LLM_MODEL, LLM_PROVIDER, USE_MOCK, VWORLD_KEY
from .llm import make_client
from .orchestrator import Orchestrator
from .tools import vworld

app = FastAPI(title="공간정보 기반 인허가 사전진단")

# 접근 허용 출처. 외부 노출 시 ALLOWED_ORIGINS 에 실제 주소를 넣는다.
#   ALLOWED_ORIGINS="http://34.50.55.150:5173,http://localhost:5173"
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

# 공유 토큰. 설정하면 /api/chat 호출 시 X-App-Token 헤더가 일치해야 한다.
#
# /api/chat 은 Anthropic API 를 호출하므로, 인증 없이 공개 IP 에 열어두면
# 누구든 이 엔드포인트로 토큰(=비용)을 소진시킬 수 있다. 외부 노출 시 반드시 설정.
APP_TOKEN = os.getenv("APP_TOKEN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def warn_if_unprotected() -> None:
    if not APP_TOKEN:
        print(
            "\n" + "!" * 72 + "\n"
            "경고: APP_TOKEN 이 설정되지 않아 /api/chat 이 인증 없이 열려 있습니다.\n"
            "공개 IP 로 노출한 상태에서 ANTHROPIC_API_KEY 를 설정하면,\n"
            "누구든 이 엔드포인트를 호출해 API 비용을 발생시킬 수 있습니다.\n"
            "외부 노출 시 APP_TOKEN 을 반드시 설정하세요.\n" + "!" * 72 + "\n"
        )

client = make_client()

# 세션 ID -> Orchestrator. 프로세스 메모리 보관(단일 인스턴스 전제).
_sessions: dict[str, Orchestrator] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str


def _provider_label() -> str:
    """실제로 호출 중인 서비스 이름. 화면 안내가 엉뚱한 곳을 가리키지 않도록."""
    if LLM_PROVIDER == "anthropic":
        return "Anthropic"
    return {
        "gemini": "Google Gemini",
        "groq": "Groq",
        "cerebras": "Cerebras",
        "openrouter": "OpenRouter",
    }.get(LLM_BASE_NAME, "OpenAI")


def _provider_billing_hint() -> str:
    return {
        "Anthropic": "console.anthropic.com → Plans & Billing 에서 확인하세요.",
        "Google Gemini": (
            "무료 티어는 분당·일일 요청 한도가 있습니다. 잠시 후 다시 시도하거나 "
            "aistudio.google.com 에서 사용량을 확인하세요."
        ),
        "Groq": "console.groq.com 에서 사용량을 확인하세요.",
        "Cerebras": "cloud.cerebras.ai 에서 사용량을 확인하세요.",
        "OpenRouter": "openrouter.ai 에서 사용량을 확인하세요.",
        "OpenAI": "platform.openai.com → Billing 에서 충전하세요.",
    }[_provider_label()]


def friendly_error(exc: Exception) -> str:
    """SDK 원문 에러는 사용자가 무엇을 해야 할지 알려주지 못한다.

    설정 실수로 인한 대표적인 실패는 조치 방법이 담긴 문장으로 바꿔서 내보낸다.
    """
    text = str(exc)

    key_env = "OPENAI_API_KEY" if LLM_PROVIDER == "openai" else "ANTHROPIC_API_KEY"
    label = _provider_label()

    if (
        "Could not resolve authentication method" in text
        or "authentication_error" in text
        or "api_key client option must be set" in text
        or "Incorrect API key" in text
    ):
        return (
            f"API 키가 설정되지 않았거나 올바르지 않습니다 "
            f"(서비스: {label}, 모델: {LLM_MODEL}, 환경변수: {key_env}).\n"
            "환경변수를 설정한 뒤 백엔드를 다시 시작하세요."
        )
    if "insufficient_quota" in text or "exceeded your current quota" in text or "RESOURCE_EXHAUSTED" in text:
        # provider 를 하드코딩하면 안 된다. OpenAI 호환 엔드포인트(Gemini/Groq 등)를
        # 쓸 때도 같은 문구가 나와 엉뚱한 곳을 안내하게 된다.
        return (
            f"{_provider_label()} 사용 한도에 걸렸습니다. (API 키 자체는 정상입니다)\n"
            f"{_provider_billing_hint()}"
        )
    if "model_not_found" in text or "does not exist" in text:
        return (
            f"모델 '{LLM_MODEL}' 을(를) 사용할 수 없습니다. "
            "계정에서 접근 가능한 모델인지 확인하거나 LLM_MODEL 환경변수로 바꾸세요."
        )
    if "credit balance is too low" in text:
        return (
            f"{label} 크레딧이 부족합니다. (API 키 자체는 정상입니다)\n"
            f"{_provider_billing_hint()}"
        )
    if "rate_limit" in text or "429" in text:
        return (
            f"{label} 요청 한도에 걸렸습니다. 잠시 후 다시 시도하세요.\n"
            f"{_provider_billing_hint()}"
        )
    if "VWorld" in text or "vworld" in text:
        return (
            f"공간정보 조회에 실패했습니다: {text}\n"
            "VWORLD_KEY 와 인증 도메인 등록(localhost)을 확인하세요."
        )
    return text


@app.get("/api/config")
async def get_config() -> dict:
    """프론트가 VWorld 지도를 띄우는 데 필요한 설정."""
    return {
        "vworld_key": VWORLD_KEY,
        "mock_mode": USE_MOCK,
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
    }


@app.get("/api/parcel-at")
async def parcel_at(lon: float = Query(...), lat: float = Query(...)) -> dict:
    """지도 클릭 즉시 선택 필지 경계를 돌려준다."""
    return await vworld.get_parcel(lon, lat)


@app.get("/api/address-search")
async def address_search(q: str = Query(..., min_length=2)) -> dict:
    """짧거나 중복되는 주소를 선택할 수 있도록 후보 목록을 반환한다."""
    return {"items": await vworld.search_addresses(q.strip())}


@app.get("/api/parcels")
async def parcels(
    west: float = Query(...), south: float = Query(...),
    east: float = Query(...), north: float = Query(...),
) -> dict:
    """2D 모드용 주변 연속지적도 경계."""
    return {"geometries": await vworld.get_parcels_bbox(west, south, east, north)}


@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    x_app_token: Optional[str] = Header(default=None),
) -> StreamingResponse:
    if APP_TOKEN and x_app_token != APP_TOKEN:
        raise HTTPException(status_code=401, detail="유효하지 않은 앱 토큰입니다.")

    orch = _sessions.setdefault(req.session_id, Orchestrator(client))

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in orch.ask(req.message):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = json.dumps({"message": friendly_error(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/session/{session_id}")
async def reset_session(session_id: str) -> dict:
    _sessions.pop(session_id, None)
    return {"reset": True}
