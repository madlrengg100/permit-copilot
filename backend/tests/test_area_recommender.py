import unittest

from app.agents.area_recommender import _region_address_terms, _region_core
from app.agents.prediagnosis import (
    _deterministic_request,
    _guess_use,
    _has_building_feasibility_intent,
    _USE_ALIASES,
    detect_use_restriction,
)
from app.orchestrator import (
    Orchestrator,
    _is_building_restore_request,
    _same_parcel_address,
    _asks_possible_use_list,
    _model_options_for_diagnosis,
)


class NaturalLanguageRegressionTest(unittest.TestCase):
    def test_possible_use_list_intent_is_narrow_and_semantic_questions_are_not_lists(self):
        should_list = [
            "선택 필지에 가능한 건물 뭐야",
            "허용되는 건축물 종류 알려줘",
            "가능 모델 보여줘",
            "다른 모델 가능해?",
            "다른 건물도 가능해?",
            "건축할 수 있는 거 있어?",
            "뭘 지을 수 있어?",
            "건물 있어?",
        ]
        should_not_list = [
            "선택 필지에 가축사육이 제한돼?",
            "가축사육제한구역이 무슨 뜻이야?",
            "모델이 뭐야?",
            "3D 모델의 사전적 의미가 뭐야?",
            "가능 모델이라는 말의 뜻이 뭐야?",
            "모델을 왜 표시해?",
            "모델 꺼줘",
            "3D 모델 다시 켜줘",
            "또 확인해야 하는 것은 뭐야?",
            "조건부 가능이 왜 나온 거야?",
            "건축 가능이라는 말의 뜻이 뭐야?",
            "도로 접도 확인 필요해?",
            "이 지역 규제가 건축에 적용돼?",
        ]
        for wording in should_list:
            with self.subTest(wording=wording):
                self.assertTrue(_asks_possible_use_list(wording))
        for wording in should_not_list:
            with self.subTest(wording=wording):
                self.assertFalse(_asks_possible_use_list(wording))

    def test_model_options_come_only_from_structured_diagnosis(self):
        diagnosis = {
            "verdict": "conditional",
            "request": {"building_use": "시설물"},
            "regulation": {
                "verdict": "conditional",
                "zone_use_overview": {
                    "allowed": ["단독주택", "제1종근린생활시설"],
                    "conditional": ["창고시설"],
                    "not_allowed": ["공장"],
                },
            },
            "massing": {
                "floors": 3,
                "layout_feasible": True,
                "exceeds_far_limit": False,
            },
        }
        options = _model_options_for_diagnosis(diagnosis)
        actions = [option["action"] for option in options]
        self.assertEqual(
            actions,
            ["housing:detached", "housing:commercial", "housing:warehouse"],
        )
        diagnosis["verdict"] = "not_allowed"
        diagnosis["regulation"]["verdict"] = "not_allowed"
        self.assertEqual(_model_options_for_diagnosis(diagnosis), [])

    def test_alternative_model_request_uses_all_structured_allowed_uses(self):
        diagnosis = {
            "verdict": "conditional",
            "request": {"building_use": "창고시설"},
            "regulation": {
                "verdict": "conditional",
                "zone_use_overview": {
                    "allowed": ["단독주택", "제1종근린생활시설"],
                    "conditional": ["창고시설"],
                    "not_allowed": ["공장"],
                },
            },
            "massing": {
                "floors": 3,
                "layout_feasible": True,
                "exceeds_far_limit": False,
            },
        }
        current_only = _model_options_for_diagnosis(diagnosis)
        alternatives = _model_options_for_diagnosis(
            diagnosis,
            include_alternatives=True,
        )
        self.assertEqual(
            [option["action"] for option in current_only],
            ["housing:warehouse"],
        )
        self.assertEqual(
            [option["action"] for option in alternatives],
            ["housing:detached", "housing:commercial", "housing:warehouse"],
        )

    def test_explicit_alternative_request_survives_original_use_not_allowed(self):
        diagnosis = {
            "verdict": "not_allowed",
            "request": {"building_use": "판매시설"},
            "regulation": {
                "verdict": "not_allowed",
                "zone_use_overview": {
                    "allowed": ["단독주택", "공동주택", "제1종근린생활시설"],
                    "conditional": ["제2종근린생활시설", "업무시설"],
                    "not_allowed": ["판매시설", "공장", "창고시설"],
                },
            },
            "massing": None,
        }

        self.assertEqual(
            _model_options_for_diagnosis(
                diagnosis,
                include_alternatives=True,
            ),
            [],
        )
        alternatives = _model_options_for_diagnosis(
            diagnosis,
            include_alternatives=True,
            allow_alternative_verdict=True,
        )
        self.assertEqual(
            [option["action"] for option in alternatives],
            ["housing:detached", "housing:lowrise", "housing:commercial"],
        )

    def test_building_feasibility_intent_handles_varied_natural_language(self):
        address = "충청남도 아산시 음봉면 신수리 347"
        variants = [
            "건물 지을 수 있어?",
            "건축할 수 있어?",
            "건축 가능해?",
            "허가 나와?",
            "건축허가 날까?",
            "여기 뭐 올릴 수 있어?",
            "집 지어도 돼?",
            "건물 들어갈 수 있나?",
            "개발 가능한 땅이야?",
            "여기에 지어도 되나?",
            "건축 날 수 있어?",
        ]
        for wording in variants:
            with self.subTest(wording=wording):
                query = f"{address}에 {wording}"
                self.assertTrue(_has_building_feasibility_intent(query))
                request = _deterministic_request(query)
                self.assertIsNotNone(request)
                self.assertEqual(request["address"], address)

    def test_non_feasibility_requests_do_not_enter_generic_diagnosis(self):
        address = "충청남도 아산시 음봉면 신수리 347"
        for wording in [
            "공시지가 알려줘",
            "건축물대장 조회해줘",
            "건물 모델 꺼",
            "건축이란 말의 뜻을 알려줘",
        ]:
            with self.subTest(wording=wording):
                self.assertFalse(_has_building_feasibility_intent(wording))
                self.assertIsNone(_deterministic_request(f"{address}에 {wording}"))

    def test_new_parcel_diagnosis_wrapper_is_not_model_restore(self):
        wrapped = (
            "지도에서 선택한 위치(경도 126.9763839, 위도 36.8500501)에서 "
            "방금 클릭한 필지가 이전 진단 필지와 다르면 새 필지이므로 종합 판정부터 "
            "처음 진단한다. 사용자가 원하는 건축물 용도는 "
            '"선택 필지에 건물 가능해"이다. 건축 가능 여부를 검토해줘'
        )
        self.assertFalse(_is_building_restore_request(wrapped))
        self.assertFalse(_is_building_restore_request("선택 필지에 건물 가능해"))
        self.assertTrue(_is_building_restore_request("3D 모델 다시 보여줘"))
        self.assertTrue(_is_building_restore_request("다시 원상 복구해줘"))

    def test_region_address_terms_keeps_city_and_dong(self):
        self.assertEqual(
            _region_address_terms("경기도 의왕시 초평동"),
            ["의왕시", "초평동"],
        )

    def test_region_address_terms_keeps_county_and_town(self):
        self.assertEqual(
            _region_address_terms("충청남도 아산시 음봉면"),
            ["아산시", "음봉면"],
        )

    def test_region_core_supports_short_region_name(self):
        self.assertEqual(_region_core("양평"), "양평")

    def test_one_room_is_detached_house(self):
        self.assertEqual(_guess_use("1층 원룸 2층 주인 거주"), "단독주택")
        self.assertEqual(_USE_ALIASES["다가구주택"], "단독주택")

    def test_multifamily_and_first_exclusive_one_room_warnings(self):
        diagnosis = {
            "regulation": {
                "zone": "제1종전용주거지역",
                "zone_use_overview": {
                    "allowed": ["단독주택"],
                    "conditional": [],
                    "not_allowed": ["공동주택"],
                }
            }
        }
        warning = detect_use_restriction("다세대주택을 지어줘", diagnosis)
        self.assertIsNotNone(warning)
        self.assertIn("건축불가", warning["label"].replace(" ", ""))
        one_room_warning = detect_use_restriction(
            "1층 원룸, 2층 주인 거주", diagnosis
        )
        self.assertIsNotNone(one_room_warning)
        self.assertIn("계획 확인 필요", one_room_warning["label"])

    def test_full_asan_address_is_not_reinterpreted_as_another_sinsu_ri(self):
        request = _deterministic_request(
            "충청남도 아산시 음봉면 신수리 100에 창고 지을 수 있어?"
        )
        self.assertIsNotNone(request)
        self.assertEqual(
            request["address"],
            "충청남도 아산시 음봉면 신수리 100",
        )
        self.assertEqual(request["building_use"], "창고시설")
        self.assertFalse(request["inferred"])

    def test_full_mountain_lot_address_is_preserved(self):
        request = _deterministic_request(
            "서울특별시 종로구 청운동 산 4-39에 건물 가능해?"
        )
        self.assertEqual(
            request["address"],
            "서울특별시 종로구 청운동 산 4-39",
        )

    def test_selected_parcel_coordinates_are_not_treated_as_an_address(self):
        request = _deterministic_request(
            '지도에서 선택한 위치(경도 127.1234567, 위도 36.7654321)에서 '
            '사용자가 원하는 건축물 용도는 "어떤 건물 가능해"이다. '
            "건축 가능 여부를 검토해줘"
        )
        self.assertEqual(request["address"], "")
        self.assertEqual(request["lon"], 127.1234567)
        self.assertEqual(request["lat"], 36.7654321)
        self.assertEqual(request["building_use"], "시설물")


class PossibleUsesFollowupTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_new_address_replaces_old_selected_pnu_and_context(self):
        from unittest.mock import AsyncMock, patch

        old_pnu = "4420035023200250000"
        new_pnu = "4773047024101030003"
        orchestrator = Orchestrator(client=None)
        orchestrator.selected_parcel = {
            "lon": 126.997,
            "lat": 36.842,
            "address": "충청남도 아산시 음봉면 신수리 산 25",
            "pnu": old_pnu,
        }
        orchestrator.diagnosis = {
            "parcel": {
                "pnu": old_pnu,
                "jibun": "충청남도 아산시 음봉면 신수리 산 25",
            },
        }
        orchestrator.messages = [
            {"role": "user", "content": "이전 필지 질문"},
        ]
        orchestrator._turn_has_explicit_address = True
        new_diagnosis = {
            "parcel": {
                "pnu": new_pnu,
                "jibun": "경상북도 의성군 안사면 신수리 103-3",
            },
            "location": {"lon": 128.445, "lat": 36.479},
            "request": {"building_use": "단독주택"},
            "regulation": {"verdict": "conditional"},
            "verdict": "conditional",
        }
        with patch(
            "app.orchestrator.run_prediagnosis",
            new=AsyncMock(return_value=new_diagnosis),
        ), patch.object(
            orchestrator,
            "_render_event",
            return_value={"event": "map_commands", "data": {"commands": []}},
        ):
            await orchestrator._diagnose_and_emit(
                "경상북도 의성군 안사면 신수리 103-3 단독주택",
                emit_card=False,
            )

        self.assertEqual(orchestrator.selected_parcel["pnu"], new_pnu)
        self.assertEqual(
            orchestrator.selected_parcel["address"],
            "경상북도 의성군 안사면 신수리 103-3",
        )
        self.assertEqual(
            orchestrator.conversation_context()["active_address"],
            "경상북도 의성군 안사면 신수리 103-3",
        )
        self.assertIn(old_pnu, orchestrator._messages_by_pnu)

    async def test_llm_followup_intent_controls_models_without_blocking_definitions(self):
        class IntentClient:
            def __init__(self, intent):
                self.intent = intent
                self.called = False

            async def complete(self, **_kwargs):
                self.called = True
                call = type("Call", (), {
                    "name": "return_followup_interpretation",
                    "input": {
                        "intent": self.intent,
                        "subject": "모델",
                        "answer": "현재 필지의 판정 데이터를 검토한 답변입니다.",
                    },
                })()
                return type("Result", (), {
                    "texts": [],
                    "tool_calls": [call],
                })()

        diagnosis = {
            "parcel": {
                "pnu": "TEST-PNU",
                "jibun": "테스트 필지",
            },
            "request": {"building_use": "시설물"},
            "verdict": "conditional",
            "regulation": {
                "verdict": "conditional",
                "zone_use_overview": {
                    "allowed": ["단독주택"],
                    "conditional": ["창고시설"],
                },
            },
            "massing": {
                "floors": 3,
                "layout_feasible": True,
                "exceeds_far_limit": False,
            },
        }

        model_client = IntentClient("possible_models")
        models = Orchestrator(client=model_client)
        models.diagnosis = diagnosis
        model_events = [
            event async for event in models.ask("다른 모델 가능해?", continuation=True)
        ]
        panel_context = next(
            event["data"]["commands"][0]
            for event in model_events
            if event["event"] == "map_commands"
            and event["data"].get("commands")
            and event["data"]["commands"][0].get("type") == "set_panel_context"
        )
        self.assertEqual(panel_context["building_use"], "가능한 건축물 전체")
        model_message = next(
            event["data"] for event in model_events if event["event"] == "message"
        )
        self.assertIn("가능 모델", model_message["text"])
        self.assertEqual(len(model_message["options"]), 2)
        self.assertTrue(model_client.called)

        definition_client = IntentClient("term_definition")
        definition = Orchestrator(client=definition_client)
        definition.diagnosis = diagnosis
        definition_events = [
            event async for event in definition.ask("모델이 무슨 뜻이야?", continuation=True)
        ]
        definition_message = next(
            event["data"] for event in definition_events if event["event"] == "message"
        )
        self.assertNotIn("options", definition_message)
        self.assertNotIn("가능 모델", definition_message["text"])
        self.assertTrue(definition_client.called)

    async def test_restriction_applicability_question_reaches_llm_with_parcel_data(self):
        class RecordingClient:
            def __init__(self):
                self.kwargs = None

            async def complete(self, **kwargs):
                self.kwargs = kwargs
                return type("Result", (), {"texts": ["필지 규제 적용 결과입니다."]})()

        client = RecordingClient()
        orchestrator = Orchestrator(client=client)
        orchestrator.messages = [
            {"role": "user", "content": "가축사육제한구역이 무슨 뜻이야?"},
            {"role": "user", "content": "또 확인해야 하는 것은 뭐야?"},
        ]
        orchestrator.diagnosis = {
            "parcel": {"jibun": "충청남도 아산시 음봉면 신수리 374-2"},
            "land_use": {
                "districts": ["가축사육제한구역"],
                "designation_lookup": {
                    "status": "AVAILABLE",
                    "active": [
                        {
                            "name": "가축사육제한구역",
                            "relation": "포함",
                        }
                    ],
                },
            },
            "regulation": {
                "zone_use_overview": {
                    "allowed": ["단독주택"],
                    "conditional": ["창고시설"],
                }
            },
        }

        answer = await orchestrator._natural_followup_answer(
            "선택 필지에 가축사육이 제한돼?"
        )

        self.assertEqual(answer, "필지 규제 적용 결과입니다.")
        self.assertIsNotNone(client.kwargs)
        prompt = str(client.kwargs)
        self.assertIn("가축사육제한구역", prompt)
        self.assertIn("포함", prompt)
        self.assertIn("같은 필지의 최근 사용자 질문", prompt)
        self.assertIn("가축사육제한구역이 무슨 뜻이야", prompt)
        self.assertNotIn("가능한 용도는", answer)

    async def _map_commands(self, query):
        orchestrator = Orchestrator(client=None)
        events = [event async for event in orchestrator.ask(query)]
        return [
            command
            for event in events
            if event["event"] == "map_commands"
            for command in event["data"]["commands"]
        ]

    async def _map_command_types(self, query):
        return [command["type"] for command in await self._map_commands(query)]

    async def test_all_map_controls_have_natural_language_routes(self):
        cases = {
            "2D 지도로 전환해줘": {"type": "set_view_mode", "mode": "2d"},
            "3D 지도 보여줘": {"type": "set_view_mode", "mode": "3d"},
            "내 위치로 이동해줘": {"type": "run_tool", "action": "my_location"},
            "거리 측정해줘": {"type": "run_tool", "action": "measure_line"},
            "넓이 재줘": {"type": "run_tool", "action": "measure_area"},
            "높이 측정해줘": {"type": "run_tool", "action": "measure_height"},
            "측정 결과 지워줘": {"type": "run_tool", "action": "erase"},
            "도구 메뉴 열어줘": {"type": "set_tool_menu", "open": True},
            "메뉴 닫아줘": {"type": "set_tool_menu", "open": False},
            "지적도 꺼줘": {"type": "set_layers", "cadastre": False},
            "용도지역 보여줘": {"type": "set_layers", "zoning": True},
            "경사도 켜줘": {"type": "set_layers", "slope": True},
            "치수선 숨겨줘": {"type": "set_layers", "dimensions": False},
            "판정창 펼쳐줘": {"type": "set_layers", "panel": True},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                commands = await self._map_commands(query)
                self.assertTrue(
                    any(all(command.get(k) == v for k, v in expected.items())
                        for command in commands),
                    commands,
                )

        commands = await self._map_commands("지도 레이어 전부 꺼줘")
        self.assertIn(
            {
                "type": "set_layers",
                "cadastre": False,
                "zoning": False,
                "slope": False,
                "dimensions": False,
            },
            commands,
        )

    async def test_building_shape_natural_language_on_off(self):
        for query in [
            "LOD1 꺼줘",
            "건물 윤곽 꺼죠",
            "건물 형상 숨겨줘",
            "모델 꺼",
            "3D 모델 끄기",
        ]:
            with self.subTest(query=query):
                self.assertEqual(
                    await self._map_command_types(query),
                    ["hide_building_shape"],
                )
        for query in ["LOD1 켜줘", "건물 윤곽 보여줘"]:
            with self.subTest(query=query):
                self.assertIn(
                    "show_lod1",
                    await self._map_command_types(query),
                )
        for query in ["건축면적만 보여줘", "바닥면적만 보여줘", "평면만 켜줘"]:
            with self.subTest(query=query):
                self.assertEqual(
                    await self._map_command_types(query),
                    ["show_building_footprint"],
                )
        for query in [
            "다시 모델 켜",
            "3D 모델 보여줘",
            "다시 이전으로 보여줘",
            "복원해줘",
            "다시 원상 복구해줘",
            "원래대로 돌려줘",
            "다시 켜",
            "다시 보여줘",
        ]:
            with self.subTest(query=query):
                self.assertEqual(
                    await self._map_command_types(query),
                    ["show_building_shape"],
                )

    async def test_hiding_model_never_changes_verdict_or_renders_panel(self):
        orchestrator = Orchestrator(client=None)
        orchestrator.diagnosis = {
            "verdict": "conditional",
            "regulation": {"verdict": "conditional"},
        }
        commands = await self._map_commands("모델 꺼")
        self.assertEqual(commands, [{"type": "hide_building_shape"}])

    async def test_restore_targets_last_hidden_not_always_building(self):
        # '다시 켜'는 방금 '끈' 대상을 되살린다. 치수선을 껐으면 치수선을,
        # 모델을 껐으면 모델을 복원해야 한다(맥락 없는 항상-모델 복원 금지).
        async def run(orch, query):
            out = []
            async for event in orch.ask(query):
                if event["event"] == "map_commands":
                    out += event["data"]["commands"]
            return out

        base = {
            "parcel": {"pnu": "RESTORE-TEST"},
            "verdict": "conditional",
            "regulation": {"verdict": "conditional"},
            "massing": {"floors": 3},
        }
        parcel = {"pnu": "RESTORE-TEST", "lon": 127.0, "lat": 37.0, "address": "테스트"}

        # 치수선을 끈 뒤 '다시 켜' → 치수선을 되살린다.
        orch = Orchestrator(client=None)
        orch.diagnosis = dict(base)
        orch.selected_parcel = dict(parcel)
        await run(orch, "치수선 꺼")
        self.assertEqual(
            await run(orch, "다시 켜"),
            [{"type": "set_layers", "dimensions": True}],
        )

        # 모델을 끈 뒤 '다시 켜' → 모델을 되살린다.
        orch2 = Orchestrator(client=None)
        orch2.diagnosis = dict(base)
        orch2.selected_parcel = dict(parcel)
        await run(orch2, "모델 꺼")
        self.assertEqual(
            await run(orch2, "다시 켜"),
            [{"type": "show_building_shape"}],
        )

    async def test_locate_phrase_moves_instead_of_diagnosing(self):
        # '고읍동 128-2 찾을 수 있어'는 종합진단이 아니라 그 필지로 이동해야 한다.
        # 반대로 '지을 수 있어'(건축 가능)는 이동으로 새면 안 된다.
        from unittest.mock import AsyncMock

        orch = Orchestrator(client=None)
        self.assertTrue(orch._requests_move_phrase("고읍동 128-2 찾을 수 있어"))
        self.assertTrue(orch._requests_move_phrase("고읍동 128-2 어디야"))
        self.assertFalse(orch._requests_move_phrase("고읍동 128-2에 창고 지을 수 있어"))

        orch._run_tool = AsyncMock(return_value=({"ok": True}, []))
        tools = [
            event["data"]["tool"]
            async for event in orch.ask("경기도 양주시 고읍동 128-2 찾을 수 있어")
            if event["event"] == "tool_start"
        ]
        self.assertIn("move_to_parcel", tools)
        self.assertNotIn("prediagnose", tools)

    async def test_teaching_statement_is_not_swallowed_as_concept(self):
        # '~하라는 뜻이야' 같은 교육 문장은 개념 정의 질문이 아니다.
        from app.orchestrator import _is_teaching_statement

        self.assertTrue(_is_teaching_statement("띄워봐라고 하면 이동하라는 뜻이야"))
        self.assertTrue(_is_teaching_statement("수치선 꺼도 되게 해"))
        self.assertFalse(_is_teaching_statement("용적률이 뭐야?"))
        self.assertFalse(_is_teaching_statement("이격거리가 뭐야?"))

    async def test_explicit_address_feasibility_variants_bypass_orchestrator_llm(self):
        # client=None에서도 tool-selection LLM으로 빠지지 않고 prediagnose에
        # 직접 진입해야 한다. 실제 공간조회는 이 단위 테스트에서 대체한다.
        from unittest.mock import AsyncMock, patch

        diagnosis = {
            "verdict": "conditional",
            "request": {"building_use": "시설물", "inferred": True},
            "location": {
                "lon": 126.976,
                "lat": 36.850,
                "matched_address": "충청남도 아산시 음봉면 신수리 347",
            },
            "parcel": {
                "pnu": "4420035023103470000",
                "jibun": "충청남도 아산시 음봉면 신수리 347",
            },
            "land_use": {"zones": ["계획관리지역"], "districts": []},
            "regulation": {
                "zone": "계획관리지역",
                "verdict": "conditional",
                "building_use": "시설물",
            },
        }
        variants = [
            "건물 지을 수 있어?",
            "건축허가 날까?",
            "여기 뭐 올릴 수 있어?",
            "건축 날 수 있어?",
        ]
        for wording in variants:
            with self.subTest(wording=wording):
                orchestrator = Orchestrator(client=None)
                with patch(
                    "app.orchestrator.run_prediagnosis",
                    new=AsyncMock(return_value=diagnosis),
                ):
                    events = [
                        event
                        async for event in orchestrator.ask(
                            f"충청남도 아산시 음봉면 신수리 347에 {wording}"
                        )
                    ]
                self.assertTrue(
                    any(event["event"] == "diagnosis" for event in events),
                    events,
                )

    async def test_possible_buildings_followup_lists_full_overview(self):
        orchestrator = Orchestrator(client=None)
        orchestrator.diagnosis = {
            "parcel": {"jibun": "경상북도 경산시 백천동 562-5"},
            "request": {"building_use": "시설물", "inferred": True},
            "regulation": {
                "zone_use_overview": {
                    "allowed": ["단독주택", "공동주택", "제1종근린생활시설"],
                    "conditional": ["업무시설"],
                    "not_allowed": ["공장"],
                }
            },
        }

        questions = [
            "선택 필지에 가능한 건물 뭐야",
            "여기 건축 가능 뭐야",
            "건축할 수 있는 거 있어?",
            "건물 있어?",
            "건축 있어?",
            "지을 거 있어?",
            "뭘 지을 수 있어?",
            "어떤 용도가 돼?",
            "허용 건축물 알려줘",
        ]
        for question in questions:
            with self.subTest(question=question):
                answer = await orchestrator._natural_followup_answer(question)
                self.assertIn("단독주택, 공동주택, 제1종근린생활시설", answer)
                self.assertIn("조건부 용도는 업무시설", answer)
                self.assertNotIn("단독주택 외", answer)

    def test_selected_parcel_use_followup_keeps_coordinates_and_use(self):
        request = _deterministic_request(
            "지도에서 선택한 위치(경도 127.1234567, 위도 36.7654321)의 "
            "필지에 대한 질문이다: 상가 용도는 무엇으로 한정되어 있어"
        )
        self.assertEqual(request["address"], "")
        self.assertEqual(request["lon"], 127.1234567)
        self.assertEqual(request["lat"], 36.7654321)
        self.assertEqual(request["building_use"], "제1종근린생활시설")

    def test_same_full_address_is_recognized_as_followup(self):
        diagnosis = {
            "request": {"address": "충청남도 아산시 음봉면 신수리 100"},
            "parcel": {"jibun": "충청남도 아산시 음봉면 신수리 100-1"},
        }
        self.assertTrue(
            _same_parcel_address(
                "충청남도 아산시 음봉면 신수리 100", diagnosis
            )
        )
        self.assertFalse(
            _same_parcel_address(
                "충청남도 아산시 음봉면 신수리 101", diagnosis
            )
        )

    def test_different_yangju_lot_number_starts_a_new_parcel(self):
        diagnosis = {
            "request": {"address": "경기도 양주시 만송동 691-5"},
            "parcel": {
                "pnu": "4163010700106910005",
                "jibun": "경기도 양주시 만송동 691-5",
            },
        }
        self.assertFalse(
            _same_parcel_address("경기도 양주시 만송동 693-1", diagnosis)
        )

    def test_mouse_click_marks_only_a_different_pnu_as_new(self):
        orchestrator = Orchestrator(client=None)
        orchestrator.diagnosis = {"parcel": {"pnu": "4163010700106910005"}}
        orchestrator.set_selected_parcel(
            lon=127.0,
            lat=37.0,
            pnu="4163010700106920002",
            from_mouse=True,
        )
        self.assertTrue(orchestrator._selection_changed)
        self.assertIsNone(orchestrator.diagnosis)
        orchestrator.set_selected_parcel(
            lon=127.0,
            lat=37.0,
            pnu="4163010700106920002",
            from_mouse=True,
        )
        self.assertFalse(orchestrator._selection_changed)

    def test_mouse_click_restores_cached_diagnosis_and_messages(self):
        orchestrator = Orchestrator(client=None)
        old_pnu = "4163010700106910005"
        clicked_pnu = "4163010700106920002"
        orchestrator.diagnosis = {"parcel": {"pnu": old_pnu}}
        orchestrator._diagnosis_by_pnu[clicked_pnu] = {
            "parcel": {"pnu": clicked_pnu}
        }
        orchestrator._messages_by_pnu[clicked_pnu] = [
            {"role": "user", "content": "B필지 첫 질문"}
        ]
        orchestrator._diag_shown_by_pnu[clicked_pnu] = True

        orchestrator.set_selected_parcel(
            lon=127.0,
            lat=37.0,
            pnu=clicked_pnu,
            from_mouse=True,
        )

        self.assertFalse(orchestrator._selection_changed)
        self.assertEqual(orchestrator.diagnosis["parcel"]["pnu"], clicked_pnu)
        self.assertEqual(
            orchestrator.messages,
            [{"role": "user", "content": "B필지 첫 질문"}],
        )
        self.assertTrue(orchestrator._diag_shown)

    def test_a_b_a_b_parcel_context_rule(self):
        orchestrator = Orchestrator(client=None)
        a = "4420035023103440001"
        b = "4420035023103470000"

        orchestrator.diagnosis = {"parcel": {"pnu": a}, "summary": "A 진단"}
        orchestrator.messages = [{"role": "user", "content": "A 첫 질문"}]
        orchestrator._context_by_pnu[a] = {
            "active_building_use": "창고시설",
            "last_subject": "A의 도로 조건",
        }
        orchestrator._diag_shown = True
        orchestrator._diagnosis_by_pnu[a] = orchestrator.diagnosis

        # 처음 보는 B는 새 진단이다.
        orchestrator.set_selected_parcel(
            lon=126.97, lat=36.85, pnu=b, from_mouse=True
        )
        self.assertTrue(orchestrator._selection_changed)
        self.assertIsNone(orchestrator.diagnosis)

        # B 진단·후속 맥락을 만든다.
        orchestrator.diagnosis = {"parcel": {"pnu": b}, "summary": "B 진단"}
        orchestrator.messages = [{"role": "user", "content": "B 첫 질문"}]
        orchestrator._context_by_pnu[b] = {
            "active_building_use": "단독주택",
            "last_subject": "B의 이격거리",
        }
        orchestrator._diag_shown = True
        orchestrator._diagnosis_by_pnu[b] = orchestrator.diagnosis

        # A로 돌아오면 A 후속 상태가 복원된다.
        orchestrator.set_selected_parcel(
            lon=126.96, lat=36.84, pnu=a, from_mouse=True
        )
        self.assertFalse(orchestrator._selection_changed)
        self.assertEqual(orchestrator.diagnosis["summary"], "A 진단")
        self.assertEqual(orchestrator.messages[0]["content"], "A 첫 질문")
        self.assertEqual(
            orchestrator.conversation_context()["active_building_use"],
            "창고시설",
        )
        self.assertEqual(
            orchestrator.conversation_context()["last_subject"],
            "A의 도로 조건",
        )

        # 다시 B로 가면 B 후속 상태가 복원된다.
        orchestrator.set_selected_parcel(
            lon=126.97, lat=36.85, pnu=b, from_mouse=True
        )
        self.assertFalse(orchestrator._selection_changed)
        self.assertEqual(orchestrator.diagnosis["summary"], "B 진단")
        self.assertEqual(orchestrator.messages[0]["content"], "B 첫 질문")
        self.assertEqual(
            orchestrator.conversation_context()["active_building_use"],
            "단독주택",
        )
        self.assertEqual(
            orchestrator.conversation_context()["last_subject"],
            "B의 이격거리",
        )

    def test_timeout_snapshot_restores_parcel_state_and_question_context(self):
        orchestrator = Orchestrator(client=None)
        pnu = "4420035023103470000"
        orchestrator.diagnosis = {"parcel": {"pnu": pnu}, "summary": "원래 진단"}
        orchestrator.selected_parcel = {"pnu": pnu, "lon": 126.97, "lat": 36.85}
        orchestrator.messages = [{"role": "user", "content": "원래 질문"}]
        orchestrator._diagnosis_by_pnu[pnu] = orchestrator.diagnosis
        snapshot = orchestrator.snapshot_state()

        orchestrator.diagnosis = {"parcel": {"pnu": "잘못된 PNU"}}
        orchestrator.selected_parcel = {"pnu": "잘못된 PNU"}
        orchestrator.messages.append({"role": "user", "content": "중복 질문"})
        orchestrator._diagnosis_by_pnu.clear()

        orchestrator.restore_state(snapshot)
        self.assertEqual(orchestrator.diagnosis["summary"], "원래 진단")
        self.assertEqual(orchestrator.selected_parcel["pnu"], pnu)
        self.assertEqual(orchestrator.messages, [{"role": "user", "content": "원래 질문"}])
        self.assertIn(pnu, orchestrator._diagnosis_by_pnu)


if __name__ == "__main__":
    unittest.main()
