import { useEffect, useRef, useState } from "react";
import { ChatPanel, type ChatMessage } from "./components/ChatPanel";
import { MapCanvas } from "./components/MapCanvas";
import { fetchConfig, searchAddresses, streamChat } from "./lib/api";
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
  geocode_address: "주소를 좌표로 변환 중",
  get_parcel: "필지 정보 조회 중",
  get_land_use: "용도지역·지구 확인 중",
  check_zone_overlap: "용도지역 경계 걸침 확인 중",
  lookup_zoning: "법령 규제 검토 중",
  calc_massing: "건폐율·용적률 기준 건축 가능 규모 산출 중",
};

const TOOL_LABEL: Record<string, string> = {
  prediagnose: "사전진단 에이전트 실행",
  render_on_map: "지도에 반영",
  restudy_massing: "건축 가능 규모 재산출",
};

export default function App() {
  const [vworldKey, setVworldKey] = useState<string | null>(null);
  const [mockMode, setMockMode] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [commands, setCommands] = useState<MapCommand[]>([]);
  const [panel, setPanel] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState<{
    lon: number;
    lat: number;
    key: number;
  } | null>(null);
  // 답변하기 전에 다른 필지를 여러 번 눌러도 같은 안내 메시지를 쌓지 않는다.
  // 좌표는 매 클릭마다 최신 필지로 갱신하고 안내 여부만 별도로 기억한다.
  const parcelPromptPendingRef = useRef(false);

  const [configError, setConfigError] = useState<string | null>(null);

  // 결과 패널을 건물 위에 띄우기 위한 상태.
  // 지도 위 한 지점의 화면 좌표를 카메라가 움직일 때마다 다시 계산한다.
  const [bridge, setBridge] = useState<MapBridge | null>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  // 걸침 필지의 용도지역 조각 범례 (색 -> 지역명·비율). 걸침일 때만 표시.
  const [zoneLegend, setZoneLegend] = useState<
    Array<{ zone: string; share_pct: number; area_m2: number; color: string }> | null
  >(null);

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

  async function send(text: string, requestText = text) {
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);

    // 시·군·구가 없는 짧은 도로명 주소는 모델이 임의 지역을 붙이기 전에
    // 전국 주소 후보를 먼저 보여준다. 후보 선택 후의 전체 주소는 지역명이
    // 포함되므로 이 분기로 다시 들어오지 않고 곧바로 진단된다.
    const shortRoad = text.match(/([가-힣0-9·]+(?:대로|로|길)\s+\d+(?:-\d+)?)/);
    const roadPrefix = shortRoad ? text.slice(0, shortRoad.index ?? 0) : "";
    const hasRegion = /(특별시|광역시|특별자치시|특별자치도|[가-힣]+도|[가-힣]+시|[가-힣]+군|[가-힣]+구)/.test(roadPrefix);
    if (requestText === text && shortRoad && !hasRegion) {
      const ambiguous = shortRoad[1];
      try {
        const candidates = await searchAddresses(ambiguous);
        const remainder = text.replace(ambiguous, "").trim();
        setMessages((m) => [...m, {
          role: "assistant",
          text: candidates.length
            ? `‘${ambiguous}’ 관련 주소입니다. 진단할 주소를 선택해 주세요.`
            : `‘${ambiguous}’과 일치하는 주소를 찾지 못했습니다. 시·군·구를 함께 입력해 주세요.`,
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
      } finally {
        setBusy(false);
      }
      return;
    }

    try {
      for await (const ev of streamChat(SESSION_ID, requestText)) {
        if (ev.event === "message") {
          setMessages((m) => [...m, { role: "assistant", text: ev.data.text }]);
        } else if (ev.event === "tool_start") {
          const label = TOOL_LABEL[ev.data.tool] ?? ev.data.tool;
          setMessages((m) => [...m, { role: "status", text: `▸ ${label}` }]);
        } else if (ev.event === "diagnosis_step") {
          const label = STEP_LABEL[ev.data.step] ?? ev.data.step;
          setMessages((m) => [...m, { role: "status", text: `   · ${label}` }]);
        } else if (ev.event === "map_commands") {
          const cmds: MapCommand[] = ev.data.commands;
          setCommands((c) => [...c, ...cmds]);
          const p = cmds.find((c) => c.type === "show_panel");
          if (p) {
            setPanel(p);
            const overview = p.zone_use_overview ?? {};
            const housingUses = [
              ...(overview.allowed ?? []),
              ...(overview.conditional ?? []),
            ];
            const options: ChatMessage["options"] = [];
            if (housingUses.includes("단독주택")) {
              options.push({
                label: `${p.massing?.floors ?? "허용"}층 단독주택형`,
                detail: "법정 가능 층수 반영 · 주택 비례",
                action: "housing:detached",
              });
            }
            if (housingUses.includes("공동주택")) {
              options.push(
                { label: `${p.massing?.floors ?? "허용"}층 공동주택형`, detail: "건축 가능 영역 최대 활용", action: "housing:lowrise" },
                { label: `${p.massing?.floors ?? "허용"}층 슬림형`, detail: "허용 층수 전체 반영", action: "housing:slim" },
              );
            }
            if (options.length > 0 && p.massing && !p.massing.exceeds_far_limit) {
              setMessages((current) => [...current, {
                role: "assistant",
                text: "허용 용도에 맞는 추천 주택 모델입니다. 선택하면 지도상의 초록색 건축 가능 공간 안에 표시합니다.",
                options,
              }]);
            }
          }
          // 걸침 필지면 우측 범례를 채우고, 새 진단(clear_mass 포함)에 조각이
          // 없으면 이전 범례를 지운다 — 지도와 범례가 어긋나지 않게.
          const zp = cmds.find((c) => c.type === "show_zone_pieces");
          if (zp) {
            setZoneLegend(zp.pieces);
          } else if (cmds.some((c) => c.type === "clear_mass")) {
            setZoneLegend(null);
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
            ? { value: "선택한 필지에 ", key: selectedLocation.key }
            : null
        }
        onSend={(text) => {
          if (selectedLocation) {
            const { lon, lat } = selectedLocation;
            const answer = text.replace(/^선택한 필지에\s*/, "");
            setSelectedLocation(null);
            parcelPromptPendingRef.current = false;
            void send(
              text,
              `지도에서 선택한 위치(경도 ${lon.toFixed(7)}, 위도 ${lat.toFixed(7)})에서 ` +
                `사용자가 원하는 건축물 용도는 "${answer}"이다. 건축 가능 여부를 검토해줘`,
            );
          } else {
            void send(text);
          }
        }}
        onAction={(action) => {
          if (!action.startsWith("housing:")) return;
          const type = action.slice("housing:".length) as HousingModelType;
          try {
            bridge?.showHousingModel(type);
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
            onMapSelect={(lon, lat) => {
              if (busy) return;
              setSelectedLocation({ lon, lat, key: Date.now() });
              parcelPromptPendingRef.current = true;
              // 안내는 여러 개 쌓지 않되, 다른 필지를 다시 누르면 기존 안내를
              // 제거하고 맨 아래로 옮겨 항상 입력창 바로 위에서 보이게 한다.
              const prompt = "필지를 선택했습니다. 무슨 건물을 짓고 싶은가요?";
              setMessages((current) => [
                ...current.filter(
                  (message) => !(message.role === "assistant" && message.text === prompt),
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
          />
        )}

        {/* 걸침 필지 범례 — 지도에 깔린 용도지역 조각 색의 의미를 설명한다.
            걸침이 아닌 필지에서는 나타나지 않는다. */}
        {zoneLegend && zoneLegend.length > 0 && (
          <div className="zone-legend">
            <div className="zone-legend-title">용도지역 구분</div>
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
      </div>
    </div>
  );
}

function ResultPanel({
  panel,
  anchor,
}: {
  panel: any;
  anchor: { x: number; y: number } | null;
}) {
  const m = panel.massing;

  // 건물의 화면 좌표를 얻기 전에는 우측 상단에 임시로 띄우지 않는다.
  // 위치가 계산되면 지붕 위 레이어 팝업으로만 표시한다.
  if (!anchor) return null;

  const style: React.CSSProperties = {
    left: anchor.x,
    top: anchor.y,
    right: "auto",
    transform: "translate(-50%, calc(-100% - 10px))",
  };

  return (
    <div className="result-panel" style={style}>
      <div className="verdict" style={{ background: panel.color }}>
        {panel.verdict_label}
      </div>

      <div className="result-address">{panel.address}</div>

      <div className="result-columns">
        <section>
          <div className="result-section-title">필지 기준</div>
          <dl className="result-grid">
            <dt>용도지역</dt>
            <dd>{panel.zone || "—"}</dd>
            <dt>검토 용도</dt>
            <dd>{panel.building_use || "—"}</dd>
            <dt>대지면적</dt>
            <dd>{panel.site_area_m2?.toLocaleString()}㎡</dd>
            <dt>건폐율 상한</dt>
            <dd>{panel.bcr_max_pct}%</dd>
            <dt>용적률 상한</dt>
            <dd>{panel.far_max_pct}%</dd>
          </dl>
        </section>

        {m && (
          <section className="result-scale">
            <div className="result-section-title">가능 규모</div>
            <dl className="result-grid">
              <dt>건축면적</dt>
              <dd>{m.building_area_m2.toLocaleString()}㎡</dd>
              {m.exceeds_far_limit ? (
                <>
                  <dt>요청 용적률</dt>
                  <dd>{m.requested_far_pct}%</dd>
                  <dt>적용 상한</dt>
                  <dd>{panel.far_max_pct}%</dd>
                </>
              ) : (
                <>
                  <dt>연면적</dt>
                  <dd>{m.gross_floor_area_m2.toLocaleString()}㎡</dd>
                  <dt>층수</dt>
                  <dd>
                    {m.floors}층
                    {m.top_floor_ratio > 0 && m.top_floor_ratio < 0.999
                      ? ` (최상층 ${Math.round(m.top_floor_ratio * 100)}%)`
                      : ""}
                    {` · 약 ${m.mass_height_m}m`}
                  </dd>
                </>
              )}
            </dl>
          </section>
        )}
      </div>

      <div className="result-evidence">
        {m?.note && <p className="result-note">{m.note}</p>}
        {panel.constraints?.length > 0 && (
          <ul className="constraints">
            {panel.constraints.map((constraint: any) => (
              <li key={constraint.name}>
                <strong>{constraint.name}</strong> — {constraint.note}
              </li>
            ))}
          </ul>
        )}
        {panel.legal_basis && <p className="result-basis">{panel.legal_basis}</p>}
      </div>
    </div>
  );
}
