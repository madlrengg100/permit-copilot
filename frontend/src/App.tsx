import { useEffect, useRef, useState } from "react";
import { ChatPanel, type ChatMessage } from "./components/ChatPanel";
import { MapCanvas } from "./components/MapCanvas";
import {
  fetchConfig,
  fetchSetbackForUse,
  searchAddresses,
  setSessionParcelSelection,
  streamChat,
} from "./lib/api";
import type { HousingModelType, MapBridge, MapCommand } from "./lib/mapBridge";

/**
 * 세션 ID.
 *
 * crypto.randomUUID() 는 보안 컨텍스트(HTTPS 또는 localhost)에서만 존재한다.
 * http://<외부IP>:5173 처럼 평문 HTTP + 비 localhost 로 접속하면 undefined 라
 * 모듈 평가 중 TypeError 가 나고 React 가 통째로 마운트되지 않는다.
 * (증상: 탭 제목만 뜨고 화면이 백지)
 */
function makeSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const SESSION_ID = makeSessionId();

// 사전진단 도구 이름 -> 사용자에게 보여줄 진행 상황
const STEP_LABEL: Record<string, string> = {
  extract_request: "질의에서 주소·용도 파악 중",
  geocode_address: "주소를 좌표로 변환 중",
  get_parcel: "필지 정보 조회 중",
  get_land_use: "용도지역·지구 확인 중",
  check_zone_overlap: "용도지역 경계 걸침 확인 중",
  check_land_conversion: "농지·산지 전용 규제 확인 중",
  check_existing_buildings: "기존 건축물대장 조회 중",
  check_road_access: "도로 접함(접도) 확인 중",
  screen_disaster_environment_heritage: "재해·환경·국가유산 규제 확인 중",
  lookup_zoning: "법령 규제 검토 중",
  calc_massing: "건폐율·용적률 기준 건축 가능 규모 산출 중",
  verify_legal_sources: "국가법령정보센터 원문 확인 중",
  cite_ordinance_evidence: "관할 조례 근거 조문 검색 중",
};

const TOOL_LABEL: Record<string, string> = {
  prediagnose: "사전진단 에이전트 실행",
  render_on_map: "지도에 반영",
  restudy_massing: "건축 가능 규모 재산출",
};

// 실무 표기: 면적은 '평(㎡)' 순서로. 1평 = 3.3058㎡.
const PYEONG_M2 = 3.3058;
function fmtArea(m2?: number | null): string {
  if (m2 == null) return "—";
  return `${Math.round(m2 / PYEONG_M2).toLocaleString()}평(${Math.round(m2).toLocaleString()}㎡)`;
}

// 지목 부호(한 글자) -> 사용자 표기. 전·답·대는 통칭을 괄호로, 나머지는 정식명.
const JIMOK_LABEL: Record<string, string> = {
  전: "전(밭)", 답: "답(논)", 대: "대(대지)", 임: "임야", 과: "과수원",
  목: "목장용지", 광: "광천지", 염: "염전", 장: "공장용지", 학: "학교용지",
  차: "주차장", 주: "주유소용지", 창: "창고용지", 도: "도로", 철: "철도용지",
  제: "제방", 천: "하천", 구: "구거", 유: "유지", 양: "양어장", 수: "수도용지",
  공: "공원", 체: "체육용지", 원: "유원지", 종: "종교용지", 사: "사적지",
  묘: "묘지", 잡: "잡종지",
};
function jimokLabel(code?: string | null): string {
  if (!code) return "—";
  return JIMOK_LABEL[code] ?? code;
}

function resolveRelativeParcelAddress(text: string, currentAddress?: string): string {
  if (!currentAddress) return text;
  if (/(?:[가-힣0-9]+(?:읍|면|동|리)\s+)(?:산\s*)?\d+(?:-\d+)?/.test(text)) {
    return text;
  }
  const relative = text.match(
    /(^|\s)((?:산\s*)\d+(?:-\d+)?|\d+(?:-\d+)?\s*(?:번지|필지))(?=\s|$)/,
  );
  if (!relative) return text;
  const locality = currentAddress.replace(
    /\s+(?:산\s*)?\d+(?:-\d+)?\s*$/,
    "",
  ).trim();
  if (!locality || locality === currentAddress.trim()) return text;
  const lot = relative[2].replace(/\s*(?:번지|필지)\s*$/, "").trim();
  return text.replace(relative[2], `${locality} ${lot}`);
}

const USE_MODEL_STYLE: Record<string, HousingModelType> = {
  "단독주택": "detached",
  "공동주택": "lowrise",
  "제1종근린생활시설": "commercial",
  "제2종근린생활시설": "commercial",
  "판매시설": "commercial",
  "공장": "factory",
  "창고시설": "warehouse",
};

export default function App() {
  const [vworldKey, setVworldKey] = useState<string | null>(null);
  const [mockMode, setMockMode] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [commands, setCommands] = useState<MapCommand[]>([]);
  const [panel, setPanel] = useState<any>(null);
  // 스트리밍 한 턴 안에서 show_panel 명령이 연속 도착할 수 있다. React state는
  // 다음 렌더 전까지 이전 값을 가리키므로, 필지 변경 판정에는 즉시 갱신되는 ref를 쓴다.
  // 그렇지 않으면 두 번째 show_panel도 새 필지로 오인해 방금 붙인 모델 버튼을 지운다.
  const panelRef = useRef<any>(null);
  const [busy, setBusy] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState<{
    lon: number;
    lat: number;
    key: number;
    address?: string;
    pnu?: string;
  } | null>(null);
  // 답변하기 전에 다른 필지를 여러 번 눌러도 같은 안내 메시지를 쌓지 않는다.
  // 좌표는 매 클릭마다 최신 필지로 갱신하고 안내 여부만 별도로 기억한다.
  const parcelPromptPendingRef = useRef(false);
  // 지도 클릭을 백엔드 세션에 저장하기 전에 질문이 먼저 도착하면 이전 PNU의
  // 후속 질문으로 오인될 수 있다. 마지막 선택 저장이 끝난 뒤 질문을 보낸다.
  const parcelSelectionSyncRef = useRef<Promise<void>>(Promise.resolve());
  // 모델별 이격·판정 요청은 비동기다. 모델을 끄거나 다른 필지/모델로 바꾼 뒤
  // 늦게 도착한 과거 응답이 현재 팝업 판정을 덮어쓰지 못하게 세대를 구분한다.
  const modelRequestGenerationRef = useRef(0);

  const [configError, setConfigError] = useState<string | null>(null);

  // 결과 패널을 건물 위에 띄우기 위한 상태.
  // 지도 위 한 지점의 화면 좌표를 카메라가 움직일 때마다 다시 계산한다.
  const [bridge, setBridge] = useState<MapBridge | null>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  // 결과 팝업 접힘 상태 (접으면 치수선·라벨도 숨긴다)
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  // 요청 시설이 개별 법령상 불가할 때 팝업에 띄우는 빨간 경고 (예: '움막' 건축불가)
  const [verdictWarning, setVerdictWarning] = useState<{ label: string; reason?: string; kind?: string } | null>(
    null,
  );
  // 걸침 필지의 용도지역 조각 범례 (색 -> 지역명·비율). 걸침일 때만 표시.
  const [zoneLegend, setZoneLegend] = useState<
    Array<{ zone: string; share_pct: number; area_m2: number; color: string }> | null
  >(null);
  const [restrictionLegend, setRestrictionLegend] = useState<{
    title: string;
    note?: string;
    pieces: Array<{ label: string; share_pct: number | null; area_m2: number | null; color: string; note?: string }>;
  } | null>(null);

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setVworldKey(c.vworld_key);
        setMockMode(c.mock_mode);
      })
      // 조회가 실패하면 vworldKey 가 null 로 남아 화면이 계속 비어 보인다.
      // 원인을 화면에 띄운다.
      .catch((err) => setConfigError(String(err)));
  }, []);

  useEffect(() => {
    panelRef.current = panel;
  }, [panel]);

  useEffect(() => {
    if (!bridge || !panel?.anchor) {
      setAnchor(null);
      return;
    }
    const { lon, lat, height } = panel.anchor;
    const update = () => {
      const next = bridge.toScreenAboveGround(lon, lat, height);
      setAnchor((current) => {
        if (!current || !next) return next;
        // 같은 화면 좌표를 매 프레임 다시 저장하지 않아 불필요한 전체 렌더를 막는다.
        return Math.abs(current.x - next.x) < 0.25 && Math.abs(current.y - next.y) < 0.25
          ? current
          : next;
      });
    };
    update();
    // 카메라가 움직이면 패널도 건물을 따라간다
    return bridge.onCameraChange(update);
  }, [bridge, panel]);

  // 공간 수치는 지도 위 치수선·면적 라벨(show_dimensions 명령, MapBridge가 처리)로
  // 표시한다. 예전의 검은 텍스트 박스(showSiteNotes)는 건물을 가려 제거했다.

  async function showUseModel(useName: string) {
    const type = USE_MODEL_STYLE[useName];
    if (!type) {
      setMessages((current) => [...current, {
        role: "status",
        text: `⚠ ${useName} 용도의 3D 표시 형식이 등록되지 않았습니다.`,
      }]);
      return;
    }
    try {
      const requestGeneration = ++modelRequestGenerationRef.current;
      bridge?.showHousingModel(type);
      const result = await fetchSetbackForUse(SESSION_ID, useName);
      if (requestGeneration !== modelRequestGenerationRef.current) return;
      if (!result?.ok) {
        throw new Error("용도별 이격 계산 결과를 받지 못했습니다.");
      }
      if (Array.isArray(result.map_commands) && result.map_commands.length) {
        setCommands((current) => [...current, ...(result.map_commands as MapCommand[])]);
      }
      setPanel((current: any) => current ? {
        ...current,
        building_use: useName,
        ...(result.verdict ? { verdict: result.verdict } : {}),
        ...(result.verdict_label ? { verdict_label: result.verdict_label } : {}),
        ...(result.verdict_color ? { color: result.verdict_color } : {}),
        site_constraints: {
          ...(current.site_constraints ?? {}),
          front_setback_m: Number(result.front_setback_m ?? 0),
          adjacent_setback_m: Number(result.adjacent_setback_m ?? 0),
          north_setback_m: Number(result.north_setback_m ?? 0),
          setback_rule: {
            ...(current.site_constraints?.setback_rule ?? {}),
            status: result.status,
            source: result.source,
            note: result.note,
          },
        },
      } : current);
    } catch (error) {
      setMessages((current) => [...current, {
        role: "status",
        text: `⚠ ${useName} 모델 표시 실패: ${error instanceof Error ? error.message : String(error)}`,
      }]);
    }
  }

  async function send(
    text: string,
    requestText = text,
    chatContext?: Parameters<typeof streamChat>[2],
  ) {
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);

    // 자연어 안의 지번은 LLM이 지역을 추측하기 전에 VWorld로 먼저 확정한다.
    // "초평동 157-2에 집", "만송동 703으로 이동"처럼 시·군이 생략돼도
    // 정확 일치 1건이면 전체 주소로 보강하고, 여러 건이면 사용자가 고르게 한다.
    const fullParcelMatch = requestText.match(
      /((?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)+(?:산\s*)?\d+(?:-\d+)?)/,
    );
    const parcelMatch = requestText.match(
      /([가-힣0-9]+(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?)/,
    );
    if (parcelMatch) {
      const ambiguous = parcelMatch[1];
      // 시·군·읍·면까지 사용자가 명시했으면 짧은 '온수리 100' 전국 검색으로
      // 축약하지 않는다. 검색 상위 결과에 다른 지역만 남을 경우 그 한 곳을
      // 자동 확정하던 오류를 막고, 입력한 전체 주소를 그대로 조회한다.
      const addressQuery = fullParcelMatch?.[1] ?? ambiguous;
      try {
        const candidates = await searchAddresses(addressQuery);
        const locality = ambiguous.match(/([가-힣0-9]+(?:읍|면|동|리))/)?.[1] ?? "";
        const lot = ambiguous.match(/((?:산\s*)?\d+(?:-\d+)?)$/)?.[1].replace(/\s/g, "") ?? "";
        const exact = candidates.filter((candidate) => {
          const address = `${candidate.parcel} ${candidate.address}`;
          const candidateLot =
            (candidate.parcel || candidate.address)
              .match(/((?:산\s*)?\d+(?:-\d+)?)\s*$/)?.[1]
              .replace(/\s/g, "") ?? "";
          // 시·군이 생략된 "신수리 100"은 전국의 100번뿐 아니라
          // 100-1·100-2 같은 분할 필지도 후보로 보여준다. 정확히 100인 한 건을
          // 임의 확정하면 다른 시·도의 같은 리 또는 실제 찾던 분할 필지를 놓친다.
          const sameLotFamily = !lot
            || candidateLot === lot
            || candidateLot.startsWith(`${lot}-`);
          return (!locality || address.includes(locality)) && sameLotFamily;
        });
        // 사용자가 후보를 눌러 시·군까지 포함한 전체 지번을 보낸 경우에는
        // 같은 '현리 435-8' 전국 후보를 다시 묻지 않고 그 전체 주소를 확정한다.
        const fullAddressMatches = exact.filter((candidate) => {
          const full = candidate.parcel || candidate.address;
          return Boolean(full && requestText.includes(full));
        });
        const resolvedExact =
          fullAddressMatches.length === 1 ? fullAddressMatches : exact;
        const remainder = requestText.replace(addressQuery, "").trim();
        if (resolvedExact.length === 1) {
          const resolved = resolvedExact[0];
          requestText = `${resolved.parcel || resolved.address} ${remainder}`.trim();
          // 주소가 하나로 확정된 단순 이동 요청은 SSE 지도 명령만 기다리지 않고
          // 프론트에서도 즉시 이동한다. 모델 답변은 성공했지만 스트림의 map_commands
          // 적용이 지연·유실되어 현재 지역에 그대로 남는 상황을 막는 안전장치다.
          if (/이동|가\s*줘|찾아\s*줘|보여\s*줘/.test(text)) {
            bridge?.moveTo(resolved.lon, resolved.lat, 1200, 50);
          }
        } else {
          const options = resolvedExact.length > 0 ? resolvedExact : candidates;
          setMessages((m) => [...m, {
            role: "assistant",
            text: options.length
              ? `‘${ambiguous}’과 일치하는 주소가 여러 곳입니다. 정확한 필지를 선택해 주세요.`
              : `‘${ambiguous}’ 주소를 찾지 못했습니다. 시·군·구를 포함해 말씀해 주세요.`,
            options: options.map((candidate) => ({
              label: candidate.parcel || candidate.address,
              detail: candidate.road ? `도로명 ${candidate.road}` : undefined,
              value: `${candidate.parcel || candidate.address} ${remainder}`.trim(),
            })),
          }]);
          setBusy(false);
          return;
        }
      } catch (addressError) {
        setMessages((m) => [...m, {
          role: "status",
          text: `⚠ 주소 확인 실패: ${addressError instanceof Error ? addressError.message : String(addressError)}`,
        }]);
        setBusy(false);
        return;
      }
    }

    // 시·군·구가 없는 짧은 도로명 주소는 모델이 임의 지역을 붙이기 전에
    // 전국 주소 후보를 먼저 보여준다. 후보 선택 후의 전체 주소는 지역명이
    // 포함되므로 이 분기로 다시 들어오지 않고 곧바로 진단된다.
    const shortRoad = requestText.match(/([가-힣0-9·]+(?:대로|로|길)\s+\d+(?:-\d+)?)/);
    const roadPrefix = shortRoad ? text.slice(0, shortRoad.index ?? 0) : "";
    const hasRegion = /(특별시|광역시|특별자치시|특별자치도|[가-힣]+도|[가-힣]+시|[가-힣]+군|[가-힣]+구)/.test(roadPrefix);
    if (shortRoad && !hasRegion) {
      const ambiguous = shortRoad[1];
      try {
        const candidates = await searchAddresses(ambiguous);
        const remainder = requestText.replace(ambiguous, "").trim();
        if (candidates.length === 1) {
          // 후보가 하나뿐이면 굳이 물어보지 않고 그 주소로 바로 진단한다.
          // (원문 사용자 메시지는 그대로 두고, 백엔드로는 시·군 포함 전체 주소를 보낸다.)
          const resolved = candidates[0];
          requestText = `${resolved.address} ${remainder}`.trim();
          if (/이동|가\s*줘|찾아\s*줘|보여\s*줘/.test(text)) {
            bridge?.moveTo(resolved.lon, resolved.lat, 1200, 50);
          }
        } else if (candidates.length === 0) {
          setMessages((m) => [...m, {
            role: "assistant",
            text: `‘${ambiguous}’과 일치하는 주소를 찾지 못했습니다. 시·군·구를 함께 입력해 주세요.`,
          }]);
          setBusy(false);
          return;
        } else {
          // 후보가 둘 이상일 때만 어느 지역인지 골라달라고 한다.
          setMessages((m) => [...m, {
            role: "assistant",
            text: `‘${ambiguous}’은 여러 지역에 있어요. 어느 곳인지 선택해 주세요.`,
            options: candidates.map((candidate) => ({
              label: candidate.road || candidate.address,
              detail: candidate.parcel && candidate.parcel !== candidate.road
                ? `지번 ${candidate.parcel}`
                : undefined,
              value: `${candidate.address} ${remainder}`.trim(),
            })),
          }]);
          setBusy(false);
          return;
        }
      } catch (addressError) {
        setMessages((m) => [...m, {
          role: "status",
          text: `⚠ 관련 주소 조회 실패: ${addressError instanceof Error ? addressError.message : String(addressError)}`,
        }]);
        setBusy(false);
        return;
      }
    }

    try {
      for await (const ev of streamChat(SESSION_ID, requestText, chatContext)) {
        if (ev.event === "message") {
          const responseText = String(ev.data.text ?? "");
          setMessages((m) => [
            ...m,
            { role: "assistant", text: responseText, options: ev.data.options },
          ]);
        } else if (ev.event === "tool_start") {
          const label = TOOL_LABEL[ev.data.tool] ?? ev.data.tool;
          setMessages((m) => [...m, { role: "status", text: `▸ ${label}` }]);
        } else if (ev.event === "diagnosis_step") {
          const label = STEP_LABEL[ev.data.step] ?? ev.data.step;
          setMessages((m) => [...m, { role: "status", text: `   · ${label}` }]);
        } else if (ev.event === "retry") {
          // 첫 실행에서 쌓인 진행 문구·부분 응답을 제거하고, 같은 사용자 질문
          // 아래에서 자동 재시작한다. 선택 필지와 지도 상태는 유지한다.
          setMessages((current) => {
            let lastUser = -1;
            for (let index = current.length - 1; index >= 0; index -= 1) {
              if (current[index].role === "user") {
                lastUser = index;
                break;
              }
            }
            const kept = lastUser >= 0 ? current.slice(0, lastUser + 1) : current;
            return [
              ...kept,
              {
                role: "status",
                text: "↻ 응답 시간이 초과되어 같은 질문을 자동으로 다시 실행합니다.",
              },
            ];
          });
        } else if (ev.event === "map_commands") {
          const cmds: MapCommand[] = ev.data.commands;
          if (cmds.some((command) =>
            command.type === "clear_mass"
            || command.type === "hide_building_shape"
            || command.type === "show_building_footprint"
            || command.type === "show_building_shape"
            || command.type === "show_lod1"
          )) {
            modelRequestGenerationRef.current += 1;
          }
          setCommands((c) => [...c, ...cmds]);
          // 주소 이동·새 진단이 성공하면 그 좌표를 현재 필지로 유지한다.
          // 한 번 질문했다고 선택을 지우지 않아 후속 대장·공시지가 질문도
          // 같은 필지를 계속 참조하게 한다.
          const fly = cmds.find(
            (c): c is Extract<MapCommand, { type: "fly_to" }> => c.type === "fly_to",
          );
          const p = cmds.find((c) => c.type === "show_panel");
          const panelContext = cmds.find(
            (c): c is Extract<MapCommand, { type: "set_panel_context" }> =>
              c.type === "set_panel_context",
          );
          if (fly) {
            if (!parcelPromptPendingRef.current) {
              setSelectedLocation((current) => ({
                lon: fly.lon,
                lat: fly.lat,
                address: p?.type === "show_panel" ? p.address : current?.address,
                pnu: p?.type === "show_panel" ? p.pnu : current?.pnu,
                key: Date.now(),
              }));
            }
          }
          // 자연어로 팝업 여닫기 (set_layers.panel) — 팝업 접기·펼치기 + 치수선 연동
          const layerCmd = cmds.find(
            (c): c is Extract<MapCommand, { type: "set_layers" }> => c.type === "set_layers",
          );
          if (layerCmd && typeof (layerCmd as any).panel === "boolean") {
            const collapse = !(layerCmd as any).panel; // panel=true(열기) → collapsed=false
            setPanelCollapsed(collapse);
            bridge?.setDimensionsVisible(!collapse);
          }
          // 요청 시설 개별 제한 경고 (예: '움막' 건축불가)
          const warn = cmds.find(
            (c): c is Extract<MapCommand, { type: "verdict_warning" }> =>
              c.type === "verdict_warning",
          );
          if (warn) {
            setVerdictWarning({ label: warn.label, reason: warn.reason });
            // 이미 채팅에 붙은 현재 필지의 모델 버튼도 불가 판정 뒤에는 남기지 않는다.
            setMessages((current) =>
              current.filter((message) =>
                !message.options?.some((option) =>
                  option.action?.startsWith("housing:")
                  || option.action?.startsWith("use-model:")
                )
              )
            );
          }

          if (p) {
            const previousPanel = panelRef.current;
            const changedParcel = Boolean(
              previousPanel?.address
              && p.address
              && previousPanel.address !== p.address
            );
            if (changedParcel) {
              // 채팅 기록은 남기되, 이전 필지의 모델 버튼은 현재 지도에 모델을
              // 올릴 수 있어 위험하다. 새 필지로 바뀌는 순간 오래된 모델 카드만 제거한다.
              setMessages((current) =>
                current.filter((message) =>
                  !message.options?.some((option) =>
                    option.action?.startsWith("housing:")
                    || option.action?.startsWith("use-model:")
                  )
                )
              );
            }
            // 같은 스트림의 다음 명령도 최신 필지를 보도록 state보다 먼저 갱신한다.
            panelRef.current = p;
            setPanel(p);
            setPanelCollapsed(false); // 새 진단은 펼친 상태로 시작
            setVerdictWarning(null); // 새 진단은 경고 초기화
          }
          if (panelContext) {
            setPanel((current: any) => {
              const next = current
                ? {
                    ...current,
                    building_use: panelContext.building_use,
                    // 검토 범위가 바뀌면 배지도 그 범위 판정으로 갱신(없으면 유지).
                    ...(panelContext.verdict ? {verdict: panelContext.verdict} : {}),
                    ...(panelContext.verdict_label
                      ? {verdict_label: panelContext.verdict_label}
                      : {}),
                    ...(panelContext.verdict_color
                      ? {color: panelContext.verdict_color}
                      : {}),
                  }
                : current;
              if (next) panelRef.current = next;
              return next;
            });
          }
          // 걸침 필지면 우측 범례를 채우고, 새 진단(clear_mass 포함)에 조각이
          // 없으면 이전 범례를 지운다 — 지도와 범례가 어긋나지 않게.
          const zp = cmds.find((c) => c.type === "show_zone_pieces");
          if (zp) {
            setZoneLegend(zp.pieces);
          } else if (cmds.some((c) => c.type === "clear_mass")) {
            setZoneLegend(null);
          }
          const rp = cmds.find((c) => c.type === "show_restriction_pieces");
          if (rp?.type === "show_restriction_pieces") {
            setRestrictionLegend({
              title: rp.title,
              note: rp.note,
              pieces: rp.pieces.map(({ label, share_pct, area_m2, color, note }) => ({
                label, share_pct, area_m2, color, note,
              })),
            });
          } else if (cmds.some((c) => c.type === "clear_mass")) {
            setRestrictionLegend(null);
          }
        } else if (ev.event === "error") {
          const errorText = String(ev.data.message ?? "");
          const match = errorText.match(/주소를 찾을 수 없습니다:\s*(.+?)(?:\n|$)/);
          if (match) {
            // 모델이 짧은 주소 앞에 임의 시·군·구를 붙였더라도(예: 의정부시)
            // 사용자가 실제로 입력한 도로명+건물번호를 후보 검색에 우선 쓴다.
            const roadInQuestion = text.match(/([가-힣0-9·]+(?:대로|로|길)\s+\d+(?:-\d+)?)/)?.[1];
            const ambiguous = (roadInQuestion || match[1]).trim();
            try {
              const candidates = await searchAddresses(ambiguous);
              const remainder = text.replace(ambiguous, "").trim();
              setMessages((m) => [...m, {
                role: "assistant",
                text: candidates.length
                  ? `‘${ambiguous}’ 관련 주소입니다. 정확한 주소를 선택해 주세요.`
                  : `‘${ambiguous}’과 일치하는 주소 후보를 찾지 못했습니다. 시·군·구를 함께 입력해 주세요.`,
                options: candidates.map((candidate) => ({
                  label: candidate.road || candidate.address,
                  detail: candidate.parcel && candidate.parcel !== candidate.road
                    ? `지번 ${candidate.parcel}`
                    : undefined,
                  value: `${candidate.address} ${remainder}`.trim(),
                })),
              }]);
            } catch (addressError) {
              setMessages((m) => [...m, {
                role: "status",
                text: `⚠ 관련 주소 조회 실패: ${addressError instanceof Error ? addressError.message : String(addressError)}`,
              }]);
            }
          } else {
            setMessages((m) => [...m, { role: "status", text: `⚠ ${errorText}` }]);
          }
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "status", text: `⚠ ${err instanceof Error ? err.message : String(err)}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <ChatPanel
        messages={messages}
        busy={busy}
        draftSeed={
          selectedLocation
            ? {
                value: "선택 필지에 ",
                key: selectedLocation.key,
              }
            : null
        }
        onSend={(text) => {
          // "내 위치/현재 위치"는 순수 지도 이동 명령이다. 백엔드 LLM을 아예 거치지
          // 않고(대화 이력 때문에 사전진단으로 새는 것을 원천 차단) 지도 이동 명령을
          // 프론트에서 바로 큐에 넣어 위치만 이동한다.
          if (/(^|\s)(내\s*위치|현재\s*위치|현\s*위치|현위치)/.test(text)) {
            setSelectedLocation(null);
            setMessages((m) => [
              ...m,
              { role: "user", text },
              { role: "assistant", text: "현재 위치로 지도를 이동했습니다." },
            ]);
            setCommands((c) => [...c, { type: "run_tool", action: "my_location" }]);
            return;
          }
          const resolvedText = resolveRelativeParcelAddress(
            text,
            selectedLocation?.address,
          );
          const hasExplicitParcelAddress =
            /(?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)+(?:산\s*)?\d+(?:-\d+)?/.test(resolvedText);
          const changesLocation = /이동|가\s*줘|찾아\s*줘/.test(text);
          const useSelectedParcel =
            Boolean(selectedLocation) && !hasExplicitParcelAddress && !changesLocation;
          if (changesLocation) setSelectedLocation(null);

          void (async () => {
            if (useSelectedParcel && selectedLocation) {
              await parcelSelectionSyncRef.current;
              const continuation =
                Boolean(panelRef.current) && !parcelPromptPendingRef.current;
              parcelPromptPendingRef.current = false;
              await send(text, text, {
                selectedParcel: {
                  lon: selectedLocation.lon,
                  lat: selectedLocation.lat,
                  address: selectedLocation.address,
                  pnu: selectedLocation.pnu,
                },
                continuation,
              });
              return;
            }
            await send(text, resolvedText);
          })();
        }}
        onAction={(action) => {
          // 지역 추천 리스트 클릭 — 그 지번으로 개별 진단을 실행한다.
          // 형식: "diagnose:<용도>::<지번주소>"
          if (action.startsWith("diagnose:")) {
            const body = action.slice("diagnose:".length);
            const [use, address] = body.split("::");
            if (address) {
              const useText = use && use !== "건물" ? `에 ${use}` : "에 건물";
              void send(`${address}${useText} 지을 수 있어?`);
            }
            return;
          }
          if (action.startsWith("use-model:")) {
            void showUseModel(action.slice("use-model:".length));
            return;
          }
          if (!action.startsWith("housing:")) return;
          const type = action.slice("housing:".length) as HousingModelType;
          try {
            const requestGeneration = ++modelRequestGenerationRef.current;
            // 3D 렌더 실패가 아래 검토용도·이격 갱신을 막지 않게 분리한다.
            let ew: ReturnType<NonNullable<typeof bridge>["showHousingModel"]> = null;
            try {
              ew = bridge?.showHousingModel(type) ?? null;
            } catch {
              setMessages((c) => [...c, {
                role: "status",
                text: "⚠ 이 필지는 3D 모델을 세울 유효한 건축 매스가 없습니다(협소·배치 불가). 검토 용도·이격만 갱신합니다.",
              }]);
            }
            // 경사지면 절토·성토(토공량) 추정을 함께 안내한다. 모델을 여러 번/여러
            // 종류로 눌러도 같은 필지면 수치가 같아 똑같은 박스가 쌓인다. 토공
            // 안내는 항상 1개만 남기도록 기존 토공 메시지를 지우고 최신 것만 붙인다.
            const dropEarthwork = (list: ChatMessage[]) =>
              list.filter((msg) => !(typeof msg.text === "string" && msg.text.startsWith("⛰")));
            if (ew && (ew.max_cut_m >= 0.3 || ew.max_fill_m >= 0.3)) {
              const won = (n: number) => Math.round(n).toLocaleString();
              setMessages((current) => [...dropEarthwork(current), {
                role: "assistant",
                text:
                  `⛰ **토공(정지) 추정** — 계획고 약 표고 ${ew.platform_m.toFixed(1)}m(균형 절성토) 기준:\n` +
                  `- 절토(깎기) 약 **${won(ew.cut_m3)}㎥**, 성토(쌓기) 약 **${won(ew.fill_m3)}㎥**\n` +
                  `- 최대 깎기 약 ${ew.max_cut_m.toFixed(1)}m · 최대 쌓기 약 ${ew.max_fill_m.toFixed(1)}m\n` +
                  `- 지형데이터 기반 개략 추정입니다. 실제 토공량·옹벽·법면은 현황측량과 설계 계획고에 따라 달라지며, 절성토는 개발행위허가·경사도 심의 대상이 될 수 있습니다.`,
              }]);
            } else if (ew) {
              setMessages((current) => [...dropEarthwork(current), {
                role: "status",
                text: "⛰ 대상지는 거의 평지로, 절토·성토는 미미할 것으로 추정됩니다.",
              }]);
            }
            // 누른 모델(용도)의 '실제' 이격을 백엔드에서 계산해 받아온다(진단 용도와
            // 무관하게, 그 용도로 대지 안의 공지를 계산). 애매한 규칙 문구가 아니라
            // 계산된 수치(예: 전면 3m)를 그대로 보여준다.
            const USE_OF_MODEL: Record<HousingModelType, string> = {
              detached: "단독주택", lowrise: "공동주택", slim: "공동주택",
              factory: "공장", warehouse: "창고시설", commercial: "판매시설",
            };
            // 상가 외형은 판매시설·제1종·제2종근린생활시설이 함께 사용한다.
            // 현재 필지에서 실제로 가능/조건부인 용도를 골라야, 허용된 근생 모델을
            // 눌렀는데 금지된 '판매시설'로 재판정되는 일이 없다.
            const commercialUses = [
              ...(panel?.zone_use_overview?.allowed ?? []),
              ...(panel?.zone_use_overview?.conditional ?? []),
            ];
            const useName =
              type === "commercial"
                ? ["판매시설", "제1종근린생활시설", "제2종근린생활시설"]
                    .find((use) => commercialUses.includes(use))
                  ?? "판매시설"
                : USE_OF_MODEL[type];
            void (async () => {
              const r = await fetchSetbackForUse(SESSION_ID, useName);
              if (requestGeneration !== modelRequestGenerationRef.current) return;
              const label = `📐 **이격거리(건축선↔인접대지경계선) — ${useName} 용도의 대지 안의 공지(이격)**`;
              let text: string;
              if (!r || !r.ok) {
                text = `${label} · 먼저 이 필지를 진단해 주세요.`;
              } else {
                // 이 용도의 이격선(전면/인접 건축선 등)을 지도에 다시 그린다.
                if (Array.isArray(r.map_commands) && r.map_commands.length) {
                  setCommands((c) => [...c, ...(r.map_commands as MapCommand[])]);
                }
                // 팝업 '검토 용도'와 판정 배지를 클릭한 용도 기준으로 바꾼다.
                setPanel((p: any) =>
                  p
                    ? {
                        ...p,
                        building_use: useName,
                        ...(r.verdict ? { verdict: r.verdict } : {}),
                        ...(r.verdict_label ? { verdict_label: r.verdict_label } : {}),
                        ...(r.verdict_color ? { color: r.verdict_color } : {}),
                      }
                    : p,
                );
                const f = Number(r.front_setback_m ?? 0);
                const a = Number(r.adjacent_setback_m ?? 0);
                const n = Number(r.north_setback_m ?? 0);
                const src = r.source ? ` (${r.source})` : "";
                // 이격은 '용도+규모+용도지역' 조건에 따라 정해진다는 걸 문구로 드러낸다.
                const gross = Number(r.gross_floor_area_m2 ?? 0);
                const zone = r.zone ?? "";
                const cond = `이 용도·${
                  gross > 0 ? `규모(연면적 약 ${Math.round(gross).toLocaleString()}㎡)·` : "규모·"
                }${zone || "지역"} 조건에서는`;
                const parts: string[] = [];
                if (f > 0) parts.push(`전면 ${f}m`);
                if (a > 0) parts.push(`인접 ${a}m`);
                if (n > 0) parts.push(`정북일조 ${n}m`);
                if (r.status === "NEEDS_SUBTYPE") {
                  text = `${label} · ${cond} 세부 유형에 따라 이격이 달라집니다 — ${r.note ?? ""}`.trim() + src;
                } else if (parts.length) {
                  text = `${label} · ${cond} ${parts.join(" · ")}가 적용됩니다 — 지도에 건축선으로 표시했습니다.${src}`;
                } else if (r.status === "NOT_COLLECTED") {
                  text = `${label} · 관할 건축조례 '대지 안의 공지' 별표 미수집으로 이격을 확정하지 못했습니다(0m). 관할 건축조례 별표 원문 확인이 필요합니다.`;
                } else {
                  text = `${label} · 이 용도·규모·지역은 대지 안의 공지 대상이 아니어서 이격 0m입니다.${src}`;
                }
              }
              setMessages((current) => [
                ...current.filter((msg) => !(typeof msg.text === "string" && msg.text.startsWith("📐"))),
                { role: "assistant", text },
              ]);
            })();
          } catch (error) {
            setMessages((current) => [...current, {
              role: "status",
              text: `⚠ 추천 모델 표시 실패: ${error instanceof Error ? error.message : String(error)}`,
            }]);
          }
        }}
      />

      <div className="map-area">
        {configError ? (
          <div className="map-placeholder">
            <div className="setup-guide">
              <h2>백엔드에 연결하지 못했습니다</h2>
              <p>
                <code>/api/config</code> 조회 실패: {configError}
              </p>
              <p className="setup-note">
                백엔드(포트 8000)가 떠 있는지, vite 프록시 설정이 맞는지 확인하세요.
              </p>
            </div>
          </div>
        ) : vworldKey ? (
          <MapCanvas
            vworldKey={vworldKey}
            commands={commands}
            onReady={setBridge}
            onMapSelect={(lon, lat, address, pnu) => {
              const activePnu =
                String(panelRef.current?.pnu ?? selectedLocation?.pnu ?? "");
              const sameParcel = Boolean(pnu && activePnu && pnu === activePnu);
              parcelSelectionSyncRef.current = setSessionParcelSelection(
                SESSION_ID,
                { lon, lat, address, pnu },
              )
                .then(() => undefined)
                .catch((error) => {
                  setMessages((current) => [
                    ...current,
                    {
                      role: "status",
                      text: `⚠ 선택 필지 세션 저장 실패: ${
                        error instanceof Error ? error.message : String(error)
                      }`,
                    },
                  ]);
                });
              if (sameParcel) {
                // 같은 PNU를 다시 눌렀다면 새 필지가 아니다. 기존 패널·모델·
                // 후속 상태를 그대로 유지하고 좌표만 최신값으로 갱신한다.
                parcelPromptPendingRef.current = false;
                setSelectedLocation((current) => ({
                  lon,
                  lat,
                  address: address || current?.address,
                  pnu,
                  key: current?.key ?? Date.now(),
                }));
                // 다른 필지처럼 어떤 필지를 눌렀는지 주소는 확인해 준다. 단 이미 분석
                // 중인 필지이므로 '무슨 건물' 초기화 문구는 붙이지 않는다(상태 유지).
                const shownAddress = address || selectedLocation?.address || "";
                if (shownAddress) {
                  const note = `**${shownAddress}** 필지입니다. (현재 분석 중인 필지)`;
                  setMessages((current) => [
                    ...current.filter(
                      (message) => !(
                        message.role === "status"
                        && message.text.includes("현재 분석 중인 필지")
                      ),
                    ),
                    { role: "status", text: note },
                  ]);
                }
                return;
              }
              // 지도에서 다른 필지를 고르는 즉시 이전 필지 모델 버튼을 비활성화한다.
              setMessages((current) =>
                current.filter((message) =>
                  !message.options?.some((option) =>
                    option.action?.startsWith("housing:")
                    || option.action?.startsWith("use-model:")
                  )
                )
              );
              // 답변 생성 중에도 지도 클릭 좌표는 반드시 새 선택으로 저장한다.
              // 예전에는 busy일 때 좌표만 버리고 지도 경계는 바뀌어, 화면은 새
              // 필지인데 다음 질문은 이전 진단 주소를 쓰는 불일치가 생겼다.
              setSelectedLocation({ lon, lat, address, pnu, key: Date.now() });
              parcelPromptPendingRef.current = true;
              // 안내는 여러 개 쌓지 않되, 다른 필지를 다시 누르면 기존 안내를
              // 제거하고 맨 아래로 옮겨 항상 입력창 바로 위에서 보이게 한다.
              const prompt = `**${address}** 필지를 선택했습니다. 무슨 건물을 짓고 싶은가요?`;
              setMessages((current) => [
                ...current.filter(
                  (message) => !(
                    message.role === "assistant"
                    && message.text.includes("필지를 선택했습니다. 무슨 건물을 짓고 싶은가요?")
                  ),
                ),
                { role: "assistant", text: prompt },
              ]);
            }}
          />
        ) : (
          // 키가 없으면 지도를 띄울 방법이 없다. "초기화 중"이라고 쓰면
          // 기다리면 될 것처럼 읽히므로 무엇이 없는지 그대로 밝힌다.
          <div className="map-placeholder">
            <div className="setup-guide">
              <h2>지도를 표시할 수 없습니다</h2>
              <p>
                <code>VWORLD_KEY</code> 가 설정되지 않았습니다. 3D 지도와 가상 건물
                규모 표시는 이 키가 있어야 동작합니다.
              </p>
              <ol>
                <li>
                  <a href="https://www.vworld.kr" target="_blank" rel="noreferrer">
                    vworld.kr
                  </a>{" "}
                  에서 인증키 발급 (오픈API → 3D 지도)
                </li>
                <li>
                  인증 도메인에 <code>localhost</code> 등록
                </li>
                <li>
                  백엔드 재시작: <code>VWORLD_KEY=발급키 uvicorn app.main:app</code>
                </li>
              </ol>
              <p className="setup-note">
                키 없이도 좌측 채팅에서 규제 판정은 확인할 수 있습니다
                {mockMode && " (필지는 목 데이터)"}.
              </p>
            </div>
          </div>
        )}

        {mockMode && vworldKey && (
          <div className="mock-badge">VWORLD_KEY 미설정 — 목 데이터로 동작 중</div>
        )}

        {panel && (
          <ResultPanel
            panel={panel}
            anchor={anchor}
            warning={verdictWarning}
            collapsed={panelCollapsed}
            onToggle={() => {
              const next = !panelCollapsed;
              setPanelCollapsed(next);
              // 접으면 치수선·라벨을 숨기고, 펼치면 다시 그린다.
              bridge?.setDimensionsVisible(!next);
            }}
          />
        )}

        {/* 걸침 필지 범례 — 지도에 깔린 용도지역 조각 색의 의미를 설명한다.
            걸침이 아닌 필지에서는 나타나지 않는다. */}
        {zoneLegend && zoneLegend.length > 0 && (
          <div className="zone-legend">
            <div className="zone-legend-title">용도지역 걸침구분</div>
            {zoneLegend.map((z) => (
              <div key={z.zone} className="zone-legend-row">
                <span className="zone-legend-swatch" style={{ background: z.color }} />
                <span className="zone-legend-name">{z.zone}</span>
                <span className="zone-legend-pct">
                  {z.share_pct}% · {Math.round(z.area_m2).toLocaleString()}㎡
                </span>
              </div>
            ))}
            <div className="zone-legend-note">색 경계 = 용도지역 경계 (사전검토 참고용)</div>
          </div>
        )}
        {restrictionLegend && restrictionLegend.pieces.length > 0 && (
          <div className={`restriction-legend${zoneLegend?.length ? " with-zone" : ""}`}>
            <div className="zone-legend-title">{restrictionLegend.title}</div>
            {restrictionLegend.pieces.map((piece) => (
              <div key={`${piece.label}-${piece.share_pct}`} className="zone-legend-row" title={piece.note}>
                <span className="zone-legend-swatch" style={{ background: piece.color }} />
                <span className="zone-legend-name">{piece.label}</span>
                {piece.share_pct != null && (
                  <span className="zone-legend-pct">
                    {piece.share_pct}% · {Math.round(piece.area_m2 ?? 0).toLocaleString()}㎡
                  </span>
                )}
              </div>
            ))}
            <div className="zone-legend-note">
              {restrictionLegend.note ?? "환경·재해 중첩 (사전검토 참고용)"}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ResultPanel({
  panel,
  anchor,
  warning,
  collapsed,
  onToggle,
}: {
  panel: any;
  anchor: { x: number; y: number } | null;
  warning: { label: string; reason?: string } | null;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const m = panel.massing;
  const registeredBuildings = panel.existing_buildings?.buildings ?? [];
  const registeredMaxFloors = registeredBuildings.reduce(
    (max: number, building: any) => Math.max(max, Number(building.ground_floors) || 0),
    0,
  );
  const registeredTotalArea = registeredBuildings.reduce(
    (sum: number, building: any) => sum + (Number(building.total_area_m2) || 0),
    0,
  );
  const registeredApprovalDate = registeredBuildings.find(
    (building: any) => building.use_approval_date,
  )?.use_approval_date;
  const formattedApprovalDate =
    registeredApprovalDate?.length === 8
      ? `${registeredApprovalDate.slice(0, 4)}-${registeredApprovalDate.slice(4, 6)}-${registeredApprovalDate.slice(6)}`
      : registeredApprovalDate;
  const districts: string[] = panel.districts ?? [];
  const conversionCharge = panel.conversion_charge;
  const developmentCharge = panel.development_charge;
  const legalSources = panel.legal_sources?.sources ?? [];
  const siteConstraints = panel.site_constraints;
  const roadAccess = panel.road_access;
  // 이격 배지: status APPLIED 는 0m도 포함하므로, 실제 그려질 이격이 있을 때만
  // '반영'이라 한다(단독주택 0m인데 '반영'으로 표기되던 오해 방지).
  const hasSetback =
    (siteConstraints?.front_setback_m ?? 0) > 0 ||
    (siteConstraints?.adjacent_setback_m ?? 0) > 0 ||
    (siteConstraints?.north_setback_m ?? 0) > 0;
  const setbackBadge =
    siteConstraints?.setback_rule?.status === "NOT_COLLECTED"
      ? "이격 미수집(조례)"
      : hasSetback
        ? "이격거리 반영"
        : "이격 없음(0m)";
  // 주차 배지: 지상주차는 '개념 면적차감'이고 지도에 배치 모델을 그리지는 않는다.
  // '반영'이라 하면 모델이 있는 것으로 오해되므로 '면적차감'으로 정확히 표기한다.
  const parkingBadge =
    siteConstraints?.parking?.strategy_status === "APPLIED"
      ? "지상주차 면적차감"
      : siteConstraints?.parking?.strategy_status === "DESIGN_REQUIRED"
        ? "주차 별도 설계"
        : "주차 미반영";
  const roadBadge =
    roadAccess?.status === "NO_CADASTRAL_ROAD"
      ? "지적도상 도로 접촉 없음(맹지 가능성 있음)"
      : roadAccess?.status === "CADASTRAL_CONTACT"
        ? "도로폭·후퇴선 현황측량 필요"
        : "도로 접도 별도 확인";
  const compactSiteNote = siteConstraints
    ? [setbackBadge, parkingBadge, roadBadge].join(" · ")
    : "";

  // 건물의 화면 좌표를 얻기 전에는 우측 상단에 임시로 띄우지 않는다.
  // 위치가 계산되면 지붕 위 레이어 팝업으로만 표시한다.
  if (!anchor) return null;

  const style: React.CSSProperties = {
    left: anchor.x,
    top: anchor.y,
    right: "auto",
    transform: "translate(-50%, calc(-100% - 10px))",
  };

  // 접힘: 건물 위에 콩알만한 판정 배지만. 누르면 펼쳐지고 치수선도 다시 나온다.
  if (collapsed) {
    return (
      <button
        className="result-pill"
        style={style}
        onClick={onToggle}
        title="펼치기 (치수선·라벨 표시)"
      >
        <span className="verdict" style={{ background: panel.color }}>
          {panel.verdict_label}
        </span>
        <span className="result-pill-caret">▾ 펼치기</span>
      </button>
    );
  }

  return (
    <div className="result-panel" style={style}>
      <button className="result-collapse" onClick={onToggle} title="접기 (치수선·라벨 숨김)">
        ▲ 접기
      </button>
      {/* 위(우측 상단)뿐 아니라 아래 가운데 꼬리에도 접기 표시 — 건물 바로 위라 손이 가는 자리 */}
      <button className="result-tail-toggle" onClick={onToggle} title="접기 (치수선·라벨 숨김)">
        ▲ 접기
      </button>
      <div className="verdict-row">
        <div className="verdict" style={{ background: panel.color }}>
          {panel.verdict_label}
        </div>
        {/* 요청 시설이 개별 법령상 불가하면 판정 옆에 빨간 경고 (예: '움막' 건축불가) */}
        {warning && (
          <div className="verdict-warning" title={warning.reason || ""}>
            <strong>{warning.label}</strong>
            {warning.reason ? <span>{warning.reason}</span> : null}
          </div>
        )}
      </div>

      <div className="result-address">{panel.address}</div>

      <div className="result-columns">
        <section>
          {/* 필지 기준 = 이 필지가 무엇인가 (지역·지구·지목·검토 용도) */}
          <div className="result-section-title">필지 기준</div>
          <dl className="result-grid">
            <dt>용도지역</dt>
            <dd>{panel.zone || "—"}</dd>
            <dt>용도지구</dt>
            <dd title={districts.join(", ")}>
              {districts.length
                ? `${districts[0]}${districts.length > 1 ? ` 외 ${districts.length - 1}개` : ""}`
                : "지정 없음"}
            </dd>
            <dt>지목</dt>
            <dd>{jimokLabel(panel.jimok)}</dd>
            <dt>검토 용도</dt>
            <dd>{panel.building_use || "—"}</dd>
            {panel.jiga_won_per_m2 != null && (
              <>
                <dt>공시지가</dt>
                <dd>평당 {Math.round(panel.jiga_won_per_m2 * PYEONG_M2).toLocaleString()}원</dd>
              </>
            )}
            {panel.existing_buildings?.status === "FOUND" && (
              <>
                <dt>기존 건축물</dt>
                <dd
                  title={[
                    `대장 ${panel.existing_buildings.count ?? registeredBuildings.length}동`,
                    registeredMaxFloors > 0 ? `최고 ${registeredMaxFloors}층` : "",
                    registeredTotalArea > 0 ? `연면적 ${fmtArea(registeredTotalArea)}` : "",
                    formattedApprovalDate ? `사용승인 ${formattedApprovalDate}` : "",
                  ].filter(Boolean).join(" · ")}
                >
                  대장 {panel.existing_buildings.count ?? registeredBuildings.length}건
                  {registeredMaxFloors > 0 ? ` · 최고 ${registeredMaxFloors}층` : ""}
                </dd>
              </>
            )}
            {panel.existing_buildings?.status === "CLEAR" && (
              <>
                <dt>기존 건축물</dt>
                <dd>표제부 조회 없음</dd>
              </>
            )}
          </dl>
        </section>

        <section className="result-scale">
          {/* 가능 규모 = 규모 산출값 (대지면적 × 건폐율/용적률 = 건축면적/연면적/층수) */}
          <div className="result-section-title">가능 규모</div>
          <dl className="result-grid">
            <dt>대지면적</dt>
            <dd>{fmtArea(panel.site_area_m2)}</dd>
            {/* 건폐율·용적률 상한은 용도지역·필지 속성이라 특수·가설 시설(매스 없음)에도
                항상 표시한다. 매스에서 나오는 건축면적/연면적/층수만 매스가 있을 때 표시. */}
            <dt>건폐율 상한</dt>
            <dd>{panel.bcr_max_pct != null ? `${panel.bcr_max_pct}%` : "—"}</dd>
            <dt>용적률 상한</dt>
            <dd>{panel.far_max_pct != null ? `${panel.far_max_pct}%` : "—"}</dd>
            {m && (
              <>
                <dt>건축면적</dt>
                <dd>{fmtArea(m.building_area_m2)}</dd>
                {m.exceeds_far_limit ? (
                  <>
                    <dt>요청 용적률</dt>
                    <dd>{m.requested_far_pct}%</dd>
                  </>
                ) : (
                  <>
                    <dt>연면적</dt>
                    <dd>{fmtArea(m.gross_floor_area_m2)}</dd>
                    <dt>신축 추정 층수</dt>
                    <dd>
                      {m.floors}층
                      {m.top_floor_ratio > 0 && m.top_floor_ratio < 0.999
                        ? ` (최상층 ${Math.round(m.top_floor_ratio * 100)}%)`
                        : ""}
                      {/* 높이(약 9.9m)는 삐져나와 팝업에서 빼고 답변에서 다룬다 */}
                    </dd>
                  </>
                )}
              </>
            )}
          </dl>
        </section>
      </div>

      {/*
        지도 팝업은 판정·필지 기준·가능 규모만 남긴다.
        지형 참고·배치 제약·도로 접도 = '공간에서 확인할 수치'는 지도 건물 옆에
        라벨로 직접 띄운다(위 useEffect → bridge.showSiteNotes). 농지전용·부담금·
        재해/환경/국가유산·인허가 단계 등 서술형 진단은 왼쪽 답변이 담당한다.
        팝업이 건물을 덮어 지도가 안 보이던 문제 해결.
      */}
      <div className="result-evidence">
        {conversionCharge?.estimated_won != null && (
          <p className="result-note">
            {conversionCharge.label}: 약{" "}
            {Math.round(conversionCharge.estimated_won / 10_000).toLocaleString()}만원
            {conversionCharge.area_basis === "full_parcel_reference"
              ? " (전체 필지 전용 가정 참고 상한)"
              : ` (건축면적 ${Math.round(conversionCharge.area_m2 ?? 0).toLocaleString()}㎡ 전용 가정)`}
          </p>
        )}
        {developmentCharge?.applicable && (
          <p className="result-note">
            개발부담금 참고: 약{" "}
            {Math.round((developmentCharge.region_avg_per_case_won ?? 0) / 10_000).toLocaleString()}만원
            {" "}({developmentCharge.statistics_year}년 {developmentCharge.region || "전국"} 평균, 부과대상 토지면적{" "}
            {developmentCharge.area_requirement_m2?.toLocaleString()}㎡ 이상)
          </p>
        )}
        {developmentCharge && !developmentCharge.applicable && (
          <p className="result-note">
            개발부담금: 면적 요건 미달 (사업 대상 토지면적{" "}
            {Math.round(developmentCharge.assessed_area_m2 ?? panel.site_area_m2 ?? 0).toLocaleString()}㎡
            {" "}/ 부과대상 토지면적 {developmentCharge.area_requirement_m2?.toLocaleString()}㎡ 이상)
          </p>
        )}
        {compactSiteNote && <p className="result-note">{compactSiteNote}</p>}
        {panel.legal_basis && (
          <p className="result-basis">
            {panel.legal_basis.includes("조례") ? "적용 조례: " : ""}
            {panel.legal_basis}
          </p>
        )}
        {legalSources.length > 0 && (
          <p className="result-basis">
            국가법령정보센터 관련 현행 법령 {legalSources.length}건 연결 확인
          </p>
        )}
      </div>
    </div>
  );
}
