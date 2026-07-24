"""오케스트레이터.

사용자의 자연어 질의를 받아 어떤 에이전트를 어떤 순서로 돌릴지 판단하고,
결과를 종합해 답변한다. 판단은 Claude 가, 실행은 도구가 한다.

  prediagnose      사전진단 에이전트 호출 (공간정보 + 법령 규제 검토)
  render_on_map    지도제어 에이전트 호출 (진단 결과를 지도에 반영)
  restudy_massing  좌표·규제 재조회 없이 건축 가능 규모만 다시 산출 (후속 질의용)

이벤트를 async generator 로 흘려보내 프론트가 진행 상황을 실시간으로 본다.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from .agents.map_control import build_map_commands
from .agents.prediagnosis import compact, run_prediagnosis
from .llm import append_tool_results
from .tools import massing

SYSTEM = """당신은 공간정보 기반 건축 인허가 상담 시스템의 오케스트레이터입니다.
사용자의 자연어 질의를 해석해 필요한 에이전트를 호출하고, 결과를 종합해 답변합니다.

판단 기준:
- 특정 주소/필지 또는 위도·경도로 지정한 위치의 건축 가능 여부를 묻는 질의 -> prediagnose 를 먼저 호출한다.
- prediagnose 는 결과를 지도에 자동 반영한다. 따로 render_on_map 을 부를 필요 없다.
- 직전 진단이 있는 상태에서 "용적률 250%면?", "10층으로 올리면?" 같은 조건 변경
  질의는 restudy_massing 을 쓴다. prediagnose 를 다시 돌리지 않는다.
- 주소가 없는 일반 법령 질문은 도구 없이 바로 답한다.
- 주소가 모호하면(예: "강남에 건물") 되묻는다. 임의로 특정 주소를 지어내지 않는다.

답변 작성:
- 결론(가능/조건부/불가)을 먼저 말한다.
- 근거가 된 용도지역, 건폐율·용적률, 대지면적, 산출된 연면적·층수를 제시한다.
- "이 필지에 뭘 지을 수 있어?"처럼 용도를 열거해 달라는 질의에는 진단 결과의
  regulation.zone_use_overview 를 근거로 가능(allowed)/조건부(conditional)/불가
  (not_allowed) 용도를 모두 나열한다. 이 목록은 판정표 9개 대분류 기준의 개요이며
  건축법 시행령 별표1 세부 용도 전체가 아니라는 점을 함께 밝힌다.
- 이 결과가 법정 상한 기준 이론값이며 지자체 조례, 일조권 사선제한, 이격거리,
  주차대수 산정으로 축소된다는 점을 반드시 덧붙인다.
- 요청 용적률이 상한을 초과해 exceeds_far_limit=true이면 요청 규모의 층수나
  연면적을 제시하지 말고, 적용 불가 결론과 건폐율 기준 최대 건축면적만 제시한다.
- 한국어로, 실무자에게 말하듯 간결하게. 표나 불릿을 과하게 쓰지 않는다.
- 산출된 건물 규모는 '건축 가능 규모'라고 부른다. '매스'는 설계 실무 용어라
  토지주·사업자에게는 통하지 않으므로 쓰지 않는다."""

TOOLS: list[dict] = [
    {
        "name": "prediagnose",
        "description": (
            "사전진단 에이전트를 호출한다. 주소 또는 위도·경도에서 필지와 용도지역을 조회한 뒤, "
            "해당 용도의 건축 허용 여부와 건폐율·용적률을 검토하고 건축 가능 규모를 산출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "사전진단 에이전트에게 전달할 지시. 주소와 검토할 건축물 용도를 "
                        "명확히 포함시킨다. 예: '서울시 강남구 테헤란로 152에 업무시설을 "
                        "지을 수 있는지 검토'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "render_on_map",
        "description": (
            "직전 사전진단 결과를 VWorld 3D 지도에 반영한다. 해당 위치로 이동하고, "
            "필지를 강조하고, 산출된 건축 가능 규모를 3D 로 세운다."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "restudy_massing",
        "description": (
            "직전 진단의 대지면적과 규제값을 그대로 쓰고 밀도만 바꿔 건축 가능 규모를 다시 산출한다. "
            "'용적률 200%면 몇 층?' 같은 후속 질의에 사용한다. 호출 후 render_on_map 으로 "
            "지도를 갱신한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "far_target_pct": {"type": "number", "description": "적용할 용적률(%)"},
                "bcr_target_pct": {
                    "type": "number",
                    "description": "적용할 건폐율(%). 생략하면 직전 값을 유지한다.",
                },
            },
            "required": ["far_target_pct"],
        },
    },
]


class Orchestrator:
    """대화 1건(=세션)의 상태를 들고 있는 오케스트레이터."""

    def __init__(self, client) -> None:
        self.client = client
        self.messages: list[dict] = []
        self.diagnosis: dict | None = None   # 직전 사전진단 결과

    async def ask(self, user_query: str, max_turns: int = 8) -> AsyncIterator[dict]:
        self.messages.append({"role": "user", "content": user_query})

        for _ in range(max_turns):
            response = await self.client.complete(
                system=SYSTEM, messages=self.messages, tools=TOOLS, max_tokens=8000
            )
            self.messages.append(response.raw_assistant)

            for text in response.texts:
                yield {"event": "message", "data": {"text": text}}

            if not response.tool_calls:
                return

            results = []
            for call in response.tool_calls:
                yield {"event": "tool_start", "data": {"tool": call.name}}
                try:
                    out, extra_events = await self._run_tool(call.name, call.input)
                    for ev in extra_events:
                        yield ev
                    content = json.dumps(out, ensure_ascii=False)
                    is_error = False
                except Exception as exc:
                    content = f"오류: {exc}"
                    is_error = True
                    yield {"event": "error", "data": {"tool": call.name, "message": str(exc)}}

                results.append({"id": call.id, "content": content, "is_error": is_error})

            append_tool_results(self.messages, self.client, results)

    # ------------------------------------------------------------------

    def _render_event(self) -> dict:
        """현재 진단을 지도 명령으로 바꿔 프론트로 보낼 이벤트."""
        return {
            "event": "map_commands",
            "data": {"commands": build_map_commands(self.diagnosis or {})},
        }

    async def _run_tool(self, name: str, args: dict) -> tuple[dict, list[dict]]:
        """도구를 실행하고 (모델에게 돌려줄 결과, 프론트로 흘릴 추가 이벤트) 반환."""
        events: list[dict] = []

        if name == "prediagnose":
            steps: list[dict] = []
            self.diagnosis = await run_prediagnosis(
                self.client,
                args["query"],
                on_progress=lambda step, payload: steps.append(
                    {"event": "diagnosis_step", "data": {"step": step, "input": payload}}
                ),
            )
            events.extend(steps)
            events.append({"event": "diagnosis", "data": self.diagnosis})

            # 진단이 끝나면 지도 반영은 확정 절차다. 이걸 별도 도구로 두고
            # 모델에게 "이제 지도에 그려라"를 한 번 더 판단시키면 LLM 호출이
            # 한 번 더 늘 뿐, 결과는 항상 같다. 여기서 바로 실행한다.
            events.append(self._render_event())

            # 모델에게는 경계 폴리곤을 뺀 축약본만 준다(컨텍스트 절약).
            return {"diagnosis": compact(self.diagnosis), "rendered_on_map": True}, events

        if name == "render_on_map":
            if not self.diagnosis:
                raise RuntimeError("먼저 prediagnose 를 실행해야 지도에 반영할 수 있습니다.")
            ev = self._render_event()
            events.append(ev)
            return {"rendered": True, "command_count": len(ev["data"]["commands"])}, events

        if name == "restudy_massing":
            if not self.diagnosis or not self.diagnosis.get("regulation"):
                raise RuntimeError("직전 진단이 없습니다. 먼저 prediagnose 를 실행하세요.")

            reg = self.diagnosis["regulation"]
            parcel = self.diagnosis.get("parcel", {})
            new_mass = massing.calc_massing(
                area_m2=parcel.get("area_m2", 0),
                bcr_max_pct=args.get("bcr_target_pct", reg["bcr_max_pct"]),
                far_max_pct=reg["far_max_pct"],
                far_target_pct=args["far_target_pct"],
            )
            self.diagnosis["massing"] = new_mass
            events.append({"event": "diagnosis", "data": self.diagnosis})
            events.append(self._render_event())   # 규모가 바뀌면 지도도 함께 갱신
            return new_mass, events

        raise ValueError(f"알 수 없는 도구: {name}")
