import jQuery from "jquery";
import { useEffect, useRef, useState } from "react";
import { MapBridge, type MapCommand } from "../lib/mapBridge";

interface Props {
  vworldKey: string;
  commands: MapCommand[];
  /** 지도가 준비되면 bridge 를 넘긴다. 패널을 건물 위에 띄우는 데 쓴다. */
  onReady?: (bridge: MapBridge) => void;
  onMapSelect?: (lon: number, lat: number) => void;
}

/**
 * VWorld 3D(ws3d) 엔진 로딩 및 초기화.
 *
 * 엔진 번들을 직접 뜯어서 확인한 사실들 — 문서가 아니라 소스가 근거다:
 *
 * 1. webglMapInit.js.do 는 엔진이 아니라 부트스트랩이다. 전역 몇 개를 세팅한 뒤
 *    document.write() 로 엔진 3개를 붙인다. document.write() 는 스크립트를
 *    appendChild 로 동적 삽입하면 무시되므로, 부트스트랩만 로드하면 엔진이
 *    영영 안 붙는다. 그래서 부트스트랩이 하는 일을 여기서 직접 한다.
 *
 * 2. 엔진은 jQuery 전역($ / jQuery)에 의존한다. 부트스트랩은 jQuery 를 로드하지
 *    않는다(원래는 페이지가 직접 넣어주는 구조). 없으면 "$ is not defined".
 *
 * 3. 실제 export 는 번들 말미에 있다:
 *      D.Map = _Map; D.MapOptions = MapOptions; window.vw = D;
 *    vw.MapController.initMap() 같은 API 는 없다(MapController 는 이벤트 상수).
 *    초기화는 new vw.Map(options) -> setMapId() -> start() 다.
 *
 * 4. vw.BasemapType = { GRAPHIC: "GRAPHIC" } — 유효한 베이스맵은 하나뿐이다.
 *    "PHOTO" 같은 값을 넣으면 지도가 그려지다 깨진다.
 *
 * 5. 엔진은 ws3d.viewer 를 재정의 불가 프로퍼티로 정의한다. 초기화를 두 번
 *    호출하면 "Cannot redefine property: viewer" 로 죽는다. React StrictMode 는
 *    개발 모드에서 effect 를 두 번 실행하므로 초기화는 반드시 한 번만 해야 한다.
 */

/**
 * 지도가 처음 자리잡을 위치.
 *
 * 카메라는 엔진에 맡긴다. 엔진은 로드 후 이 위치로 짧게 비행하는데(SDK 기본
 * 동작), 이를 억지로 막거나 되돌리려 하면 오히려 화면이 흔들린다.
 */
export interface InitialView {
  lon: number;
  lat: number;
  height: number;
}

const INITIAL_VIEW: InitialView = { lon: 126.978, lat: 37.5665, height: 3000 };

const VW_BASE = "https://map.vworld.kr";
const VW_ENGINE_SCRIPTS = [
  "/js/ws3dmap/WS3DRelease3/WSViewerStartup.js",
  "/js/ws3dmap/WS3DRelease3/VWViewerStartup.v30.min.js?ver=2024061902",
  "/js/ws3dmap/WS3DRelease3/vw.ol3WebGL.v30.js?ver=2024061902",
];

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.async = false; // 실행 순서가 중요하다
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`스크립트를 내려받지 못했습니다: ${src}`));
    document.head.appendChild(el);
  });
}

async function loadEngine(key: string): Promise<void> {
  const w = window as any;
  if (w.__vworldEngineLoaded) return;

  // (2) jQuery 전역 주입. 엔진이 .size() 를 한 곳에서 쓰는데 jQuery 3 에서
  //     제거된 API 라 되살린다.
  if (!w.jQuery) {
    if (typeof (jQuery.fn as any).size !== "function") {
      (jQuery.fn as any).size = function (this: any) {
        return this.length;
      };
    }
    w.jQuery = jQuery;
    w.$ = jQuery;
  }

  // (1) 부트스트랩이 세팅하던 전역들
  const https = location.protocol === "https:";
  w.v_protocol = https ? "https://" : "http://";
  w.vworldUrl = https ? "https://map.vworld.kr" : "http://map.vworld.kr";
  w.vworld2DCache = https ? "https://2d.vworld.kr/2DCache" : "http://2d.vworld.kr:8895/2DCache";
  w.vworldBaseMapUrl = https ? "https://cdn.vworld.kr/2d" : "http://cdn.vworld.kr:8080/2d";
  w.vworldStyledMapUrl = https ? "https://2d.vworld.kr/stmap" : "http://2d.vworld.kr:8895/stmap";
  w.vworldIsValid = "true";
  w.vworldErrMsg = "";
  w.vworldApiKey = key;
  w.vworld3DUrl = "/js/webglMapInit.js.do";
  w.vworldNoCss = "n";
  w.vworldVectorKey = "483E0418-2F46-3223-80A1-F66D16A24685";

  for (const path of VW_ENGINE_SCRIPTS) {
    await loadScript(`${VW_BASE}${path}`);
  }
  w.__vworldEngineLoaded = true;
}

/** ws3d.viewer 가 우리가 쓸 API 를 갖출 때까지 기다린다. */
function waitForViewer(timeoutMs = 20000): Promise<any> {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = setInterval(() => {
      const v = (window as any).ws3d?.viewer;
      if (v?.objectManager?.createPolygons && v?.scene?.camera?.flyTo) {
        clearInterval(tick);
        console.info("[MapCanvas] ws3d 뷰어 준비됨");
        resolve(v);
        return;
      }
      if (Date.now() - started > timeoutMs) {
        clearInterval(tick);
        console.error("[MapCanvas] window.ws3d =", (window as any).ws3d);
        reject(
          new Error(
            "지도는 떴지만 ws3d.viewer 에서 objectManager/scene.camera 를 찾지 못했습니다. " +
              "필지·건물이 그려지지 않습니다.",
          ),
        );
      }
    }, 200);
  });
}

/**
 * 뷰어 객체 생성과 실제 지도 표시는 별개다. VWorld가 WebGL 캔버스를 먼저
 * 검게 만든 뒤 지형 타일을 그리므로, 첫 유효 렌더가 끝날 때까지 로딩 화면을
 * 유지한다.
 */
function waitForFirstMapRender(viewer: any): Promise<void> {
  return new Promise((resolve) => {
    const scene = viewer.scene;
    let frames = 0;
    let indicatorSeen = false;
    let finished = false;

    const finish = (reason: string) => {
      if (finished) return;
      finished = true;
      removePostRender?.();
      console.info(`[MapCanvas] 첫 지도 렌더 준비됨 (${reason})`);
      resolve();
    };

    const removePostRender = scene.postRender?.addEventListener?.(() => {
      frames += 1;
      const indicator = document.querySelector<HTMLElement>(
        "#wsLoadingIndicator, .wsLoadingIndicator",
      );
      const style = indicator ? window.getComputedStyle(indicator) : null;
      const indicatorVisible = Boolean(
        indicator &&
          style?.display !== "none" &&
          style?.visibility !== "hidden" &&
          Number(style?.opacity ?? "1") > 0 &&
          indicator.getClientRects().length > 0,
      );
      if (indicatorVisible) indicatorSeen = true;
      if (indicatorSeen && !indicatorVisible) {
        finish("VWorld wsLoadingIndicator 종료");
        return;
      }

      const position = scene.camera?.positionCartographic;
      const cameraAtInitialView =
        position &&
        Math.abs((position.longitude * 180) / Math.PI - INITIAL_VIEW.lon) < 0.1 &&
        Math.abs((position.latitude * 180) / Math.PI - INITIAL_VIEW.lat) < 0.1 &&
        position.height < 10000;

      // 일부 SDK 버전은 wsLoadingIndicator를 만들지 않는다. 그 경우에만 목표
      // 위치에서 실제 렌더가 연속으로 발생하고 타일이 준비된 것을 보조 신호로 쓴다.
      if (!indicator && frames >= 3 && cameraAtInitialView && scene.globe?.tilesLoaded) {
        finish("목표 위치 첫 지도 렌더");
      }
    });
    scene.requestRender?.();
  });
}

/**
 * 초기 시점을 '붙잡아' 둔다.
 *
 * 엔진에는 카메라를 움직이는 주체가 여럿이다(Lookat / Drive / Fly 애니메이터,
 * 초기 비행, 지형 로딩 후 재배치 등). 어느 하나를 막으면 다른 하나가 움직였고,
 * 그때마다 화면이 흘렀다.
 *
 * 그래서 원인을 하나씩 쫓는 대신, 로드 직후 PIN_MS 동안 매 프레임 카메라를
 * 원하는 위치로 되돌린다. 무엇이 움직이든 결과적으로 고정된다.
 * PIN_MS 가 지나면 손을 떼므로 이후 사용자 조작(줌·회전·내 위치)은 정상이다.
 *
 * 우아한 방법은 아니다. 다만 '초기 시점이 흔들리지 않는다'는 요구를 확실히
 * 만족시키고, 사용자 조작을 방해하지 않는다.
 */
const PIN_MS = 8000;

/**
 * 초기 시점 고정을 즉시 푸는 함수.
 *
 * 질의 결과로 지도를 특정 필지로 옮길 때 반드시 먼저 호출해야 한다.
 * 그러지 않으면 고정 루프가 매 프레임 카메라를 초기 위치로 되돌려서,
 * 필지로 날아간 카메라가 곧바로 밀려난다(= 지도가 안 움직이는 것처럼 보인다).
 */
let releasePin: (() => void) | null = null;

/**
 * 엔진 map 인스턴스. 지도 명령이 도착했을 때 엔진의 카메라 애니메이터를
 * 멈추기 위해 보관한다.
 *
 * 엔진은 지도 로드가 끝나면 _lookat.moveTo(initPosition) 으로 초기 비행을
 * 시작하는데, 그 타이밍이 질의 완료보다 늦으면 fly_to 로 옮긴 카메라를
 * 그대로 덮어쓴다(= 필지로 갔다가 초기 위치로 되돌아온다).
 */
let engineMap: any = null;

/** 지도 명령 실행 직전에 호출. 초기 시점 고정과 엔진 비행을 모두 멈춘다. */
export function stopCameraAnimations(): void {
  releasePin?.();
  try {
    engineMap?._lookat?.stop?.();
  } catch {
    /* 애니메이터가 없으면 무시 */
  }
}

function pinInitialView(viewer: any): void {
  const ws3d = (window as any).ws3d;
  const camera = viewer.scene.camera;
  const until = Date.now() + PIN_MS;
  let released = false;

  // 사용자가 조작하면 즉시 손을 뗀다.
  //
  // 이게 없으면 고정 시간(8초) 안에 누른 줌·'내 위치' 버튼이 매 프레임
  // 되돌려져서, 버튼이 고장난 것처럼 보인다.
  const release = (why: string) => {
    if (released) return;
    released = true;
    releasePin = null;
    console.info(`[MapCanvas] 초기 시점 고정 해제 (${why})`);
  };
  releasePin = () => release("지도 명령 수신");

  const canvas = viewer.scene.canvas as HTMLElement | undefined;
  const onUserInput = () => release("사용자 조작");
  for (const ev of ["pointerdown", "wheel", "touchstart", "keydown"]) {
    canvas?.addEventListener(ev, onUserInput, { once: true, passive: true });
  }
  // 지도 컨트롤 버튼은 캔버스 밖에 있으므로 문서 전체에서도 받는다.
  document.addEventListener("pointerdown", onUserInput, { once: true, passive: true });

  const hold = () => {
    if (released) return;
    try {
      camera.setView({
        destination: ws3d.common.Cartesian3.fromDegrees(
          INITIAL_VIEW.lon,
          INITIAL_VIEW.lat,
          INITIAL_VIEW.height,
        ),
        orientation: { heading: 0, pitch: -Math.PI / 4, roll: 0 }, // 라디안(-45도)
      });
    } catch {
      /* 초기화 중 일시적 실패는 무시 */
    }
    if (Date.now() < until) requestAnimationFrame(hold);
    else release("시간 만료");
  };

  hold();
}

/**
 * (5) 초기화는 앱 전체에서 단 한 번.
 *
 * StrictMode 이중 실행과 HMR 재실행 모두 이 프로미스를 재사용한다.
 * 실패해도 재시도하지 않는다 — start() 가 이미 불린 뒤라면 재호출이
 * "Cannot redefine property: viewer" 로 다시 죽기 때문이다.
 */
let viewerPromise: Promise<any> | null = null;

function initMapOnce(key: string): Promise<any> {
  if (viewerPromise) return viewerPromise;

  viewerPromise = loadEngine(key).then(() => {
    const vw = (window as any).vw;
    if (!vw?.Map || !vw?.MapOptions) {
      throw new Error("vw.Map / vw.MapOptions 가 없습니다. 엔진 export 가 기대와 다릅니다.");
    }

    // (6) BASIC_OPTION 은 쓰지 않는다. 그 기본 방향이 Direction(0, 60, 0) 인데
    //     엔진이 tilt 를 Cesium pitch 로 그대로 넘기므로(pitch +60 = 위를 향함)
    //     화면에 하늘만 보인다.
    //
    //     Cartesian3(x, y, z) 는 여기서 (경도, 위도, 고도m) 다 —
    //     엔진이 Cartesian3.fromDegrees(location.x, .y, .z) 로 변환한다.
    const ws3d = (window as any).ws3d;
    const camera = new vw.CameraPosition(
      new ws3d.common.Cartesian3(INITIAL_VIEW.lon, INITIAL_VIEW.lat, INITIAL_VIEW.height),
      // tilt 는 도(degree). 엔진이 pitch 로 그대로 넘긴다.
      // -90(완전 수직)은 방위가 불안정해질 수 있어 엔진 자신도 -45 를 쓴다.
      new vw.Direction(0, -45, 0),
    );
    const opts = new vw.MapOptions("GRAPHIC", "", "BASIC", "BASIC", false, camera, camera);

    const map = new vw.Map(opts);
    map.setMapId("vmap");
    map.start();
    engineMap = map;

    return waitForViewer().then(async (viewer) => {
      pinInitialView(viewer);
      await waitForFirstMapRender(viewer);
      return viewer;
    });
  });

  return viewerPromise;
}

type Status = "loading" | "ready" | "error";

export function MapCanvas({ vworldKey, commands, onReady, onMapSelect }: Props) {
  const bridgeRef = useRef<MapBridge | null>(null);
  const appliedRef = useRef(0);
  // 결과 팝업은 카메라를 따라 매 프레임 App을 다시 렌더한다. onMapSelect 함수
  // identity를 effect 의존성으로 쓰면 클릭 핸들러도 매 프레임 파괴/재생성되어
  // 팝업이 뜬 뒤 클릭이 유실된다. 최신 콜백만 ref로 교체하고 핸들러는 유지한다.
  const onMapSelectRef = useRef(onMapSelect);
  const [status, setStatus] = useState<Status>("loading");
  const [errorDetail, setErrorDetail] = useState("");
  const [locMsg, setLocMsg] = useState("");
  const [viewMode, setViewMode] = useState<"2d" | "3d">("3d");
  const [toolsOpen, setToolsOpen] = useState(false);

  useEffect(() => {
    onMapSelectRef.current = onMapSelect;
  }, [onMapSelect]);

  function runMapTool(action: "measureLine" | "measureArea" | "measureHeight" | "erase"): void {
    stopCameraAnimations();
    const navigation = (window as any).vw?.NavigationZoom;
    if (typeof navigation?.[action] !== "function") {
      setLocMsg("지도 측정 도구가 아직 준비되지 않았습니다.");
      return;
    }
    navigation[action]();
  }

  useEffect(() => {
    let cancelled = false;

    initMapOnce(vworldKey)
      .then((viewer) => {
        if (cancelled) return;
        bridgeRef.current = new MapBridge(viewer, vworldKey);
        setStatus("ready");
        onReady?.(bridgeRef.current);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error("[MapCanvas] 지도 준비 실패:", err);
        setErrorDetail(String(err?.message ?? err));
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, [vworldKey]);

  useEffect(() => {
    if (!bridgeRef.current || status !== "ready") return;
    return bridgeRef.current.onMapClick(
      (lon, lat) => onMapSelectRef.current?.(lon, lat),
      setLocMsg,
    );
  }, [status]);

  /**
   * '내 위치' — 브이월드 SDK 기본 버튼은 실패해도 아무 표시가 없어(에러 콜백을
   * 넘기지 않는다) 왜 안 되는지 알 수가 없다. 그래서 직접 만든다.
   * 실패하면 사유를 화면에 띄운다.
   */
  function goToMyLocation(): void {
    setLocMsg("IP 기반 대략적 위치 확인 중…");
    fetch("https://ipwho.is/")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (data.success === false || !Number.isFinite(data.longitude) || !Number.isFinite(data.latitude)) {
          throw new Error(data.message || "위치 좌표 없음");
        }
        const longitude = Number(data.longitude);
        const latitude = Number(data.latitude);
        bridgeRef.current?.moveTo(longitude, latitude, 800, 55);
        const place = [data.region, data.city].filter(Boolean).join(" ");
        setLocMsg(`IP 기반 대략적 위치로 이동${place ? ` — ${place}` : ""} (실제 위치와 다를 수 있음)`);
        setTimeout(() => setLocMsg(""), 7000);
      })
      .catch((err) => setLocMsg(`IP 위치를 확인하지 못했습니다: ${String(err)}`));
  }

  // 새로 도착한 명령만 실행한다.
  //
  // status 를 의존성에 넣는 이유: 지도 로딩(수 초)보다 질의가 먼저 끝나면
  // 명령이 도착해도 bridge 가 없어 버려지고, 다음 질의 전까지 안 그려진다.
  // 준비되는 순간 밀린 명령을 몰아서 실행한다.
  useEffect(() => {
    if (!bridgeRef.current) return;
    const pending = commands.slice(appliedRef.current);
    if (pending.length === 0) return;

    // 질의 결과가 도착했으면 카메라를 움직이는 다른 주체를 모두 멈춘다.
    // (초기 시점 고정 + 엔진의 초기 비행) 살아 있으면 fly_to 가 곧 덮어써진다.
    stopCameraAnimations();

    bridgeRef.current.execute(pending);
    appliedRef.current = commands.length;

    // 비행이 끝난 뒤 실제 카메라 위치를 남긴다. 화면과 로그가 어긋나면
    // 누가 카메라를 되돌렸는지 추적할 근거가 된다.
    setTimeout(() => {
      const c = (window as any).ws3d?.viewer?.scene?.camera?.positionCartographic;
      const ws3d = (window as any).ws3d;
      if (c && ws3d) {
        console.info(
          `[MapCanvas] 명령 실행 후 카메라: ` +
            `${ws3d.common.CesiumMath.toDegrees(c.latitude).toFixed(5)}, ` +
            `${ws3d.common.CesiumMath.toDegrees(c.longitude).toFixed(5)}, ` +
            `고도 ${Math.round(c.height)}m`,
        );
      }
    }, 3000);
  }, [commands, status]);

  return (
    <>
      <div id="vmap" className="map-canvas" />

      {status === "loading" && (
        <div className="map-loading" role="status" aria-live="polite">
          <div className="map-loading-contours" aria-hidden="true" />
          <div className="map-loading-content">
            <div className="map-loading-mark" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <strong>토지 정보를 준비하고 있습니다</strong>
            <p>지도와 지적 경계를 불러오는 중입니다</p>
            <div className="map-loading-track" aria-hidden="true">
              <i />
            </div>
          </div>
        </div>
      )}

      {status === "ready" && (
        <>
          <div className="map-mode-controls">
            <button onClick={goToMyLocation} title="IP 기반 대략적 위치로 이동">◎ 내 위치</button>
            <button
            onClick={() => {
              const next = viewMode === "3d" ? "2d" : "3d";
              // 검색 직후에는 VWorld의 초기 _lookat 비행이 늦게 살아나
              // 저장·복원한 카메라를 서울 초기 화면으로 덮어쓸 수 있다.
              // 지도 명령 때뿐 아니라 모드 전환 직전에도 반드시 중단한다.
              stopCameraAnimations();
              setViewMode(next);
              void bridgeRef.current?.setViewMode(next).catch((error: unknown) => {
                const detail = error instanceof Error ? error.message : String(error);
                setLocMsg(
                  next === "2d"
                    ? `2D 화면은 전환됐지만 지적도 경계를 불러오지 못했습니다: ${detail}`
                    : `3D 화면 복원에 실패했습니다: ${detail}`,
                );
              });
            }}
            title="2D 필지 선택 지도와 3D 규모 지도 전환"
          >
            {viewMode === "3d" ? "2D 지적도" : "3D 지도"}
            </button>
          </div>

          <div className={`map-tool-menu${toolsOpen ? " is-open" : ""}`}>
            <button
              type="button"
              className="map-tool-toggle"
              aria-expanded={toolsOpen}
              onClick={() => setToolsOpen((open) => !open)}
            >
              <span aria-hidden="true">☰</span>
              메뉴
            </button>
            <div className="map-tool-items" aria-hidden={!toolsOpen}>
              <button type="button" onClick={() => runMapTool("measureLine")}>거리</button>
              <button type="button" onClick={() => runMapTool("measureArea")}>면적</button>
              <button type="button" onClick={() => runMapTool("measureHeight")}>높이</button>
              <button type="button" onClick={() => runMapTool("erase")}>초기화</button>
            </div>
          </div>
        </>
      )}
      {locMsg && <div className="my-location-msg">{locMsg}</div>}
      {status === "error" && (
        <div className="map-error">
          지도를 준비하지 못했습니다.
          <br />
          <small>{errorDetail}</small>
        </div>
      )}
    </>
  );
}
