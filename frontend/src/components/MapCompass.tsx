import { useEffect, useState } from "react";
import type { MapSurface } from "../lib/mapSurface";

/**
 * 오른쪽 위 나침반 + 해 방향 위젯.
 *
 * - 나침반: 카메라 방위(heading)에 따라 회전해 N 이 항상 실제 북쪽을 가리킨다.
 *   VWorld 내장 나침반(#naviTopPannel3d)이 이 엔진 설정에서 렌더되지 않아 직접 그린다.
 * - 해 방향: 필지 위치·현재 시각의 태양 방위(azimuth)를 링 위에 표시한다.
 *   일조권 검토의 시각 보조 — 정밀 일영분석은 아니다.
 */

/** 태양 위치(방위·고도). SunCalc 방식의 표준 천문 근사. */
function sunPosition(lat: number, lon: number, date: Date): { azimuth: number; elevation: number } {
  const rad = Math.PI / 180;
  const dayMs = 86400000;
  const J1970 = 2440588;
  const J2000 = 2451545;
  const toDays = (d: Date) => d.valueOf() / dayMs - 0.5 + J1970 - J2000;
  const d = toDays(date);
  const M = rad * (357.5291 + 0.98560028 * d);
  const C = rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const P = rad * 102.9372;
  const L = M + C + P + Math.PI;
  const e = rad * 23.4397;
  const dec = Math.asin(Math.sin(L) * Math.sin(e));
  const ra = Math.atan2(Math.sin(L) * Math.cos(e), Math.cos(L));
  const lw = rad * -lon;
  const theta = rad * (280.16 + 360.9856235 * d) - lw;
  const H = theta - ra;
  const phi = rad * lat;
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi));
  const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
  // az 는 남쪽 기준 시계방향(서쪽 +). 나침반 방위(북 0, 동 90)로 변환.
  const azimuth = ((az / rad + 180) % 360 + 360) % 360;
  return { azimuth, elevation: alt / rad };
}

export function MapCompass({ bridge }: { bridge: MapSurface | null }) {
  const [heading, setHeading] = useState(0);
  // 시각은 1분마다 갱신(태양 방위가 서서히 이동)
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!bridge) return;
    const update = () => setHeading(bridge.cameraHeadingDeg());
    update();
    const dispose = bridge.onCameraChange(update);
    return dispose;
  }, [bridge]);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 60000);
    return () => window.clearInterval(t);
  }, []);

  if (!bridge) return null;
  const focus = bridge.focusLonLat();

  // 나침반은 방위 반대로 돌려 N 이 실제 북을 가리키게 한다.
  const roseStyle = { transform: `rotate(${-heading}deg)` };

  // 해 방향: 태양 방위 - 카메라 방위 = 화면상 각도(위=화면 북). 필지가 있을 때만.
  let sun: { azimuth: number; elevation: number } | null = null;
  if (focus) sun = sunPosition(focus.lat, focus.lon, new Date(now));
  const sunScreenDeg = sun ? sun.azimuth - heading : 0;
  const sunUp = sun && sun.elevation > 0;
  // 링 위 위치(반지름 34px). 화면각 0=위(북), 시계방향.
  const r = 34;
  const sx = sun ? Math.sin((sunScreenDeg * Math.PI) / 180) * r : 0;
  const sy = sun ? -Math.cos((sunScreenDeg * Math.PI) / 180) * r : 0;

  return (
    <div className="map-compass" title="나침반 · 해 방향(현재 시각)">
      <div className="map-compass-rose" style={roseStyle}>
        <span className="cn">N</span>
        <span className="ce">E</span>
        <span className="cs">S</span>
        <span className="cw">W</span>
        <div className="map-compass-needle" />
      </div>
      {sun && (
        <div
          className={`map-compass-sun${sunUp ? "" : " is-down"}`}
          style={{ transform: `translate(${sx}px, ${sy}px)` }}
          title={
            sunUp
              ? `해 방위 ${Math.round(sun.azimuth)}° · 고도 ${Math.round(sun.elevation)}°`
              : `일몰 후(태양 고도 ${Math.round(sun.elevation)}°)`
          }
        >
          {sunUp ? "☀" : "🌙"}
        </div>
      )}
      {sun && (
        <div className="map-compass-suninfo">
          {sunUp ? `해 ${Math.round(sun.elevation)}°` : "일몰 후"}
        </div>
      )}
    </div>
  );
}
