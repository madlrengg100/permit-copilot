"""LLM 어댑터 — Anthropic / OpenAI 를 같은 인터페이스로 쓴다.

오케스트레이터와 사전진단 에이전트는 도구 호출 루프가 핵심이고, 그 루프의
모양은 두 provider 가 사실상 같다(모델에게 도구 목록을 주고 → 모델이 호출을
요청하면 → 실행하고 결과를 돌려주고 → 반복). 다른 건 요청/응답의 형식뿐이다.

그 형식 차이만 여기서 흡수해서, 에이전트 코드가 provider 를 몰라도 되게 한다.
provider 를 바꿔도 판정 로직·프롬프트·도구 정의는 그대로 쓴다.

  LLM_PROVIDER=anthropic  (기본)  ANTHROPIC_API_KEY 필요
  LLM_PROVIDER=openai             OPENAI_API_KEY 필요
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import LLM_MODEL, LLM_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """provider 중립 응답."""

    texts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    # 대화 이력에 그대로 다시 넣어야 하는 provider 고유 형식의 assistant 메시지
    raw_assistant: Any = None


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------


class AnthropicAdapter:
    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        # 외부 LLM이 연결만 유지한 채 응답하지 않으면 SSE 화면이 무기한
        # 진행 중으로 남는다. 한 번의 상담 요청이 합리적인 시간 안에 실패해
        # 결정식 fallback 또는 사용자 오류 안내로 끝나도록 제한한다.
        self.client = AsyncAnthropic(timeout=20.0, max_retries=0)

    @staticmethod
    def tool_schema(tools: list[dict]) -> list[dict]:
        # 이미 Anthropic 형식({name, description, input_schema})으로 정의되어 있다
        return tools

    async def complete(
        self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int,
        model: str | None = None, reasoning_effort: str | None = None,
    ) -> LLMResponse:
        # reasoning_effort 는 gemini(OpenAI 호환) thinking 제어용 인자다. anthropic 은
        # 자체 thinking(adaptive)을 쓰므로 시그니처 호환을 위해 받되 사용하지 않는다.
        resp = await self.client.messages.create(
            model=model or LLM_MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=system,
            tools=self.tool_schema(tools),
            messages=messages,
        )
        out = LLMResponse(raw_assistant={"role": "assistant", "content": resp.content})
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                out.texts.append(block.text)
            elif block.type == "tool_use":
                out.tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
        return out

    @staticmethod
    def tool_results_message(results: list[dict]) -> dict:
        """results: [{id, content, is_error}] -> 대화에 넣을 user 메시지."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r["id"],
                    "content": r["content"],
                    "is_error": r.get("is_error", False),
                }
                for r in results
            ],
        }


# --------------------------------------------------------------------------


def _tool_call_to_dict(tc: Any) -> dict:
    """응답의 tool_call 을 대화 이력에 다시 넣을 dict 로 바꾼다.

    provider 고유 필드를 임의로 버리면 안 된다. 예를 들어 Gemini 는
    extra_content.google.thought_signature 를 함께 돌려주길 요구하며,
    빠지면 400 "Function call is missing a thought_signature" 로 거부한다.
    그래서 필요한 필드만 골라 담지 않고 원본을 그대로 직렬화한다.
    """
    if hasattr(tc, "model_dump"):
        d = tc.model_dump(exclude_none=True)
    else:  # dict 로 오는 경우
        d = {k: v for k, v in dict(tc).items() if v is not None}
    d.setdefault("type", "function")
    return d


class OpenAIAdapter:
    """OpenAI 및 OpenAI 호환 엔드포인트.

    Groq / Cerebras / OpenRouter / Gemini(호환 모드)는 모두 OpenAI 형식의
    /chat/completions 를 제공한다. base_url 과 키만 바꾸면 같은 코드로 붙는다.
    무료 티어를 쓰려면 config.py 의 프리셋을 참고할 것.
    """

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        kwargs: dict = {}
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        if OPENAI_API_KEY:
            kwargs["api_key"] = OPENAI_API_KEY
        # OpenAI SDK 기본 제한시간은 대화형 화면에서 너무 길다. 특히 Gemini
        # 호환 엔드포인트가 연결 후 응답을 늦게 주는 경우 무한 정지처럼 보이므로
        # 호출당 20초로 제한하고 SDK 내부 장시간 재시도는 하지 않는다.
        kwargs["timeout"] = 20.0
        kwargs["max_retries"] = 0
        self.client = AsyncOpenAI(**kwargs)

    @staticmethod
    def tool_schema(tools: list[dict]) -> list[dict]:
        """Anthropic 형식 도구 정의를 OpenAI function 형식으로 변환한다."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    async def complete(
        self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int,
        model: str | None = None, reasoning_effort: str | None = None,
    ) -> LLMResponse:
        # OpenAI 는 system 을 messages 의 첫 항목으로 넣는다
        payload = [{"role": "system", "content": system}, *messages]
        # gemini 2.5 flash 는 thinking 이 기본 ON 이라 지연이 크다. reasoning_effort 로
        # thinking 예산을 낮춰(none/low) 응답 지연을 제어한다(gemini OpenAI 호환 필드).
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        resp = await self.client.chat.completions.create(
            model=model or LLM_MODEL,
            max_completion_tokens=max_tokens,
            messages=payload,
            tools=self.tool_schema(tools),
            **extra,
        )
        msg = resp.choices[0].message

        out = LLMResponse(
            raw_assistant={
                "role": "assistant",
                "content": msg.content or "",
                # tool_calls 는 이력에 그대로 돌려줘야 다음 turn 에서 짝이 맞는다
                **(
                    {"tool_calls": [_tool_call_to_dict(tc) for tc in msg.tool_calls]}
                    if msg.tool_calls
                    else {}
                ),
            }
        )
        if msg.content and msg.content.strip():
            out.texts.append(msg.content)
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"도구 인자를 JSON 으로 파싱하지 못했습니다 ({tc.function.name}): {exc}"
                ) from exc
            out.tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
        return out

    @staticmethod
    def tool_results_message(results: list[dict]) -> list[dict]:
        """OpenAI 는 도구 결과를 호출 1건당 role="tool" 메시지 하나로 넣는다."""
        return [
            {"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in results
        ]


# --------------------------------------------------------------------------


def make_client():
    if LLM_PROVIDER == "openai":
        return OpenAIAdapter()
    if LLM_PROVIDER == "anthropic":
        return AnthropicAdapter()
    raise LLMError(f"알 수 없는 LLM_PROVIDER: {LLM_PROVIDER} (anthropic | openai)")


def append_tool_results(messages: list[dict], adapter, results: list[dict]) -> None:
    """provider 형식에 맞게 도구 결과를 대화 이력에 붙인다."""
    out = adapter.tool_results_message(results)
    if isinstance(out, list):
        messages.extend(out)
    else:
        messages.append(out)
