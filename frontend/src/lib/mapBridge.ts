/**
 * 지도 명령 실행기 — VWorld 3D 엔진용.
 *
 * 그리기는 vw.geom 공개 API 를 쓴다. 저수준 ws3d.viewer.objectManager 를 직접
 * 부르면 좌표·색상 객체 타입이 맞지 않아 조용히 아무것도 안 그려진다
 * (setter 들이 타입 검사 후 console.log 만 남기고 값을 버린다).
 *
 * 엔진 번들에서 확인한 공개 API:
 *   const poly = new vw.geom.Polygon([vw.CoordZ...])   // 평면
 *   const mass = new vw.geom.PolygonZ([vw.CoordZ...])  // 입체
 *   poly.setFillColor(new vw.Color(r, g, b, a))        // 0~255
 *   poly.setOutLineColor(new vw.Color(...))
 *   mass.setExtrudeHeight(meters)                      // 건물 높이
 *   poly.create()                                      // 실제로 그린다
 *   ws3d.viewer.objectManager.removeGeometryById(id)   // 제거
 *   ws3d.viewer.scene.camera.flyTo({...})              // 라디안
 */

declare global {
  interface Window {
    vw: any;
    ws3d: any;
    Cesium: any;
  }
}

export type MapCommand =
  | { type: "clear_mass" }
  | {
      type: "fly_to";
      lon: number;
      lat: number;
      altitude: number;
      tilt: number;
      /** 북쪽 0, 동쪽 90. 긴 필지를 옆에서 보도록 백엔드가 계산한다. */
      heading?: number;
    }
  | {
      type: "highlight_parcel";
      geometry: GeoJSONPolygon;
      pnu: string;
      label: string;
      color: string;
    }
  | {
      type: "extrude_mass";
      geometry: GeoJSONPolygon;
      footprint_geometry?: GeoJSONPolygon | null;
      top_footprint_geometry?: GeoJSONPolygon | null;
      anchor?: { lon: number; lat: number };
      height_m: number;
      floors: number;
      full_floors?: number;
      top_floor_ratio?: number;
      flat_only?: boolean;
      footprint_ratio: number;
      color: string;
      opacity: number;
      label: string;
    }
  | {
      /** 걸침 필지: 용도지역별 교차 조각을 색으로 깐다. 색 경계 = 지역 경계 */
      type: "show_zone_pieces";
      pieces: Array<{
        zone: string;
        share_pct: number;
        area_m2: number;
        color: string;
        geometry: GeoJSONPolygon;
      }>;
    }
  | { type: "show_panel"; [k: string]: any };

/**
 * VWorld 연속지적도는 MultiPolygon 으로 온다 (한 필지가 여러 조각일 수 있다).
 * Polygon 도 들어올 수 있으므로 둘 다 받는다.
 */
export type GeoJSONPolygon =
  | { type: "Polygon"; coordinates: number[][][] }
  | { type: "MultiPolygon"; coordinates: number[][][][] };

export type HousingModelType = "detached" | "lowrise" | "slim";

/** Polygon / MultiPolygon 을 외곽 링 목록으로 통일한다. (backend/vworld.py 와 동일 규칙) */
function outerRings(geometry: GeoJSONPolygon): number[][][] {
  if (geometry.type === "Polygon") {
    return geometry.coordinates.length ? [geometry.coordinates[0]] : [];
  }
  return geometry.coordinates.filter((poly) => poly.length).map((poly) => poly[0]);
}

/** 신발끈 공식 — 경위도 기준 상대 비교용이라 실제 면적일 필요는 없다. */
function ringArea(r: number[][]): number {
  return Math.abs(
    r.reduce((s, [x1, y1], i) => {
      const [x2, y2] = r[(i + 1) % r.length];
      return s + (x1 * y2 - x2 * y1);
    }, 0) / 2,
  );
}

/** 여러 조각 중 가장 넓은 링. 매스는 대표 조각 위에 세운다. */
function largestRing(geometry: GeoJSONPolygon): number[][] | null {
  const rings = outerRings(geometry);
  if (rings.length === 0) return null;
  return rings.reduce((best, r) => (ringArea(r) > ringArea(best) ? r : best), rings[0]);
}

/** 폴리곤을 중심점 기준으로 축소한다. 건폐율만큼만 바닥을 차지하는 매스를 만들 때 사용. */
function scalePolygon(ring: number[][], ratio: number): number[][] {
  // 면적비 ratio -> 선형 축척은 sqrt(ratio)
  const k = Math.sqrt(Math.max(0, Math.min(1, ratio)));
  const n = ring.length;
  const cx = ring.reduce((s, p) => s + p[0], 0) / n;
  const cy = ring.reduce((s, p) => s + p[1], 0) / n;
  return ring.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k]);
}

function centroid(ring: number[][]): [number, number] {
  const n = ring.length;
  return [
    ring.reduce((s, p) => s + p[0], 0) / n,
    ring.reduce((s, p) => s + p[1], 0) / n,
  ];
}

/** 점이 폴리곤 내부인지 확인하는 ray-casting. */
function pointInRing(x: number, y: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

/**
 * 복잡한 필지 형상을 그대로 축소하지 않고 내부에 들어가는 직사각형을 찾는다.
 * 가장 긴 필지 변과 평행하게 놓고, 내부 표본점이 모두 포함될 때까지 줄인다.
 */
function inscribedRectangle(
  ring: number[][],
  center: [number, number],
  occupancy: number,
  maxAspect: number,
): number[][] {
  const [cx, cy] = center;
  const cosLat = Math.cos((cy * Math.PI) / 180);
  const local = ring.map(([lon, lat]) => [
    (lon - cx) * 111320 * cosLat,
    (lat - cy) * 111320,
  ]);
  let longest = { d2: 0, angle: 0 };
  for (let i = 0; i + 1 < local.length; i += 1) {
    const dx = local[i + 1][0] - local[i][0];
    const dy = local[i + 1][1] - local[i][1];
    const d2 = dx * dx + dy * dy;
    if (d2 > longest.d2) longest = { d2, angle: Math.atan2(dy, dx) };
  }
  const ca = Math.cos(longest.angle);
  const sa = Math.sin(longest.angle);
  const rotated = local.map(([x, y]) => [x * ca + y * sa, -x * sa + y * ca]);
  const spanU = Math.max(...rotated.map((p) => p[0])) - Math.min(...rotated.map((p) => p[0]));
  const spanV = Math.max(...rotated.map((p) => p[1])) - Math.min(...rotated.map((p) => p[1]));
  // 법정 건축 가능 영역 면적의 대부분을 쓰되, 긴 필지 비율을 그대로 복제해
  // 창고처럼 보이지 않도록 모델 유형별 최대 장단변비를 제한한다.
  const area = Math.abs(local.reduce((sum, [x1, y1], index) => {
    const [x2, y2] = local[(index + 1) % local.length];
    return sum + x1 * y2 - x2 * y1;
  }, 0) / 2);
  const rawAspect = spanV > 0 ? spanU / spanV : maxAspect;
  const aspect = Math.max(1 / maxAspect, Math.min(maxAspect, rawAspect));
  const targetArea = Math.max(12, area * occupancy);
  let halfU = Math.sqrt(targetArea * aspect) / 2;
  let halfV = Math.sqrt(targetArea / aspect) / 2;

  const localRing = local;
  const sampleInside = (hu: number, hv: number) => {
    for (let iu = -4; iu <= 4; iu += 1) {
      for (let iv = -4; iv <= 4; iv += 1) {
        const u = (hu * iu) / 4;
        const v = (hv * iv) / 4;
        const x = u * ca - v * sa;
        const y = u * sa + v * ca;
        if (!pointInRing(x, y, localRing)) return false;
      }
    }
    return true;
  };
  for (let i = 0; i < 30 && !sampleInside(halfU, halfV); i += 1) {
    halfU *= 0.88;
    halfV *= 0.88;
  }
  const corners = [
    [-halfU, -halfV], [halfU, -halfV], [halfU, halfV], [-halfU, halfV], [-halfU, -halfV],
  ];
  return corners.map(([u, v]) => {
    const x = u * ca - v * sa;
    const y = u * sa + v * ca;
    return [cx + x / (111320 * cosLat), cy + y / 111320];
  });
}

/** 필지의 가장 긴 변이 화면 가로로 놓이도록 바라볼 카메라 방위각. */
function cameraHeading(geometry: GeoJSONPolygon): number {
  const edges: Array<{ length2: number; east: number; north: number }> = [];
  for (const ring of outerRings(geometry)) {
    for (let i = 0; i + 1 < ring.length; i += 1) {
      const [lon1, lat1] = ring[i];
      const [lon2, lat2] = ring[i + 1];
      const meanLat = ((lat1 + lat2) * Math.PI) / 360;
      const east = (lon2 - lon1) * Math.cos(meanLat);
      const north = lat2 - lat1;
      edges.push({ length2: east * east + north * north, east, north });
    }
  }
  if (edges.length === 0) return 0;
  const longest = edges.reduce((a, b) => (b.length2 > a.length2 ? b : a));
  const edgeBearing = (Math.atan2(longest.east, longest.north) * 180) / Math.PI;
  return (edgeBearing + 90 + 360) % 360;
}

/**
 * 필지·매스를 지면에서 살짝 띄우는 높이(m).
 *
 * 0 으로 두면 엔진이 지형 클램프로 그려서, 그 자리에 선 3D 건물 모델에
 * 완전히 가려진다(= 화면에 아무것도 안 보인다). 작은 값이라도 주면
 * 일반 3D 지오메트리로 그려진다.
 */
const PARCEL_LIFT_M = 3;

/** "#2E7D32" + 투명도(0~1) -> vw.Color(r, g, b, a) — 각 성분 0~255 */
function color(css: string, alpha: number): any {
  const hex = css.replace("#", "");
  const n = parseInt(hex.length === 3 ? hex.replace(/(.)/g, "$1$1") : hex, 16);
  return new window.vw.Color((n >> 16) & 255, (n >> 8) & 255, n & 255, Math.round(alpha * 255));
}

/**
 * 경위도 링 -> vw.Collection(vw.CoordZ...)
 *
 * 반드시 Collection 으로 감싸야 한다. 엔진의 두 생성자가 비대칭이다:
 *
 *   Polygon(e)  { if (e instanceof Array) rings = new Collection(e) }  // 배열 OK
 *   PolygonZ(e) { if (e instanceof Array) ring  = new Collection    }  // 배열을 버림!
 *               { else if (e instanceof Collection) ring = e        }
 *
 * PolygonZ 에 배열을 주면 좌표가 통째로 사라지고, 이후 makePolygon() 이
 * 빈 데이터를 만지다 "Cannot read properties of undefined (reading 'subarray')"
 * 로 죽는다. Collection 을 주면 두 클래스 모두 정상 동작한다.
 */
function toRing(ring: number[][], height = 0): any {
  const vw = window.vw;
  // 첫 점과 끝 점이 같으면 LinearRing 이 한 번 더 닫으면서 중복점이 생긴다.
  const pts = ring.slice();
  const [fx, fy] = pts[0];
  const [lx, ly] = pts[pts.length - 1];
  if (pts.length > 1 && fx === lx && fy === ly) pts.pop();

  return new vw.Collection(pts.map(([lon, lat]) => new vw.CoordZ(lon, lat, height)));
}

export class MapBridge {
  /**
   * 명령별 실행 결과. 콘솔을 열지 않고도 화면에서 원인을 볼 수 있게 남긴다.
   * 이 엔진은 실패해도 예외를 안 던지는 경우가 많아, 결과를 명시적으로 기록한다.
   */
  readonly log: string[] = [];

  private note(msg: string): void {
    this.log.push(msg);
    if (this.log.length > 12) this.log.shift();
    console.info("[MapBridge]", msg);
  }

  /**
   * 해당 지점의 지형 표고(m). PolygonZ 는 height/extrudedHeight 를 '해수면 기준
   * 절대고도'로 해석하므로, 지형 위에 세우려면 이 값을 더해야 한다.
   * (더하지 않으면 표고가 있는 지역에서 매스가 땅속에 묻힌다)
   */
  private terrainHeight(lon: number, lat: number): number {
    try {
      const ws3d = window.ws3d;
      const carto = ws3d.common.Cartographic.fromDegrees(lon, lat);
      const h = this.viewer.scene?.globe?.getHeight(carto);
      return typeof h === "number" && isFinite(h) ? h : 0;
    } catch {
      return 0;
    }
  }

  /** ws3d.viewer — 엔진이 전역으로 노출하는 뷰어 네임스페이스 */
  private viewer: any;
  private parcelIds: string[] = [];
  private massIds: string[] = [];
  private housingModelIds: string[] = [];
  private cadastreIds: string[] = [];
  // 걸침 필지의 용도지역 조각 오버레이
  private zonePieceIds: string[] = [];
  private focusEntity: any = null;
  private lastMassCommand: Extract<MapCommand, { type: "extrude_mass" }> | null = null;
  private cameraGeneration = 0;
  private modeGeneration = 0;
  private lastFocus: { lon: number; lat: number } | null = null;
  // 2D에서 이동한 위치를 3D에도 이어 쓰기 위해 절대 좌표가 아니라
  // 3D 카메라의 기울기와 화면 중심까지의 거리만 보관한다.
  private saved3DView: { pitch: number; range: number } | null = null;
  private readonly vworldKey: string;

  constructor(viewer: any, vworldKey: string) {
    this.viewer = viewer;
    this.vworldKey = vworldKey;
  }

  execute(commands: MapCommand[]): void {
    // 구버전 백엔드가 heading 을 보내지 않아도 같은 응답의 필지 형상으로
    // 프론트에서 계산한다. 길고 좁은 필지를 정면에서 봐 고층처럼 보이는 현상을
    // 막으며, 백엔드 프로세스 재시작 여부에도 영향을 받지 않는다.
    const geometry = commands.find(
      (candidate): candidate is Extract<MapCommand, { type: "extrude_mass" | "highlight_parcel" }> =>
        candidate.type === "extrude_mass" || candidate.type === "highlight_parcel",
    )?.geometry;
    const fallbackHeading = geometry ? cameraHeading(geometry) : 0;
    const focusRing = geometry ? largestRing(geometry) : null;
    const focusPoint = focusRing ? centroid(focusRing) : null;
    const focusHeight =
      commands.find((candidate) => candidate.type === "extrude_mass")?.height_m ?? 0;
    let deferredFly: Extract<MapCommand, { type: "fly_to" }> | null = null;

    for (const cmd of commands) {
      try {
        switch (cmd.type) {
          case "clear_mass":
            this.clearMass();
            this.clearParcel();
            this.clearZonePieces();
            break;
          case "fly_to":
            // 지형 상대 Entity가 생성된 다음 중심을 잡아야 실제 매스 위치와
            // 카메라 목표가 일치한다.
            deferredFly = cmd;
            break;
          case "highlight_parcel":
            this.highlightParcel(cmd);
            break;
          case "extrude_mass":
            this.extrudeMass(cmd);
            break;
          case "show_zone_pieces":
            this.showZonePieces(cmd);
            break;
          case "show_panel":
            // 패널은 React 쪽에서 상태로 렌더링한다 — 지도에서는 할 일이 없다
            break;
        }
      } catch (err) {
        // 한 명령이 실패해도 나머지는 실행한다. 필지는 그렸는데 라벨에서
        // 터져서 전부 사라지는 상황을 막는다.
        this.note(`✗ ${cmd.type} 실패: ${(err as Error)?.message ?? err}`);
        console.error(`[MapBridge] '${cmd.type}' 실행 실패:`, err);
      }
    }

    if (deferredFly) {
      try {
        const heading =
          deferredFly.heading == null || deferredFly.heading === 0
            ? fallbackHeading
            : deferredFly.heading;
        this.flyTo(
          focusPoint?.[0] ?? deferredFly.lon,
          focusPoint?.[1] ?? deferredFly.lat,
          deferredFly.altitude,
          deferredFly.tilt,
          heading,
          focusHeight / 2,
        );
      } catch (err) {
        this.note(`✗ fly_to 실패: ${(err as Error)?.message ?? err}`);
      }
    }
  }

  /**
   * 외부에서 특정 좌표로 이동시킬 때 사용한다('내 위치' 버튼 등).
   *
   * 기본 고도를 낮게 잡으면(수백 m) 그 레벨의 지도 타일이 아직 없거나 늦게 와서
   * Cesium 기본 지구색(파랑)만 보인다 — 바다로 착각하기 쉽다. 여유 있게 잡는다.
   */
  moveTo(lon: number, lat: number, altitude = 2500, tilt = 50): void {
    this.flyTo(lon, lat, altitude, tilt);
  }

  private flyTo(
    lon: number,
    lat: number,
    altitude: number,
    tilt: number,
    heading = 0,
    focusHeight = 0,
  ): void {
    this.lastFocus = { lon, lat };
    const generation = ++this.cameraGeneration;
    const ws3d = window.ws3d;
    const camera = this.viewer.scene.camera;

    // 기울인 카메라는 발밑이 아니라 '앞쪽'을 본다. 대상을 화면 중앙에 두려면
    // 바라보는 방향 반대로 그만큼 물러나야 한다.
    //
    //        카메라 ─┐  ↘ 부각 depression
    //                │ altitude          d = altitude / tan(depression)
    //                └────── d ──────▶ 대상(화면 중앙)
    //
    // 이 거리를 고정값(0.003도)으로 두면 고도·각도가 바뀔 때마다 대상이
    // 화면 위아래로 밀려난다.
    //
    // altitude 는 '지면 위 높이'로 받는다. Cesium 카메라 고도는 해수면 절대값
    // 이므로 지형고를 더해 올려야 한다. 이걸 빼먹으면 내륙(음성 지형 140m)에서
    // 실제 눈높이가 그만큼 낮아지고, 후퇴 거리만 해수면 기준으로 계산되어
    // 시선이 대상보다 한참 앞 땅에 꽂힌다 — 필지가 화면 위로 밀려 올라간다.
    const ground = this.terrainHeight(lon, lat);
    const depression = ((90 - tilt) * Math.PI) / 180; // tilt=55 -> 35도
    const backOffM = altitude / Math.tan(depression);
    const headingRad = (heading * Math.PI) / 180;
    const latBackOff = (backOffM * Math.cos(headingRad)) / 111320;
    const lonBackOff =
      (backOffM * Math.sin(headingRad)) / (111320 * Math.cos((lat * Math.PI) / 180));

    const applyView = (terrainHeight: number) => {
      const destination = ws3d.common.Cartesian3.fromDegrees(
        lon - lonBackOff,
        lat - latBackOff,
        terrainHeight + focusHeight + altitude,
      );
      const target = ws3d.common.Cartesian3.fromDegrees(
        lon,
        lat,
        terrainHeight + focusHeight,
      );
      if (tilt <= 1) {
        camera.setView({
          destination,
          orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 },
        });
        return;
      }
      const direction = ws3d.common.Cartesian3.normalize(
        ws3d.common.Cartesian3.subtract(target, destination, new ws3d.common.Cartesian3()),
        new ws3d.common.Cartesian3(),
      );
      const surfaceNormal = ws3d.common.Cartesian3.normalize(
        destination,
        new ws3d.common.Cartesian3(),
      );
      const right = ws3d.common.Cartesian3.normalize(
        ws3d.common.Cartesian3.cross(direction, surfaceNormal, new ws3d.common.Cartesian3()),
        new ws3d.common.Cartesian3(),
      );
      const up = ws3d.common.Cartesian3.normalize(
        ws3d.common.Cartesian3.cross(right, direction, new ws3d.common.Cartesian3()),
        new ws3d.common.Cartesian3(),
      );
      camera.setView({ destination, orientation: { direction, up } });
    };

    // 1차 이동으로 해당 지역의 상세 지형 타일 로딩을 시작한다.
    applyView(ground);
    this.note("✓ 필지 중심 3D 벡터로 화면 중앙 정렬");

    // 같은 지점이 첫 조회 140m, 재조회 -55m로 바뀌므로 충분한 로딩 시간을
    // 둔 뒤 값이 연속적으로 안정되면 확정 지형고로 한 번 더 중앙을 맞춘다.
    const started = performance.now();
    let previous = ground;
    let stableFrames = 0;
    const refine = () => {
      if (generation !== this.cameraGeneration) return;
      const current = this.terrainHeight(lon, lat);
      stableFrames = Math.abs(current - previous) < 0.2 ? stableFrames + 1 : 0;
      previous = current;
      const elapsed = performance.now() - started;
      if ((elapsed >= 1000 && stableFrames >= 8) || elapsed >= 4000) {
        applyView(current);
        this.note(`✓ 상세 지형고 ${Math.round(current)}m 반영 후 중앙 재정렬`);
        return;
      }
      requestAnimationFrame(refine);
    };
    requestAnimationFrame(refine);

    const c = camera.positionCartographic;
    const deg = (r: number) => ws3d.common.CesiumMath.toDegrees(r).toFixed(4);
    this.note(
      `카메라 → 대상 ${lat.toFixed(4)}, ${lon.toFixed(4)} / 지면위 ${altitude}m` +
        `(지형 ${Math.round(ground)}m), 방위 ${Math.round(heading)}°, 후퇴 ${Math.round(backOffM)}m / ` +
        `실제 ${deg(c.latitude)}, ${deg(c.longitude)} @${Math.round(c.height)}m`,
    );
  }

  private highlightParcel(cmd: Extract<MapCommand, { type: "highlight_parcel" }>): void {
    const rings = outerRings(cmd.geometry);
    if (rings.length === 0) return;

    // 매스 색상과 명확히 구분되는 청록색 반투명 면 + 외곽선.
    // VWorld Polygon outline은 선 굵기가 제대로 적용되지 않아 Cesium Entity의
    // polygon과 clampToGround polyline을 함께 사용한다.
    const ws3d = window.ws3d;
    // 선택 필지는 주변 지적선과 즉시 구별되도록 밝은 시안색을 사용한다.
    // 기존 디자인: 밝은 청록색 외곽선과 청록 반투명 면.
    const parcelColor = ws3d.common.Color.fromCssColorString("#00E5FF");
    for (const ring of rings) {
      const flat = ring.flatMap(([lon, lat]) => [lon, lat]);
      const fillId = `map-parcel-fill-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const lineId = `map-parcel-line-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      this.viewer.entities.add({
        id: fillId,
        polygon: {
          hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
          material: parcelColor.withAlpha(0.22),
          height: 0.2,
          heightReference: 2, // RELATIVE_TO_GROUND
        },
      });
      this.viewer.entities.add({
        id: lineId,
        polyline: {
          positions: ws3d.common.Cartesian3.fromDegreesArray(flat),
          clampToGround: true,
          width: 3,
          material: parcelColor.withAlpha(1),
          depthFailMaterial: parcelColor.withAlpha(1),
        },
      });
      this.parcelIds.push(fillId, lineId);
    }
    this.viewer.scene?.requestRender?.();
    this.note("✓ 필지 경계 생성됨 (청록 반투명 + 외곽선)");

    // 필지에는 라벨을 붙이지 않는다. 매스에도 라벨이 붙어 마커가 둘이 되는데,
    // 같은 필지를 가리키는 핀이 둘이라 서로 다른 지점처럼 읽혔다.
    // 지번·면적은 결과 패널이 이미 보여준다.
  }

  /**
   * 걸침 필지의 용도지역별 교차 조각을 반투명 색으로 깐다.
   * 색이 갈리는 지점이 곧 용도지역 경계라 경계선을 따로 그리지 않는다.
   * 조각 의미(지역명·비율)는 우측 범례 창(React)이 설명한다.
   */
  private showZonePieces(cmd: Extract<MapCommand, { type: "show_zone_pieces" }>): void {
    const ws3d = window.ws3d;
    let drawn = 0;
    for (const piece of cmd.pieces) {
      const pieceColor = ws3d.common.Color.fromCssColorString(piece.color);
      for (const ring of outerRings(piece.geometry)) {
        if (ring.length < 3) continue;
        const flat = ring.flatMap(([lon, lat]) => [lon, lat]);
        const id = `map-zone-piece-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        this.viewer.entities.add({
          id,
          polygon: {
            hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
            // 위성영상(들판·숲) 위에서 0.38 은 뭉개져 보였다 — 진하게
            material: pieceColor.withAlpha(0.5),
            // 필지 채움(0.2m)보다 살짝 위 — 아래 청록색과 섞여 탁해지지 않게
            height: 0.6,
            heightReference: 2, // RELATIVE_TO_GROUND
          },
        });
        this.zonePieceIds.push(id);
        drawn += 1;
      }
    }

    // 경계선·벽은 그리지 않는다. 조각 색이 갈리는 곳이 곧 경계이며,
    // 별도 선·벽은 시각적으로 혼란만 줬다(사용자 확인 후 제거).
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 용도지역 조각 ${cmd.pieces.length}개 표시 (폴리곤 ${drawn}개)`);
  }

  private clearZonePieces(): void {
    this.removeAll(this.zonePieceIds);
  }

  private extrudeMass(cmd: Extract<MapCommand, { type: "extrude_mass" }>): void {
    this.lastMassCommand = cmd;
    const suppliedFootprint = cmd.footprint_geometry
      ? largestRing(cmd.footprint_geometry)
      : null;
    const base = suppliedFootprint ?? largestRing(cmd.geometry);
    if (!base) return;

    // 필지 형상을 유지한 채 건폐율에 맞춰 축소한다. 개념 직사각형은 실제
    // 대지 모양을 오해하게 하므로 사용하지 않는다.
    const footprint = suppliedFootprint
      ? base
      : scalePolygon(base, cmd.footprint_ratio);
    const fallbackAnchor = centroid(footprint);
    const px = cmd.anchor?.lon ?? fallbackAnchor[0];
    const py = cmd.anchor?.lat ?? fallbackAnchor[1];
    const ground = this.terrainHeight(px, py);
    const baseAboveGround = 0.5;
    const topAboveGround = baseAboveGround + cmd.height_m;

    // vw.geom.PolygonZ 래퍼는 height와 extrudedHeight를 다시 합산해 지형 표고가
    // 이중 적용된다. VWorld 내부 Cesium Entity를 사용하고 양쪽 높이 기준을
    // RELATIVE_TO_GROUND로 고정하면 지형 표고와 무관하게 정확히 지면에 붙는다.
    const ws3d = window.ws3d;
    const flat = footprint.flatMap(([lon, lat]) => [lon, lat]);
    const cesiumColor = ws3d.common.Color.fromCssColorString(cmd.color);
    // VWorld는 Cesium HeightReference를 ws3d.common에 재노출하지 않는 버전이
    // 있다. 이 번들 내부 enum에서 RELATIVE_TO_GROUND는 2이므로 전역 export가
    // 없을 때도 같은 값으로 동작하게 한다.
    const relativeToGround =
      (window as any).Cesium?.HeightReference?.RELATIVE_TO_GROUND ?? 2;
    const entityId = `map-mass-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const polygon: any = {
      hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
      height: cmd.flat_only ? baseAboveGround : topAboveGround,
      heightReference: relativeToGround,
      material: cesiumColor.withAlpha(cmd.opacity),
      outline: true,
      outlineColor: cesiumColor,
      closeTop: true,
      closeBottom: true,
    };
    if (!cmd.flat_only) {
      polygon.extrudedHeight = baseAboveGround;
      polygon.extrudedHeightReference = relativeToGround;
    }
    const massEntity = this.viewer.entities.add({
      id: entityId,
      polygon,
    });
    this.focusEntity = massEntity;
    this.massIds.push(entityId);
    this.note(
      cmd.flat_only
        ? "✓ 용적률 초과: 최대 건축면적 평면 표시"
        : "✓ 건물 매스 생성됨 (지형 상대고도 Entity)",
    );
    this.note(
      `매스: 지형 ${Math.round(ground)}m 위 ${baseAboveGround}m 부터 ` +
        `${cmd.height_m}m (${cmd.floors}층) · RELATIVE_TO_GROUND`,
    );

    // VWorld PointZ는 절대고도라 지형 상대 매스와 따로 움직인다. 마커도 같은
    // RELATIVE_TO_GROUND Entity로 만들어 항상 건물 지붕 중앙을 따라가게 한다.
    const markerId = `map-mass-marker-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.viewer.entities.add({
      id: markerId,
      position: ws3d.common.Cartesian3.fromDegrees(
        px,
        py,
        (cmd.flat_only ? 0 : cmd.height_m) + 2,
      ),
      point: {
        pixelSize: 11,
        color: cesiumColor,
        outlineColor: ws3d.common.Color.WHITE,
        outlineWidth: 2,
        heightReference: relativeToGround,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
    this.massIds.push(markerId);
    this.note("✓ 매스 지붕 상대고도 마커 생성됨");
  }

  /** 추천 주택을 법정 한계 매스 내부에 개념 모델로 배치한다. */
  showHousingModel(type: HousingModelType): void {
    this.showHousingModelExact(type);
    return;
    // 아래 코드는 이전 단일 직사각형 모델 구현이다. 정확 형상 구현으로 대체됨.
    const cmd = this.lastMassCommand!;
    if (!cmd || cmd.flat_only) throw new Error("추천 모델을 배치할 유효한 건축 매스가 없습니다.");
    this.removeAll(this.housingModelIds);

    const supplied = cmd.footprint_geometry
      ? largestRing(cmd.footprint_geometry as GeoJSONPolygon)
      : null;
    const envelope = (supplied ?? largestRing(cmd.geometry))!;
    if (!envelope) throw new Error("건축 가능 영역을 찾지 못했습니다.");

    const spec = {
      detached: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#F5E6C8", roof: "#8D3B2F" },
      lowrise: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#E0E6EA", roof: "#455A64" },
      slim: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#D8E6F3", roof: "#263F5A" },
    }[type];
    const fallbackCenter = centroid(envelope);
    const modelCenter: [number, number] = [
      cmd.anchor?.lon ?? fallbackCenter[0],
      cmd.anchor?.lat ?? fallbackCenter[1],
    ];
    const footprint = inscribedRectangle(envelope, modelCenter, spec.occupancy, spec.maxAspect);
    const flat = footprint.flatMap(([lon, lat]) => [lon, lat]);
    const ws3d = window.ws3d;
    const relative = (window as any).Cesium?.HeightReference?.RELATIVE_TO_GROUND ?? 2;
    const floorHeight = cmd.height_m / Math.max(1, cmd.floors);
    const fullFloors = Math.max(0, Math.min(cmd.floors, cmd.full_floors ?? cmd.floors));
    const topRatio = Math.max(0, Math.min(1, cmd.top_floor_ratio ?? 1));
    const hasPartialTop = fullFloors < cmd.floors && topRatio > 0 && topRatio < 0.999;
    const lowerHeight = hasPartialTop ? fullFloors * floorHeight : cmd.height_m;
    const height = cmd.height_m;
    const bodyColor = ws3d.common.Color.fromCssColorString(spec.body);
    const roofColor = ws3d.common.Color.fromCssColorString(spec.roof);

    // 기존 초록 매스는 법정 한계 공간이라는 의미로 옅게 남긴다.
    if (this.focusEntity?.polygon) {
      this.focusEntity.polygon.material = ws3d.common.Color
        .fromCssColorString(cmd.color)
        .withAlpha(0.13);
    }

    const bodyId = `housing-body-${Date.now()}`;
    const roofId = `housing-roof-${Date.now()}`;
    this.viewer.entities.add({
      id: bodyId,
      polygon: {
        hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
        height: lowerHeight,
        heightReference: relative,
        extrudedHeight: 0.5,
        extrudedHeightReference: relative,
        material: bodyColor.withAlpha(0.96),
        outline: true,
        outlineColor: ws3d.common.Color.DARKGRAY,
        closeTop: true,
        closeBottom: true,
      },
    });
    let roofFootprint = footprint;
    if (hasPartialTop) {
      roofFootprint = scalePolygon(footprint, topRatio);
      const topId = `housing-partial-top-${Date.now()}`;
      this.viewer.entities.add({
        id: topId,
        polygon: {
          hierarchy: ws3d.common.Cartesian3.fromDegreesArray(
            roofFootprint.flatMap(([lon, lat]) => [lon, lat]),
          ),
          height,
          heightReference: relative,
          extrudedHeight: lowerHeight,
          extrudedHeightReference: relative,
          material: bodyColor.withAlpha(0.96),
          outline: true,
          outlineColor: ws3d.common.Color.DARKGRAY,
          closeTop: true,
          closeBottom: true,
        },
      });
      this.housingModelIds.push(topId);
    }
    this.viewer.entities.add({
      id: roofId,
      polygon: {
        hierarchy: ws3d.common.Cartesian3.fromDegreesArray(
          roofFootprint.flatMap(([lon, lat]) => [lon, lat]),
        ),
        height: height + 0.35,
        heightReference: relative,
        material: roofColor.withAlpha(1),
      },
    });
    this.housingModelIds.push(bodyId, roofId);

    // 직사각형 네 모서리에서 실제 폭·깊이·방향을 계산해 층선과 창호를 얹는다.
    const p0 = footprint[0];
    const p1 = footprint[1];
    const p2 = footprint[2];
    const center: [number, number] = [
      (p0[0] + p1[0] + p2[0] + footprint[3][0]) / 4,
      (p0[1] + p1[1] + p2[1] + footprint[3][1]) / 4,
    ];
    const cosLat = Math.cos((center[1] * Math.PI) / 180);
    const meters = (a: number[], b: number[]) => {
      const east = (b[0] - a[0]) * 111320 * cosLat;
      const north = (b[1] - a[1]) * 111320;
      return { east, north, length: Math.hypot(east, north) };
    };
    const edgeU = meters(p0, p1);
    const edgeV = meters(p1, p2);
    const width = edgeU.length;
    const depth = edgeV.length;
    const ux = edgeU.east / width;
    const uy = edgeU.north / width;
    const vx = edgeV.east / depth;
    const vy = edgeV.north / depth;
    const offsetPoint = (u: number, v: number, z: number) => {
      const east = ux * u + vx * v;
      const north = uy * u + vy * v;
      return ws3d.common.Cartesian3.fromDegrees(
        center[0] + east / (111320 * cosLat),
        center[1] + north / 111320,
        z,
      );
    };
    const heading = Math.atan2(edgeU.east, edgeU.north) - Math.PI / 2;
    let detailNo = 0;
    const addBox = (
      u: number,
      v: number,
      z: number,
      dimensions: [number, number, number],
      material: any,
      roll = 0,
    ) => {
      const position = offsetPoint(u, v, z);
      const hpr = ws3d.common.HeadingPitchRoll;
      const transforms = ws3d.common.Transforms;
      const orientation = hpr && transforms?.headingPitchRollQuaternion
        ? transforms.headingPitchRollQuaternion(position, new hpr(heading, 0, roll))
        : undefined;
      const id = `housing-detail-${Date.now()}-${detailNo++}`;
      this.viewer.entities.add({
        id,
        position,
        ...(orientation ? { orientation } : {}),
        box: {
          dimensions: new ws3d.common.Cartesian3(...dimensions),
          material,
          outline: false,
          heightReference: relative,
        },
      });
      this.housingModelIds.push(id);
    };

    const floorCount = Math.max(1, fullFloors || spec.floors);
    const bandColor = ws3d.common.Color.fromCssColorString("#8C9295").withAlpha(0.9);
    const glassColor = ws3d.common.Color.fromCssColorString("#4A90B8").withAlpha(0.95);
    const frameColor = ws3d.common.Color.fromCssColorString("#E8F1F5").withAlpha(1);
    const doorColor = ws3d.common.Color.fromCssColorString("#70452F").withAlpha(1);

    // 층별 슬래브 띠가 층수를 읽히게 한다.
    for (let floor = 1; floor < floorCount; floor += 1) {
      addBox(0, 0, floor * floorHeight + 0.5, [width + 0.18, depth + 0.18, 0.14], bandColor);
    }

    // 네 입면에 층별 창호를 배치한다. 과도한 엔티티 생성을 막아 면당 최대 5개.
    const windowsU = Math.max(2, Math.min(5, Math.floor(width / 2.6)));
    const windowsV = Math.max(1, Math.min(4, Math.floor(depth / 2.8)));
    const windowWidth = Math.min(1.35, Math.max(0.8, width / (windowsU * 1.8)));
    for (let floor = 0; floor < floorCount; floor += 1) {
      const windowZ = 0.5 + floor * floorHeight + Math.min(1.75, floorHeight * 0.55);
      for (let i = 0; i < windowsU; i += 1) {
        const u = ((i + 1) / (windowsU + 1) - 0.5) * width;
        for (const side of [-1, 1]) {
          addBox(u, side * (depth / 2 + 0.04), windowZ, [windowWidth + 0.12, 0.1, 1.35], frameColor);
          addBox(u, side * (depth / 2 + 0.1), windowZ, [windowWidth, 0.08, 1.18], glassColor);
        }
      }
      for (let i = 0; i < windowsV; i += 1) {
        const v = ((i + 1) / (windowsV + 1) - 0.5) * depth;
        for (const side of [-1, 1]) {
          addBox(side * (width / 2 + 0.04), v, windowZ, [0.1, windowWidth + 0.12, 1.35], frameColor);
          addBox(side * (width / 2 + 0.1), v, windowZ, [0.08, windowWidth, 1.18], glassColor);
        }
      }
    }
    // 1층 정면 출입문.
    addBox(0, -(depth / 2 + 0.11), 0.5 + Math.min(1.15, floorHeight / 2), [1.25, 0.12, 2.2], doorColor);

    const roofScale = hasPartialTop ? Math.sqrt(topRatio) : 1;
    const roofWidth = width * roofScale;
    const roofDepth = depth * roofScale;
    if (type === "detached") {
      // 이 VWorld 번들에서는 box의 roll 축이 지역 ENU가 아닌 방식으로 적용돼
      // 경사지붕판이 하늘로 길게 뻗었다. 회전 없는 처마·상부 지붕·용마루로
      // 안정적인 단독주택 지붕 실루엣을 만든다.
      const availableRoof = Math.max(0.35, cmd.height_m - height);
      const upperRise = Math.min(0.65, availableRoof * 0.55);
      addBox(0, 0, height + 0.42, [roofWidth + 0.9, roofDepth + 0.9, 0.2], roofColor);
      addBox(0, 0, height + 0.45 + upperRise / 2, [roofWidth + 0.45, roofDepth * 0.58, upperRise], roofColor);
      addBox(0, 0, height + 0.5 + upperRise, [roofWidth + 0.65, 0.22, 0.2], roofColor);
    } else {
      // 공동주택은 옥상 파라펫을 표현한다.
      addBox(0, -roofDepth / 2, height + 0.75, [roofWidth + 0.35, 0.18, 0.8], roofColor);
      addBox(0, roofDepth / 2, height + 0.75, [roofWidth + 0.35, 0.18, 0.8], roofColor);
      addBox(-roofWidth / 2, 0, height + 0.75, [0.18, roofDepth, 0.8], roofColor);
      addBox(roofWidth / 2, 0, height + 0.75, [0.18, roofDepth, 0.8], roofColor);
    }
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 추천 주택 모델 배치 (${type}, ${spec.floors}층)`);
  }

  /** 건폐율 매스 형상을 그대로 사용해 면적·부분 최상층을 정확히 표현한다. */
  private showHousingModelExact(type: HousingModelType): void {
    const cmd = this.lastMassCommand;
    if (!cmd || cmd.flat_only) throw new Error("추천 모델을 배치할 유효한 건축 매스가 없습니다.");
    this.removeAll(this.housingModelIds);

    const footprint = cmd.footprint_geometry ? largestRing(cmd.footprint_geometry) : null;
    if (!footprint) throw new Error("건폐율 적용 건축면적 형상을 찾지 못했습니다.");
    const topRatio = Math.max(0, Math.min(1, cmd.top_floor_ratio ?? 1));
    const fullFloors = Math.max(0, Math.min(cmd.floors, cmd.full_floors ?? cmd.floors));
    const hasPartialTop = fullFloors < cmd.floors && topRatio > 0 && topRatio < 0.999;
    const topFootprint = hasPartialTop
      ? (cmd.top_footprint_geometry ? largestRing(cmd.top_footprint_geometry) : null) ?? scalePolygon(footprint, topRatio)
      : footprint;
    const floorHeight = cmd.height_m / Math.max(1, cmd.floors);
    const lowerHeight = hasPartialTop ? fullFloors * floorHeight : cmd.height_m;
    const ws3d = window.ws3d;
    // 창문마다 지형 상대고도를 쓰면 경사면/LOD 차이로 같은 층에서도 높이가
    // 달라진다. 건물 전체가 공유할 하나의 기준 표고를 정해 절대고도로 그린다.
    const heightNone = (window as any).Cesium?.HeightReference?.NONE ?? 0;
    const footprintCenter = centroid(footprint);
    const groundSamples = [footprintCenter, ...footprint.slice(0, -1)]
      .map(([lon, lat]) => this.terrainHeight(lon, lat))
      .filter((value) => Number.isFinite(value))
      .sort((a, b) => a - b);
    const commonGround = groundSamples.length
      ? groundSamples[Math.floor(groundSamples.length / 2)]
      : this.terrainHeight(footprintCenter[0], footprintCenter[1]);
    const palette = {
      detached: { body: "#E8D8B8", roof: "#9E3F2C" },
      lowrise: { body: "#D9E0E3", roof: "#455A64" },
      slim: { body: "#CFDFEC", roof: "#29475F" },
    }[type];
    const bodyColor = ws3d.common.Color.fromCssColorString(palette.body);
    const roofColor = ws3d.common.Color.fromCssColorString(palette.roof);
    const bandColor = ws3d.common.Color.fromCssColorString("#8B9295");
    const glassColor = ws3d.common.Color.fromCssColorString("#3F86AD");
    const frameColor = ws3d.common.Color.fromCssColorString("#EEF5F7");
    const doorColor = ws3d.common.Color.fromCssColorString("#6C402D");
    let serial = 0;
    const addEntity = (definition: any) => {
      const id = `housing-exact-${Date.now()}-${serial++}`;
      this.viewer.entities.add({ id, ...definition });
      this.housingModelIds.push(id);
      return id;
    };
    const flat = (ring: number[][]) => ring.flatMap(([lon, lat]) => [lon, lat]);
    const addLayer = (ring: number[][], bottom: number, top: number, material: any) => {
      addEntity({
        polygon: {
          hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat(ring)),
          height: commonGround + top,
          heightReference: heightNone,
          extrudedHeight: commonGround + bottom,
          extrudedHeightReference: heightNone,
          material,
          outline: true,
          outlineColor: bandColor,
          closeTop: true,
          closeBottom: true,
        },
      });
    };

    // 완전한 층은 건폐율로 산출한 바닥 형상을 그대로 사용한다.
    // 법정 한계 매스와 실제 건물이 같은 표면을 공유하는 구간에서도 주황색
    // 비교 영역이 완전히 가려지지 않도록 건물 외피에 약간의 투과성을 둔다.
    if (fullFloors > 0) addLayer(footprint, 0.5, lowerHeight, bodyColor.withAlpha(0.86));
    // 소수층은 백엔드에서 다시 내부 이격해 목표 면적비를 정확히 맞춘 형상이다.
    if (hasPartialTop) addLayer(topFootprint, lowerHeight, cmd.height_m, bodyColor.withAlpha(0.86));

    // 각 층 경계에 얇은 슬래브 띠를 넣어 외관에서도 층수를 읽을 수 있게 한다.
    for (let floor = 1; floor <= fullFloors; floor += 1) {
      const z = floor * floorHeight;
      addLayer(footprint, z - 0.07, z + 0.07, bandColor.withAlpha(0.9));
    }
    if (hasPartialTop) {
      addLayer(topFootprint, lowerHeight - 0.06, lowerHeight + 0.08, bandColor.withAlpha(0.9));
    }

    // 최상층 실제 형상에 맞춘 지붕.
    addLayer(topFootprint, cmd.height_m + 0.18, cmd.height_m + 0.42, roofColor.withAlpha(1));

    const cosLatFor = (lat: number) => Math.cos((lat * Math.PI) / 180);
    const edgeInfo = (ring: number[][]) => ring.slice(0, -1).map((a, index) => {
      const b = ring[index + 1] ?? ring[0];
      const midLat = (a[1] + b[1]) / 2;
      const east = (b[0] - a[0]) * 111320 * cosLatFor(midLat);
      const north = (b[1] - a[1]) * 111320;
      return { a, b, length: Math.hypot(east, north), heading: Math.atan2(east, north) - Math.PI / 2 };
    }).filter((edge) => edge.length >= 2.4).sort((a, b) => b.length - a.length).slice(0, 32);

    const addFacadeBox = (
      lon: number, lat: number, z: number, heading: number,
      dimensions: [number, number, number], material: any,
    ) => {
      const position = ws3d.common.Cartesian3.fromDegrees(lon, lat, commonGround + z);
      const HPR = ws3d.common.HeadingPitchRoll;
      const transforms = ws3d.common.Transforms;
      const orientation = HPR && transforms?.headingPitchRollQuaternion
        ? transforms.headingPitchRollQuaternion(position, new HPR(heading, 0, 0))
        : undefined;
      addEntity({
        position,
        ...(orientation ? { orientation } : {}),
        box: {
          dimensions: new ws3d.common.Cartesian3(...dimensions),
          material,
          heightReference: heightNone,
        },
      });
    };

    // 가장 긴 외벽을 개념 모델의 정면으로 보고 1층 중앙에 주출입구를 둔다.
    // 창문 배치에서도 같은 면을 사용해 출입문과 창문이 겹치지 않게 한다.
    const entrance = edgeInfo(footprint)[0];
    const sameEdge = (left: any, right: any) =>
      !!left && !!right &&
      left.a[0] === right.a[0] && left.a[1] === right.a[1] &&
      left.b[0] === right.b[0] && left.b[1] === right.b[1];

    const addWindows = (ring: number[][], floor: number) => {
      // box position은 창문의 중심 높이다. 이전 55%(최대 1.7m)는 창틀 상단이
      // 다음 층 슬래브에 걸쳐 보였으므로, 층 바닥에서 약 42% 지점으로 내린다.
      const z = 0.5 + floor * floorHeight + Math.min(1.35, floorHeight * 0.42);
      for (const edge of edgeInfo(ring)) {
        // 긴 정면도 최대 6개에서 끊지 않고 약 3m 간격으로 고르게 배치한다.
        const count = Math.max(1, Math.min(18, Math.floor(edge.length / 3)));
        const windowWidth = Math.min(1.35, Math.max(0.75, edge.length / (count * 2)));
        for (let i = 0; i < count; i += 1) {
          const t = (i + 1) / (count + 1);
          // 1층 정면 중앙은 출입문과 좌우 여유 폭을 위해 비운다.
          if (floor === 0 && sameEdge(edge, entrance) && Math.abs(t - 0.5) * edge.length < 1.65) {
            continue;
          }
          const lon = edge.a[0] + (edge.b[0] - edge.a[0]) * t;
          const lat = edge.a[1] + (edge.b[1] - edge.a[1]) * t;
          addFacadeBox(lon, lat, z, edge.heading, [windowWidth + 0.12, 0.13, 1.3], frameColor);
          addFacadeBox(lon, lat, z, edge.heading, [windowWidth, 0.18, 1.12], glassColor);
        }
      }
    };
    for (let floor = 0; floor < fullFloors; floor += 1) addWindows(footprint, floor);
    if (hasPartialTop) addWindows(topFootprint, fullFloors);

    // 가장 긴 1층 벽 중앙에 출입문을 둔다.
    if (entrance) {
      addFacadeBox(
        (entrance.a[0] + entrance.b[0]) / 2,
        (entrance.a[1] + entrance.b[1]) / 2,
        1.6,
        entrance.heading,
        [1.35, 0.2, 2.2],
        doorColor,
      );
    }

    if (this.focusEntity?.polygon) {
      const envelopeColor = ws3d.common.Color.fromCssColorString(cmd.color);
      // 추천 건물 뒤에서도 법정 최대 매스가 비교 기준으로 확실히 보여야 한다.
      // 실제 건물과 같은 좌표에 겹치는 면이 있으므로 반투명도는 유지하되
      // 위성영상 위에서도 식별 가능한 농도로 표시한다.
      this.focusEntity.show = true;
      this.focusEntity.polygon.material = envelopeColor.withAlpha(0.23);
      this.focusEntity.polygon.outline = true;
      this.focusEntity.polygon.outlineColor = envelopeColor.withAlpha(1);
    }

    // 법정 매스와 실제 건물은 상당 부분 동일한 면을 공유한다. 이 경우 반투명
    // 폴리곤은 깊이 버퍼에서 건물 뒤로 완전히 사라질 수 있으므로, 최대 매스의
    // 바닥·천장·수직 모서리를 별도 3D 케이지로 그려 비교 영역을 확실히 남긴다.
    const envelopeColor = ws3d.common.Color.fromCssColorString(cmd.color);
    const cageBottom = commonGround + 0.35;
    const cageTop = commonGround + cmd.height_m + 0.65;
    const closedFootprint = footprint[0][0] === footprint[footprint.length - 1][0] &&
      footprint[0][1] === footprint[footprint.length - 1][1]
      ? footprint
      : [...footprint, footprint[0]];
    const cagePolyline = (points: number[][]) => addEntity({
      polyline: {
        positions: points.map(([lon, lat, height]) =>
          ws3d.common.Cartesian3.fromDegrees(lon, lat, height)),
        width: 1.5,
        material: envelopeColor.withAlpha(1),
        depthFailMaterial: envelopeColor.withAlpha(1),
        clampToGround: false,
      },
    });
    cagePolyline(closedFootprint.map(([lon, lat]) => [lon, lat, cageBottom]));
    cagePolyline(closedFootprint.map(([lon, lat]) => [lon, lat, cageTop]));
    for (const [lon, lat] of footprint.slice(0, -1)) {
      cagePolyline([
        [lon, lat, cageBottom],
        [lon, lat, cageTop],
      ]);
    }

    // 선만 있는 철골처럼 보이지 않도록 법정 한계 공간의 측면과 천장에
    // 매우 옅은 주황색 반투명 면을 별도로 더한다.
    addEntity({
      wall: {
        positions: ws3d.common.Cartesian3.fromDegreesArray(
          closedFootprint.flatMap(([lon, lat]) => [lon, lat]),
        ),
        minimumHeights: closedFootprint.map(() => cageBottom),
        maximumHeights: closedFootprint.map(() => cageTop),
        material: envelopeColor.withAlpha(0.18),
        outline: false,
      },
    });
    addEntity({
      polygon: {
        hierarchy: ws3d.common.Cartesian3.fromDegreesArrayHeights(
          closedFootprint.flatMap(([lon, lat]) => [lon, lat, cageTop]),
        ),
        perPositionHeight: true,
        material: envelopeColor.withAlpha(0.16),
        outline: false,
        closeTop: true,
        closeBottom: false,
      },
    });

    this.viewer.scene?.requestRender?.();
    this.note(
      `✓ 건폐율 형상 그대로 모델 배치 · ${fullFloors}개 전체층` +
      (hasPartialTop ? ` + 최상층 ${Math.round(topRatio * 100)}%` : "") +
      ` · 공통 기준표고 ${commonGround.toFixed(1)}m`,
    );
  }

  /**
   * 생성된 geom 객체의 내부 그래픽 id 를 보관한다(나중에 지우기 위해).
   *
   * id 가 없다는 것은 create() 가 실제로는 아무것도 그리지 않았다는 뜻이다.
   * 엔진은 이 경우 예외를 던지지 않으므로 여기서 명시적으로 알린다.
   */
  private push(sink: string[], geom: any, what: string): void {
    const id = geom?.ws3dGraphics?.id;
    if (id) {
      sink.push(id);
      this.note(`✓ ${what} 생성됨`);
    } else {
      this.note(`✗ ${what} 미생성 (ws3dGraphics 없음)`);
    }
  }

  /** 라벨. 지원 형태가 버전마다 달라 실패해도 도형은 유지한다. */
  private addLabel(
    lon: number,
    lat: number,
    height: number,
    text: string,
    sink: string[],
  ): void {
    try {
      const p = new window.vw.geom.PointZ(new window.vw.CoordZ(lon, lat, height));
      p.setCaption?.(text);
      p.setFontSize?.(15);
      p.create();
      this.push(sink, p, "라벨");
    } catch (err) {
      console.warn("[MapBridge] 라벨 생성 실패(무시):", err);
    }
  }

  private removeAll(ids: string[]): void {
    const om = this.viewer.objectManager;
    for (const id of ids) {
      try {
        // 직접 만든 Cesium Entity와 VWorld geom 객체를 모두 정리한다.
        if (this.viewer.entities?.removeById?.(id)) continue;
        om.removeGeometryById(id);
      } catch {
        // 이미 사라진 객체 — 무시
      }
    }
    ids.length = 0;
  }

  /**
   * 지도 위 한 지점(경위도+고도)이 화면의 어느 픽셀에 있는지 돌려준다.
   * 결과 패널을 건물 바로 위에 띄우는 데 쓴다.
   * 화면 밖이거나 지구 뒤편이면 null.
   */
  /**
   * 지면 위 높이를 화면 좌표로 투영한다.
   *
   * 백엔드가 보내는 anchor 높이는 지면 기준인데 toScreen 은 해수면 절대고도를
   * 받는다. 그냥 넘기면 표고 140m 인 곳에서 패널이 그만큼 땅으로 꺼진다.
   */
  toScreenAboveGround(
    lon: number,
    lat: number,
    heightAboveGround = 0,
  ): { x: number; y: number } | null {
    return this.toScreen(lon, lat, this.terrainHeight(lon, lat) + heightAboveGround);
  }

  toScreen(lon: number, lat: number, height = 0): { x: number; y: number } | null {
    try {
      const ws3d = window.ws3d;
      const world = ws3d.common.Cartesian3.fromDegrees(lon, lat, height);
      const win =
        this.viewer.scene?.cartesianToCanvasCoordinates?.(world) ??
        ws3d.common.SceneTransforms?.wgs84ToWindowCoordinates?.(this.viewer.scene, world);
      if (!win) return null;
      return { x: win.x, y: win.y };
    } catch {
      return null;
    }
  }

  /** 카메라가 움직일 때마다 콜백. 패널을 건물에 붙여 따라다니게 한다. */
  onCameraChange(cb: () => void): () => void {
    const ev = this.viewer.scene?.camera?.changed;
    try {
      ev?.addEventListener(cb);
      // postRender 가 더 촘촘하다 — 비행 중에도 매끄럽게 따라간다
      this.viewer.scene?.postRender?.addEventListener(cb);
    } catch {
      /* 이벤트가 없으면 정적 위치로 둔다 */
    }
    return () => {
      try {
        ev?.removeEventListener(cb);
        this.viewer.scene?.postRender?.removeEventListener(cb);
      } catch {
        /* 무시 */
      }
    };
  }

  /** 지도에서 드래그가 아닌 단순 클릭 지점의 경위도를 전달한다. */
  onMapClick(
    cb: (lon: number, lat: number) => void,
    onError?: (message: string) => void,
  ): () => void {
    const canvas = this.viewer.scene?.canvas as HTMLCanvasElement | undefined;
    if (!canvas) return () => {};
    const ws3d = window.ws3d;
    const handler = new ws3d.common.ScreenSpaceEventHandler(canvas);
    const onClick = (movement: { position?: any }) => {
      try {
        const screen = movement.position;
        if (!screen) return;
        const scene = this.viewer.scene;
        // 경사진 3D 화면에서는 가상 매스뿐 아니라 VWorld 기존 건물도 지면을
        // 가린다. globe.pick만 사용하면 모든 건물을 관통해 뒤쪽 필지를 고른다.
        // 깊이 버퍼가 지원되면 화면에 실제로 보이는 표면 좌표를 항상 우선한다.
        let world = null;
        if (scene.pickPositionSupported) {
          try {
            world = scene.pickPosition?.(screen) ?? null;
          } catch {
            // 깊이값이 없는 하늘·미로딩 타일 등은 아래 지면 교차점으로 보완한다.
          }
        }
        if (!world) {
          const ray = scene.camera.getPickRay(screen);
          world = ray ? scene.globe.pick(ray, scene) : null;
        }
        if (!world) return;
        const carto = ws3d.common.Cartographic.fromCartesian(world);
        const lon = ws3d.common.CesiumMath.toDegrees(carto.longitude);
        const lat = ws3d.common.CesiumMath.toDegrees(carto.latitude);
        // 좌표 선택 사실은 즉시 전달한다. 경계 API나 렌더링이 늦더라도 입력창
        // 안내까지 함께 멈춰 사용자가 클릭이 안 된 것으로 오해하지 않게 한다.
        cb(lon, lat);
        void this.selectParcelAt(lon, lat)
          .catch((error: unknown) => {
            const message = error instanceof Error ? error.message : String(error);
            onError?.(`선택한 필지 경계를 불러오지 못했습니다: ${message}`);
          });
      } catch (err) {
        console.warn("[MapBridge] 지도 클릭 좌표 변환 실패:", err);
      }
    };
    handler.setInputAction(onClick, ws3d.common.ScreenSpaceEventType.LEFT_CLICK);
    return () => {
      if (!handler.isDestroyed?.()) handler.destroy?.();
    };
  }

  /** 클릭한 필지를 즉시 선명하게 선택 표시한다. */
  private async selectParcelAt(lon: number, lat: number): Promise<void> {
    const response = await fetch(`/api/parcel-at?lon=${lon}&lat=${lat}`);
    if (!response.ok) {
      const detail = (await response.text()).slice(0, 160);
      throw new Error(`HTTP ${response.status}${detail ? ` - ${detail}` : ""}`);
    }
    const parcel = await response.json();
    if (!parcel?.geometry) throw new Error("응답에 필지 geometry가 없습니다.");
    this.clearParcel();
    this.highlightParcel({
      type: "highlight_parcel",
      geometry: parcel.geometry,
      pnu: parcel.pnu ?? "",
      label: parcel.jibun ?? "선택 필지",
      color: "#00BCD4",
    });
    const ring = largestRing(parcel.geometry);
    if (ring) {
      const [focusLon, focusLat] = centroid(ring);
      this.lastFocus = { lon: focusLon, lat: focusLat };
    }
  }

  /** 같은 3D 엔진에서 수직 시점 + 연속지적도 오버레이로 2D 선택 모드를 만든다. */
  async setViewMode(mode: "2d" | "3d"): Promise<void> {
    const ws3d = window.ws3d;
    const camera = this.viewer.scene.camera;
    const modeGeneration = ++this.modeGeneration;
    // 검색 후 예약된 상세 지형고 보정과 진행 중인 비행이 전환 카메라를
    // 뒤늦게 덮어쓰지 못하게 한다.
    ++this.cameraGeneration;
    camera.cancelFlight?.();

    // 전환 직전 화면 정중앙이 닿는 지면과 카메라 거리를 구한다. 검색했던
    // 필지 좌표가 아니라 사용자가 방금 드래그해 보고 있던 위치가 기준이다.
    const currentFocus = (): { lon: number; lat: number; range: number; world?: any } | null => {
      try {
        const canvas = this.viewer.scene.canvas;
        const center = new ws3d.common.Cartesian2(canvas.clientWidth / 2, canvas.clientHeight / 2);
        const ray = camera.getPickRay(center);
        const world = ray && this.viewer.scene.globe.pick(ray, this.viewer.scene);
        if (!world) return null;
        const carto = ws3d.common.Cartographic.fromCartesian(world);
        return {
          lon: ws3d.common.CesiumMath.toDegrees(carto.longitude),
          lat: ws3d.common.CesiumMath.toDegrees(carto.latitude),
          range: ws3d.common.Cartesian3.distance(camera.positionWC, world),
          world,
        };
      } catch {
        return null;
      }
    };

    if (mode === "2d") {
      const heading = Number.isFinite(camera.heading) ? camera.heading : 0;
      let focus = currentFocus();
      if (!focus && this.lastFocus) focus = { ...this.lastFocus, range: 700 };
      if (!focus) throw new Error("현재 화면의 지도 중심을 찾지 못했습니다.");
      this.saved3DView = {
        pitch: Number.isFinite(camera.pitch) ? camera.pitch : -Math.PI / 4,
        range: focus.range,
      };
      // VWorld 컨트롤은 Cesium SceneMode.2D와 호환되지 않아 morphTo2D 호출 시
      // longitude 오류로 렌더러가 중단된다. 3D SceneMode를 유지하고 카메라만
      // 수직으로 세워 안전한 2D 선택 화면을 만든다.
      const ground = this.terrainHeight(focus.lon, focus.lat);
      const height = Math.max(80, Math.min(5000, focus.range));
      camera.setView({
        destination: ws3d.common.Cartesian3.fromDegrees(focus.lon, focus.lat, ground + height),
        orientation: {
          heading,
          pitch: -Math.PI / 2,
          roll: 0,
        },
      });
      const count = await this.loadCadastre(focus.lon, focus.lat);
      if (modeGeneration !== this.modeGeneration) return;
      this.note(`✓ 2D 지적도 모드 전환 완료 (경계 ${count}개)`);
    } else {
      this.clearCadastre();
      if (this.saved3DView) {
        const focus = currentFocus();
        camera.cancelFlight?.();
        if (focus?.world) {
          const heading = Number.isFinite(camera.heading) ? camera.heading : 0;
          // 현재 2D 중심·회전은 유지하고, 3D에서 쓰던 기울기와 배율만 복원한다.
          camera.lookAt(
            focus.world,
            new ws3d.common.HeadingPitchRange(
              heading,
              this.saved3DView.pitch,
              this.saved3DView.range,
            ),
          );
          const position = ws3d.common.Cartesian3.clone(camera.positionWC);
          const direction = ws3d.common.Cartesian3.clone(camera.directionWC);
          const up = ws3d.common.Cartesian3.clone(camera.upWC);
          camera.lookAtTransform(ws3d.common.Matrix4.IDENTITY);
          camera.setView({ destination: position, orientation: { direction, up } });
        }
        this.saved3DView = null;
      }
      this.note("✓ 3D 지도 모드 전환 완료");
    }
  }

  private async loadCadastre(lon: number, lat: number): Promise<number> {
    this.clearCadastre();
    const dx = 0.004;
    const dy = 0.003;
    try {
      let response = await fetch(
        `/api/parcels?west=${lon - dx}&south=${lat - dy}&east=${lon + dx}&north=${lat + dy}`,
      );
      // 배포 서버의 FastAPI 프로세스가 아직 구버전이면 새 route가 404다.
      // 지도 표시 자체는 중단하지 않고, 브라우저에 이미 전달된 VWorld 키로
      // 동일 GetFeature를 직접 호출한다.
      if (response.status === 404 && this.vworldKey) {
        const query = new URLSearchParams({
          service: "data",
          request: "GetFeature",
          version: "2.0",
          key: this.vworldKey,
          domain: window.location.origin,
          data: "LP_PA_CBND_BUBUN",
          geomFilter: `BOX(${lon - dx},${lat - dy},${lon + dx},${lat + dy})`,
          geometry: "true",
          crs: "EPSG:4326",
          size: "1000",
          format: "json",
        });
        response = await fetch(`https://api.vworld.kr/req/data?${query.toString()}`);
      }
      if (!response.ok) {
        const detail = (await response.text()).slice(0, 160);
        throw new Error(`HTTP ${response.status}${detail ? ` - ${detail}` : ""}`);
      }
      const data = await response.json();
      const geometries: GeoJSONPolygon[] = Array.isArray(data.geometries)
        ? data.geometries
        : (data.response?.result?.featureCollection?.features ?? [])
            .map((feature: any) => feature?.geometry)
            .filter(Boolean);
      const ws3d = window.ws3d;
      // 주변 연속지적도는 선택 필지보다 뒤로 물러나 보이는 회청색 보조선이다.
      const lineColor = ws3d.common.Color.fromCssColorString("#90A4AE").withAlpha(0.9);
      for (const geometry of geometries) {
        for (const ring of outerRings(geometry)) {
          const id = `cadastre-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          this.viewer.entities.add({
            id,
            polyline: {
              positions: ws3d.common.Cartesian3.fromDegreesArray(
                ring.flatMap(([x, y]) => [x, y]),
              ),
              clampToGround: true,
              width: 1,
              material: lineColor,
              depthFailMaterial: lineColor,
            },
          });
          this.cadastreIds.push(id);
        }
      }
      if (this.cadastreIds.length === 0) {
        throw new Error("현재 화면 범위에서 조회된 필지 경계가 없습니다.");
      }
    } catch (err) {
      console.warn("[MapBridge] 연속지적도 조회 실패:", err);
      throw err;
    }
    return this.cadastreIds.length;
  }

  private clearCadastre(): void {
    this.removeAll(this.cadastreIds);
  }

  private clearMass(): void {
    this.cameraGeneration += 1;
    this.removeAll(this.massIds);
    this.removeAll(this.housingModelIds);
    this.focusEntity = null;
    this.lastMassCommand = null;
  }

  private clearParcel(): void {
    this.removeAll(this.parcelIds);
  }
}
