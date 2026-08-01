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
      /** 같은 필지 후속 질문에서 법적 진단 원본은 보존하고 현재 표시 범위만 바꾼다. */
      type: "set_panel_context";
      building_use: string;
      /** '가능한 건축물 전체'처럼 검토 범위가 바뀌면 배지도 그 범위의 판정으로 갱신한다. */
      verdict?: string;
      verdict_label?: string;
      verdict_color?: string;
    }
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
  | {
      /** 생태·자연도·재해위험지구 등 규제 레이어 중첩. geometry 가 있으면 지도에도
       *  깔고, 없으면(생태·자연도처럼 조각 도형이 없는 경우) 우하단 범례로만 안내한다. */
      type: "show_restriction_pieces";
      title: string;
      note?: string;
      pieces: Array<{
        label: string;
        share_pct: number;
        area_m2: number;
        color: string;
        geometry?: GeoJSONPolygon;
      }>;
    }
  | {
      /** 검은 박스 대신 지도에 직접 얹는 치수선·면적 라벨 */
      type: "show_dimensions";
      segments: Array<{ positions: number[][]; label: string; color?: string; width?: number; onTop?: boolean; height_m?: number }>;
      labels: Array<{ lon: number; lat: number; text: string; height?: number; offset?: boolean }>;
    }
  | {
      /** 자연어로 켠/끈 지도 레이어. 지정된 항목만 바꾼다(MapCanvas가 처리). */
      type: "set_layers";
      cadastre?: boolean;
      zoning?: boolean;
      slope?: boolean;
      dimensions?: boolean;
      /** 가능여부 팝업창 여닫기 (App이 처리): true=열기, false=닫기 */
      panel?: boolean;
    }
  | {
      /** 서버의 전국 DEM 분석값. 경사도 버튼을 켤 때 이 셀만 표시한다. */
      type: "set_slope_data";
      source: string;
      resolution_m: number;
      min_elevation_m: number;
      max_elevation_m: number;
      mean_elevation_m: number;
      max_slope_deg: number;
      mean_slope_deg: number;
      cells: Array<{
        geometry: GeoJSONPolygon;
        elevation_m: number;
        slope_deg: number;
      }>;
    }
  | {
      /** 자연어로 실행한 지도 도구(측정·내 위치). MapCanvas가 처리. */
      type: "run_tool";
      action: "measure_line" | "measure_area" | "measure_height" | "erase" | "my_location";
    }
  | {
      /** 화면 하단 지도 도구 메뉴를 자연어로 여닫는다. */
      type: "set_tool_menu";
      open: boolean;
    }
  | {
      /** 2D 필지 선택 화면과 3D 규모 화면을 자연어로 전환한다. */
      type: "set_view_mode";
      mode: "2d" | "3d";
    }
  | {
      /** 자연어로 동일 건물의 토공 전/평탄화 후 상태를 전환한다. */
      type: "set_earthwork_mode";
      mode: "original" | "graded";
    }
  | {
      /** 상세 건물 모델을 숨기고 진단에서 계산된 단순 LOD1 매스만 다시 표시한다. */
      type: "show_lod1";
    }
  | {
      /** 입체 건물을 숨기고 진단에서 계산된 건축면적 평면 윤곽만 표시한다. */
      type: "show_building_footprint";
    }
  | {
      /** 직전에 보던 상세 3D 모델을 복원하고, 없으면 진단 LOD1 매스를 복원한다. */
      type: "show_building_shape";
    }
  | {
      /** LOD1·상세 모델·건축면적 윤곽을 모두 숨긴다. */
      type: "hide_building_shape";
    }
  | {
      /** 자연어로 요청한 건물 하나만 지정 층수·토공 상태로 표시한다. */
      type: "show_housing_model";
      model: HousingModelType;
      floors?: number;
      earthwork_mode?: "original" | "graded";
      hide_envelope?: boolean;
    }
  | {
      /** 요청 시설이 개별 법령상 불가할 때 팝업에 띄우는 빨간 경고. App이 처리. */
      type: "verdict_warning";
      label: string;
      reason?: string;
    }
  | { type: "show_panel"; [k: string]: any };

/**
 * VWorld 연속지적도는 MultiPolygon 으로 온다 (한 필지가 여러 조각일 수 있다).
 * Polygon 도 들어올 수 있으므로 둘 다 받는다.
 */
export type GeoJSONPolygon =
  | { type: "Polygon"; coordinates: number[][][] }
  | { type: "MultiPolygon"; coordinates: number[][][][] };

// 주택 3종 + 공장·상가. 같은 렌더러를 팔레트·스타일만 바꿔 재사용한다.
export type HousingModelType =
  | "detached" | "lowrise" | "slim" | "factory" | "commercial" | "warehouse";

/** 경사지 정지(整地) 토공 추정 — 균형 계획고 기준 절토·성토. */
export interface EarthworkEstimate {
  platform_m: number; // 계획고(정지 기준면, 균형 절성토 = 평균 표고)
  cut_m3: number; // 절토(깎기)
  fill_m3: number; // 성토(쌓기)
  max_cut_m: number; // 최대 절토 깊이
  max_fill_m: number; // 최대 성토 높이
  area_m2: number; // 대상 면적(건축면적 근사)
}

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

/** 경사도(°) -> 색. 개발행위허가 통상 기준(15°/20°)을 구간 경계로 쓴다. */
function slopeColor(deg: number): string {
  if (deg < 5) return "#2E7D32"; // 완경사(초록)
  if (deg < 10) return "#9CCC65"; // 연두
  if (deg < 15) return "#FDD835"; // 노랑
  if (deg < 20) return "#FB8C00"; // 주황(허가 제한 근접)
  return "#E53935"; // 급경사(빨강)
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

/** 링의 볼록껍질(Andrew monotone chain). 오목한 자투리를 메워 한 덩어리로 만든다.
 * 볼록껍질은 아핀불변이라 lon/lat 좌표에서 직접 계산해도 꼭짓점 집합이 옳다. */
function convexHull(ring: number[][]): number[][] {
  const pts = ring.slice();
  if (pts.length > 1) {
    const f = pts[0], l = pts[pts.length - 1];
    if (f[0] === l[0] && f[1] === l[1]) pts.pop();
  }
  const uniq = Array.from(new Map(pts.map((p) => [`${p[0]},${p[1]}`, p])).values());
  if (uniq.length < 3) return ring.map((p) => p.slice());
  uniq.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: number[], a: number[], b: number[]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: number[][] = [];
  for (const p of uniq) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: number[][] = [];
  for (let i = uniq.length - 1; i >= 0; i -= 1) {
    const p = uniq[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
  hull.push(hull[0].slice());
  return hull;
}

/** 규제로 오목하게(ㄷ자·빗살) 잘려 여러 동처럼 보이는 건축가능 형상을,
 * 같은 면적의 한 덩어리 볼록 매스로 바꾼다. 실제 잘린 형상은 법정 윤곽(주황)이
 * 별도로 보여주므로 개념 건물만 한 동으로 읽히게 하는 용도다. */
function coherentMass(ring: number[][]): number[][] {
  const hull = convexHull(ring);
  const hullArea = ringArea(hull);
  const origArea = ringArea(ring);
  if (hullArea <= 0 || origArea <= 0) return hull;
  // scalePolygon 은 '면적비'를 받아 sqrt 로 선형 축척한다 → 볼록매스를 원 면적으로.
  return scalePolygon(hull, Math.min(1, origArea / hullArea));
}

/**
 * 긴 외곽선 변을 일정 간격으로 나눈다.
 * 토공 벽을 꼭짓점 표고만으로 만들면 변 중간의 능선/골을 놓쳐 절토가 화면에서
 * 사라질 수 있다. 반환 링은 Cesium wall에 바로 쓸 수 있도록 닫혀 있다.
 */
function densifyRing(ring: number[][], maxSegmentM = 4): number[][] {
  if (ring.length < 2) return ring.slice();
  const source = ring.slice();
  const first = source[0];
  const last = source[source.length - 1];
  if (first[0] === last[0] && first[1] === last[1]) source.pop();
  if (source.length < 2) return ring.slice();

  const result: number[][] = [];
  for (let i = 0; i < source.length; i += 1) {
    const a = source[i];
    const b = source[(i + 1) % source.length];
    const lat = ((a[1] + b[1]) / 2) * Math.PI / 180;
    const dx = (b[0] - a[0]) * 111320 * Math.cos(lat);
    const dy = (b[1] - a[1]) * 111320;
    const parts = Math.max(1, Math.ceil(Math.hypot(dx, dy) / maxSegmentM));
    for (let j = 0; j < parts; j += 1) {
      const t = j / parts;
      result.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
  }
  result.push(result[0].slice());
  return result;
}

/** 중심에서 방사 방향으로 링을 meter만큼 확장한다(소규모 건축 배치용 근사 offset). */
function expandRingMeters(ring: number[][], meters: number): number[][] {
  if (ring.length < 3 || meters === 0) return ring.map((p) => p.slice());
  const source = ring.slice();
  const first = source[0];
  const last = source[source.length - 1];
  const wasClosed = first[0] === last[0] && first[1] === last[1];
  if (wasClosed) source.pop();
  const [cx, cy] = centroid(source);
  const cosLat = Math.max(0.01, Math.cos((cy * Math.PI) / 180));
  const expanded = source.map(([lon, lat]) => {
    const dx = (lon - cx) * 111320 * cosLat;
    const dy = (lat - cy) * 111320;
    const distance = Math.hypot(dx, dy);
    if (distance < 1e-6) return [lon, lat];
    const scale = (distance + meters) / distance;
    return [
      cx + (dx * scale) / (111320 * cosLat),
      cy + (dy * scale) / 111320,
    ];
  });
  if (wasClosed) expanded.push(expanded[0].slice());
  return expanded;
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

  private pointInRing(x: number, y: number, ring: number[][]): boolean {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }

  /** 건축면적 형상 위 지형표고의 중앙값. 건물 발판(계획고)을 여기에 두면 절토·성토가
   *  대략 균형을 이뤄 한쪽만(전부 성토 등) 되지 않는다. */
  private footprintTerrainMedian(footprint: number[][]): number | null {
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const p of footprint) {
      if (p[0] < minx) minx = p[0];
      if (p[0] > maxx) maxx = p[0];
      if (p[1] < miny) miny = p[1];
      if (p[1] > maxy) maxy = p[1];
    }
    const N = 12;
    const cellW = (maxx - minx) / N || 1e-9;
    const cellH = (maxy - miny) / N || 1e-9;
    const gs: number[] = [];
    for (let i = 0; i < N; i += 1) {
      for (let j = 0; j < N; j += 1) {
        const lon = minx + (i + 0.5) * cellW;
        const lat = miny + (j + 0.5) * cellH;
        if (!this.pointInRing(lon, lat, footprint)) continue;
        const g = this.terrainHeight(lon, lat);
        if (Number.isFinite(g)) gs.push(g);
      }
    }
    if (!gs.length) return null;
    gs.sort((a, b) => a - b);
    return gs[Math.floor(gs.length / 2)];
  }

  /**
   * 경사지에 건물을 앉힐 때 필요한 절토·성토(토공량) 개략 추정.
   * footprint(건축면적 형상) 위를 격자로 지형 표고를 샘플링하고, '균형 계획고
   * (평균 표고, 절토=성토가 대략 상쇄되는 높이)'를 기준으로 깎기·쌓기 부피를 적분한다.
   * 정밀 DEM·설계 계획고·법면·다짐이 아닌, 지형데이터 기반 사전 감(感)용이다.
   */
  private estimateEarthwork(footprint: number[][], platform: number): EarthworkEstimate {
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const p of footprint) {
      if (p[0] < minx) minx = p[0];
      if (p[0] > maxx) maxx = p[0];
      if (p[1] < miny) miny = p[1];
      if (p[1] > maxy) maxy = p[1];
    }
    const lat0 = (miny + maxy) / 2;
    const mLon = 111320 * Math.cos((lat0 * Math.PI) / 180);
    const mLat = 111320;
    const N = 14;
    const cellW = (maxx - minx) / N || 1e-9;
    const cellH = (maxy - miny) / N || 1e-9;
    const cellArea = cellW * mLon * cellH * mLat;
    const grounds: number[] = [];
    for (let i = 0; i < N; i += 1) {
      for (let j = 0; j < N; j += 1) {
        const lon = minx + (i + 0.5) * cellW;
        const lat = miny + (j + 0.5) * cellH;
        if (!this.pointInRing(lon, lat, footprint)) continue;
        const g = this.terrainHeight(lon, lat);
        if (Number.isFinite(g)) grounds.push(g);
      }
    }
    if (!grounds.length) {
      return { platform_m: platform, cut_m3: 0, fill_m3: 0, max_cut_m: 0, max_fill_m: 0, area_m2: 0 };
    }
    let cut = 0, fill = 0, maxCut = 0, maxFill = 0;
    for (const g of grounds) {
      const d = g - platform;
      if (d >= 0) {
        cut += d * cellArea;
        if (d > maxCut) maxCut = d;
      } else {
        fill += -d * cellArea;
        if (-d > maxFill) maxFill = -d;
      }
    }
    return {
      platform_m: platform,
      cut_m3: cut,
      fill_m3: fill,
      max_cut_m: maxCut,
      max_fill_m: maxFill,
      area_m2: grounds.length * cellArea,
    };
  }

  /**
   * 현재 로드된 VWorld 3D 지형에서 필지 표고·경사 참고치를 계산한다.
   * 정밀 DEM 분석이나 개발행위허가용 평균경사도 산정 결과는 아니다.
   */
  terrainSummary(geometry: GeoJSONPolygon): {
    available: boolean;
    min_elevation_m?: number;
    max_elevation_m?: number;
    relief_m?: number;
    max_sample_slope_deg?: number;
    sample_count?: number;
    source: string;
    caveat: string;
  } {
    const points = outerRings(geometry)
      .flatMap((ring) => ring.slice(0, -1))
      .filter((point) => point.length >= 2)
      .slice(0, 40);
    if (points.length < 2) {
      return {
        available: false,
        source: "VWorld 3D 지형",
        caveat: "필지 표본점을 만들 수 없습니다.",
      };
    }
    const samples = points.map(([lon, lat]) => ({
      lon,
      lat,
      height: this.terrainHeight(lon, lat),
    }));
    // 지형 타일 준비 전 getHeight가 전부 0을 돌려주는 경우를 평탄지로 오인하지 않는다.
    if (samples.every((sample) => sample.height === 0)) {
      return {
        available: false,
        source: "VWorld 3D 지형",
        caveat: "지형 타일이 아직 준비되지 않아 표고를 확인하지 못했습니다.",
      };
    }
    const heights = samples.map((sample) => sample.height);
    let maxSlope = 0;
    for (let i = 0; i < samples.length; i += 1) {
      for (let j = i + 1; j < samples.length; j += 1) {
        const a = samples[i];
        const b = samples[j];
        const meanLat = ((a.lat + b.lat) / 2) * Math.PI / 180;
        const east = (a.lon - b.lon) * 111320 * Math.cos(meanLat);
        const north = (a.lat - b.lat) * 111320;
        const horizontal = Math.hypot(east, north);
        if (horizontal < 1) continue;
        const slope = Math.atan(Math.abs(a.height - b.height) / horizontal) * 180 / Math.PI;
        maxSlope = Math.max(maxSlope, slope);
      }
    }
    const min = Math.min(...heights);
    const max = Math.max(...heights);
    return {
      available: true,
      min_elevation_m: Math.round(min * 10) / 10,
      max_elevation_m: Math.round(max * 10) / 10,
      relief_m: Math.round((max - min) * 10) / 10,
      max_sample_slope_deg: Math.round(maxSlope * 10) / 10,
      sample_count: samples.length,
      source: "VWorld 3D 지형 표본",
      caveat: "화면에 로드된 지형의 표본 참고치이며 허가용 평균경사도·표고 분석을 대체하지 않습니다.",
    };
  }

  /** ws3d.viewer — 엔진이 전역으로 노출하는 뷰어 네임스페이스 */
  private viewer: any;
  private parcelIds: string[] = [];
  private massIds: string[] = [];
  private housingModelIds: string[] = [];
  private cadastreIds: string[] = [];
  private cadastreEnabled = false;
  private cadastreMoveEndHandler: (() => void) | null = null;
  private cadastreReloadTimer: number | null = null;
  private cadastreLoadGeneration = 0;
  // 걸침 필지의 용도지역 조각 오버레이
  private zonePieceIds: string[] = [];
  private restrictionPieceIds: string[] = [];
  // 지형·배치·도로 접도 등 '공간에서 확인할 수치'를 건물 옆에 띄우는 라벨
  private siteNoteIds: string[] = [];
  // 치수선·면적 라벨 (검은 박스 대체)
  private dimensionIds: string[] = [];
  // 세그먼트 라벨(가로·세로·도로접촉·건축선/이격) 겹침 방지용 앵커 + 카메라 리스너.
  private dimLabelAnchors: { id: string; lon: number; lat: number; w: number; h: number }[] = [];
  private dimLabelDisposer: (() => void) | null = null;
  // 팝업 접기 시 숨겼다가 펼칠 때 다시 그리기 위해 마지막 치수 명령을 보관
  private lastDimensionsCommand: Extract<MapCommand, { type: "show_dimensions" }> | null = null;
  // 용도지역 주제도 오버레이
  private zoningOverlayIds: string[] = [];
  // 용도지역 라벨의 두 앵커(near=필지 근처, center=지역 중심)와 카메라 리스너.
  // 확대하면 center 로, 축소하면 near 로 라벨을 보간 이동한다.
  private zoningLabelAnchors: {
    id: string; nearLon: number; nearLat: number; centerLon: number; centerLat: number;
  }[] = [];
  private zoningLabelDisposer: (() => void) | null = null;
  // 경사도 격자
  private slopeGridIds: string[] = [];
  private slopeData: Extract<MapCommand, { type: "set_slope_data" }> | null = null;
  // 최근 진단 필지 기하 (경사도 격자를 그 안으로 자르기 위해 보관)
  private lastParcelGeometry: GeoJSONPolygon | null = null;
  private focusEntity: any = null;
  private lastMassCommand: Extract<MapCommand, { type: "extrude_mass" }> | null = null;
  private lastEarthwork: EarthworkEstimate | null = null;
  private earthworkMode: "original" | "graded" = "graded";
  private lastHousingModelType: HousingModelType | null = null;
  private terrainClipping: any = null;
  private previousTerrainClipping: any = null;
  private terrainClippingKind: "polygon" | "planes" | null = null;
  private earthworkPrimitives: any[] = [];
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

  private clearTerrainClipping(): void {
    const globe = this.viewer.scene?.globe;
    if (!globe) return;
    if (this.terrainClipping) {
      const key = this.terrainClippingKind === "polygon" ? "clippingPolygons" : "clippingPlanes";
      if (globe[key] === this.terrainClipping) globe[key] = this.previousTerrainClipping;
      try {
        if (!this.terrainClipping?.isDestroyed?.()) this.terrainClipping?.destroy?.();
      } catch {
        // VWorld 번들별 destroy 차이는 다음 렌더링을 막지 않게 무시한다.
      }
    }
    this.terrainClipping = null;
    this.previousTerrainClipping = null;
    this.terrainClippingKind = null;
  }

  private clearEarthworkPrimitives(): void {
    const primitives = this.viewer.scene?.primitives;
    for (const primitive of this.earthworkPrimitives) {
      try {
        if (primitives?.contains?.(primitive)) primitives.remove(primitive);
      } catch {
        // 이미 제거되었거나 VWorld가 소유권을 정리한 primitive는 건너뛴다.
      }
    }
    this.earthworkPrimitives.length = 0;
  }

  /**
   * 건축면적 내부의 원지형을 실제로 숨긴다.
   * 신형 Cesium은 다각형 클리핑, 구형 VWorld 번들은 수직 평면 묶음을 사용한다.
   */
  private applyTerrainClipping(footprint: number[][], platform: number): boolean {
    this.clearTerrainClipping();
    const globe = this.viewer.scene?.globe;
    const C = (window as any).Cesium;
    if (!globe || !C?.Cartesian3) return false;
    const ring = footprint.slice();
    if (
      ring.length > 1 &&
      ring[0][0] === ring[ring.length - 1][0] &&
      ring[0][1] === ring[ring.length - 1][1]
    ) ring.pop();
    if (ring.length < 3) return false;

    try {
      if (C.ClippingPolygon && C.ClippingPolygonCollection && "clippingPolygons" in globe) {
        const positions = ring.map(([lon, lat]) => C.Cartesian3.fromDegrees(lon, lat, platform));
        const clipping = new C.ClippingPolygonCollection({
          polygons: [new C.ClippingPolygon({ positions })],
        });
        this.previousTerrainClipping = globe.clippingPolygons;
        globe.clippingPolygons = clipping;
        this.terrainClipping = clipping;
        this.terrainClippingKind = "polygon";
        this.note("✓ VWorld 지형 절토 클리핑 적용 (polygon)");
        return true;
      }

      if (C.ClippingPlane && C.ClippingPlaneCollection && C.Plane && "clippingPlanes" in globe) {
        const centerLon = ring.reduce((sum, p) => sum + p[0], 0) / ring.length;
        const centerLat = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
        const center = C.Cartesian3.fromDegrees(centerLon, centerLat, platform);
        const planes = ring.map((point, i) => {
          const next = ring[(i + 1) % ring.length];
          const a = C.Cartesian3.fromDegrees(point[0], point[1], platform);
          const b = C.Cartesian3.fromDegrees(next[0], next[1], platform);
          const midpoint = C.Cartesian3.multiplyByScalar(
            C.Cartesian3.add(a, b, new C.Cartesian3()),
            0.5,
            new C.Cartesian3(),
          );
          const up = C.Cartesian3.normalize(midpoint, new C.Cartesian3());
          const edge = C.Cartesian3.normalize(
            C.Cartesian3.subtract(b, a, new C.Cartesian3()),
            new C.Cartesian3(),
          );
          let normal = C.Cartesian3.normalize(
            C.Cartesian3.cross(edge, up, new C.Cartesian3()),
            new C.Cartesian3(),
          );
          const towardCenter = C.Cartesian3.subtract(center, midpoint, new C.Cartesian3());
          if (C.Cartesian3.dot(normal, towardCenter) > 0) {
            normal = C.Cartesian3.negate(normal, new C.Cartesian3());
          }
          return C.ClippingPlane.fromPlane(C.Plane.fromPointNormal(midpoint, normal));
        });
        const clipping = new C.ClippingPlaneCollection({
          planes,
          unionClippingRegions: false,
          edgeColor: C.Color?.ORANGE,
          edgeWidth: 1,
        });
        this.previousTerrainClipping = globe.clippingPlanes;
        globe.clippingPlanes = clipping;
        this.terrainClipping = clipping;
        this.terrainClippingKind = "planes";
        this.note("✓ VWorld 지형 절토 클리핑 적용 (planes)");
        return true;
      }
    } catch (error) {
      this.note(`⚠ VWorld 지형 클리핑 실패: ${String(error)}`);
      this.clearTerrainClipping();
    }
    this.note("⚠ VWorld 번들이 지형 클리핑 API를 노출하지 않아 절토선으로 표시");
    return false;
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
            this.clearRestrictionPieces();
            this.clearSiteNotes();
            this.clearDimensions();
            this.clearSlopeGrid();
            this.slopeData = null;
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
          case "set_earthwork_mode":
            this.setEarthworkMode(cmd.mode);
            break;
          case "set_slope_data":
            this.slopeData = cmd;
            this.clearSlopeGrid();
            break;
          case "show_lod1":
            this.showLod1Only();
            break;
          case "show_building_footprint":
            this.showBuildingFootprintOnly();
            break;
          case "show_building_shape":
            this.restoreBuildingShape();
            break;
          case "hide_building_shape":
            this.hideBuildingShape();
            break;
          case "show_housing_model":
            this.showRequestedHousingModel(
              cmd.model,
              cmd.floors,
              cmd.earthwork_mode ?? "graded",
              cmd.hide_envelope ?? true,
            );
            break;
          case "show_zone_pieces":
            this.showZonePieces(cmd);
            break;
          case "show_restriction_pieces":
            this.showRestrictionPieces(cmd);
            break;
          case "show_dimensions":
            this.showDimensions(cmd);
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
    this.lastParcelGeometry = cmd.geometry; // 경사도 격자용
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

  private showRestrictionPieces(
    cmd: Extract<MapCommand, { type: "show_restriction_pieces" }>,
  ): void {
    const ws3d = window.ws3d;
    this.clearRestrictionPieces();
    for (const piece of cmd.pieces) {
      if (!piece.geometry) continue;  // 조각 도형이 없으면(생태·자연도 등) 범례로만 안내
      const pieceColor = ws3d.common.Color.fromCssColorString(piece.color);
      for (const ring of outerRings(piece.geometry)) {
        if (ring.length < 3) continue;
        const id = `map-restriction-piece-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        this.viewer.entities.add({
          id,
          polygon: {
            hierarchy: ws3d.common.Cartesian3.fromDegreesArray(
              ring.flatMap(([lon, lat]) => [lon, lat]),
            ),
            material: pieceColor.withAlpha(0.42),
            height: 0.8,
            heightReference: 2,
          },
        });
        this.restrictionPieceIds.push(id);
      }
    }
    this.viewer.scene?.requestRender?.();
    this.note(`✓ ${cmd.title} ${cmd.pieces.length}개 표시`);
  }

  private clearRestrictionPieces(): void {
    this.removeAll(this.restrictionPieceIds);
  }

  /**
   * 공간에서 확인할 수치(지형 표고·경사, 배치 제약, 도로 접도)를 건물 옆
   * 지도 위에 직접 라벨로 띄운다. 서술형 진단은 왼쪽 답변이 담당하고,
   * 이 라벨은 '어디에·얼마나'가 지도상에서 바로 읽혀야 하는 값만 담는다.
   *
   * lon/lat 는 건물 앵커, lines 는 표시할 문자열 목록. 빈 목록이면 지운다.
   */
  showSiteNotes(lon: number, lat: number, lines: string[]): void {
    this.clearSiteNotes();
    const clean = lines.filter((l) => l && l.trim());
    if (!clean.length) return;

    const ws3d = window.ws3d;
    const relativeToGround =
      (window as any).Cesium?.HeightReference?.RELATIVE_TO_GROUND ?? 2;
    const id = `map-site-note-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.viewer.entities.add({
      id,
      position: ws3d.common.Cartesian3.fromDegrees(lon, lat, 1),
      // 속성은 기존 지붕 마커 라벨에서 이미 동작이 검증된 것만 쓴다.
      // (backgroundPadding·NearFarScalar·숫자 origin 은 이 번들 Cesium 버전에서
      //  미검증이라, 잘못된 값이 라벨을 뒤집거나 예외를 낼 수 있어 제외)
      label: {
        text: clean.join("\n"),
        font: "13px 'Malgun Gothic', sans-serif",
        fillColor: ws3d.common.Color.WHITE,
        showBackground: true,
        backgroundColor: ws3d.common.Color.fromCssColorString("#0d1b2a").withAlpha(0.82),
        // 건물(앵커 가운데)과 겹치지 않게 화면상 왼쪽 아래로 밀어 배치
        pixelOffset: new ws3d.common.Cartesian2(-150, 44),
        heightReference: relativeToGround,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
    this.siteNoteIds.push(id);

    // 라벨이 어느 필지를 설명하는지 잇는 작은 기준점
    const dotId = `map-site-note-dot-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.viewer.entities.add({
      id: dotId,
      position: ws3d.common.Cartesian3.fromDegrees(lon, lat, 0.5),
      point: {
        pixelSize: 7,
        color: ws3d.common.Color.fromCssColorString("#4dd0e1"),
        outlineColor: ws3d.common.Color.WHITE,
        outlineWidth: 2,
        heightReference: relativeToGround,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
    this.siteNoteIds.push(dotId);
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 지도 위 공간 수치 라벨 ${clean.length}줄 표시`);
  }

  clearSiteNotes(): void {
    this.removeAll(this.siteNoteIds);
  }

  /**
   * 치수선·면적 라벨을 지도에 직접 그린다(검은 텍스트 박스 대체).
   * 노란 치수선(VWorld 측정선 느낌) + 중앙 값 라벨, 면적은 지점 라벨.
   */
  private showDimensions(cmd: Extract<MapCommand, { type: "show_dimensions" }>): void {
    this.lastDimensionsCommand = cmd; // 접기/펼치기 재표시용
    this.clearDimensions();
    const ws3d = window.ws3d;
    const relativeToGround =
      (window as any).Cesium?.HeightReference?.RELATIVE_TO_GROUND ?? 2;
    const yellow = ws3d.common.Color.fromCssColorString("#FFD400");
    const rid = () => Math.random().toString(36).slice(2);

    // 치수선 + 선 중앙 라벨
    for (const seg of cmd.segments) {
      if (!seg.positions || seg.positions.length < 2) continue;
      // 세그먼트별 색(건축선·이격선 등은 눈에 띄는 색). 기본은 노란색.
      const segColor = seg.color
        ? ws3d.common.Color.fromCssColorString(seg.color)
        : yellow;
      const isCustom = Boolean(seg.color);

      // 높이 치수선 — height_m 이 있으면 모서리(positions[0])에서 매스와 같은 기준
      // (지면+0.5m)으로 수직으로 올린다. 가로·세로가 만나는 모서리에 세워 3축을 이룬다.
      if (seg.height_m && seg.height_m > 0) {
        const [hlon, hlat] = seg.positions[0];
        const base = this.terrainHeight(hlon, hlat) + 0.5; // 매스 baseAboveGround 와 일치
        const top = base + seg.height_m;
        const vlineId = `map-dim-vline-${Date.now()}-${rid()}`;
        this.viewer.entities.add({
          id: vlineId,
          polyline: {
            positions: [
              ws3d.common.Cartesian3.fromDegrees(hlon, hlat, base),
              ws3d.common.Cartesian3.fromDegrees(hlon, hlat, top),
            ],
            width: seg.width ?? 4,
            material: segColor,
            depthFailMaterial: segColor,
          },
        });
        this.dimensionIds.push(vlineId);
        const vlabelId = `map-dim-vlabel-${Date.now()}-${rid()}`;
        this.viewer.entities.add({
          id: vlabelId,
          position: ws3d.common.Cartesian3.fromDegrees(hlon, hlat, base + seg.height_m / 2),
          label: {
            text: seg.label,
            font: "bold 13px 'Malgun Gothic', sans-serif",
            fillColor: ws3d.common.Color.WHITE,
            showBackground: true,
            backgroundColor: segColor.withAlpha(0.95),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
        this.dimensionIds.push(vlabelId);
        continue;
      }

      const lineId = `map-dim-line-${Date.now()}-${rid()}`;
      const flat = seg.positions.flatMap(([lon, lat]) => [lon, lat]);
      const linePoly: any = {
        positions: ws3d.common.Cartesian3.fromDegreesArray(flat),
        clampToGround: true,
        width: seg.width ?? (isCustom ? 5 : 3),
        material: segColor,
        depthFailMaterial: segColor,
      };
      // onTop: 지적 경계선(청록)보다 위에 그려 선면 전체가 그 색으로 보이게 한다.
      if (seg.onTop) linePoly.zIndex = 1000;
      this.viewer.entities.add({ id: lineId, polyline: linePoly });
      this.dimensionIds.push(lineId);

      const mid = seg.positions[Math.floor(seg.positions.length / 2)];
      const a = seg.positions[0];
      const b = seg.positions[seg.positions.length - 1];
      const midLon = (a[0] + b[0]) / 2;
      const midLat = (a[1] + b[1]) / 2;
      const labelId = `map-dim-label-${Date.now()}-${rid()}`;
      this.viewer.entities.add({
        id: labelId,
        position: ws3d.common.Cartesian3.fromDegrees(midLon, midLat, 1),
        label: {
          text: seg.label,
          font: isCustom ? "bold 13px 'Malgun Gothic', sans-serif" : "12px 'Malgun Gothic', sans-serif",
          fillColor: isCustom ? ws3d.common.Color.WHITE : ws3d.common.Color.BLACK,
          showBackground: true,
          backgroundColor: isCustom ? segColor.withAlpha(0.95) : yellow.withAlpha(0.92),
          // 치수선 라벨(가로/세로)은 살짝 위로 올려, 같은 지면의 '도로 접촉'
          // 라벨과 겹치지 않게 한다.
          pixelOffset: new ws3d.common.Cartesian2(0, -20),
          heightReference: relativeToGround,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this.dimensionIds.push(labelId);
      // 겹침 방지 대상으로 등록(글자수로 대략적 폭 추정).
      this.dimLabelAnchors.push({
        id: labelId,
        lon: midLon,
        lat: midLat,
        w: seg.label.length * 9 + 14,
        h: 22,
      });
      void mid;
    }

    // 면적·도로 등 지점 라벨
    for (const lab of cmd.labels) {
      const id = `map-dim-arealabel-${Date.now()}-${rid()}`;
      this.viewer.entities.add({
        id,
        // 면적 라벨은 팝업 앵커와 같은 높이(lab.height)에 둔다 — 확대해도 안 떨어진다.
        // 도로 등 height 없는 라벨만 지면 위 1m.
        position: ws3d.common.Cartesian3.fromDegrees(lab.lon, lab.lat, lab.height ?? 1),
        label: {
          text: lab.text,
          font: "13px 'Malgun Gothic', sans-serif",
          fillColor: ws3d.common.Color.WHITE,
          showBackground: true,
          backgroundColor: ws3d.common.Color.fromCssColorString("#0d1b2a").withAlpha(0.85),
          // 접기 버튼 → (간격) → 건축면적 → (간격) → 대지면적 순으로 규칙적으로.
          // 버튼 바로 밑에 붙지 않게 건축면적을 충분히 내리고, 대지면적은 그보다
          // 42px 더 아래로.
          pixelOffset: new ws3d.common.Cartesian2(
            0,
            lab.text.startsWith("건축면적")
              ? 44
              : lab.text.startsWith("대지면적")
                ? 86
                // '도로 접촉'은 지면 라벨이라 치수선 라벨과 겹친다 — 아래로 내린다.
                : lab.text.startsWith("도로 접촉")
                  ? 22
                  : 0,
          ),
          heightReference: relativeToGround,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this.dimensionIds.push(id);
    }
    // 세그먼트 라벨(가로·세로·도로접촉·건축선/이격) 겹침 방지 — 카메라가 움직일
    // 때마다 화면좌표로 겹침을 검사해 라벨을 화면상에서 밀어낸다. 회전·줌아웃에도
    // 안 붙는다. 면적 라벨(건축/대지면적)은 대상에서 제외한다.
    const declutter = () => {
      const C = (window as any).Cesium;
      const scene: any = this.viewer.scene;
      if (!C?.SceneTransforms || !scene?.camera) return;
      const toWin = C.SceneTransforms.wgs84ToWindowCoordinates?.bind(C.SceneTransforms)
        ?? C.SceneTransforms.worldToWindowCoordinates?.bind(C.SceneTransforms);
      if (!toWin) return;
      const camera = scene.camera;
      // 화면에 나타나는 순서(가까운 것 우선)로 자리를 먼저 잡는다.
      const placed: { x: number; y: number; w: number; h: number }[] = [];
      // 후보 오프셋(화면 px): 라벨이 길어서 세로로 쌓아 확실히 떨어뜨린다.
      const cands: [number, number][] = [
        [0, -22], [0, 22], [0, -48], [0, 48], [0, -74], [0, 74],
        [0, -100], [0, 100], [0, -126], [0, 126], [0, -152], [0, 152],
      ];
      const anchors = this.dimLabelAnchors
        .map((a) => {
          const gh = this.terrainHeight(a.lon, a.lat);
          const world = C.Cartesian3.fromDegrees(a.lon, a.lat, gh + 1);
          const toPt = C.Cartesian3.subtract(world, camera.positionWC, new C.Cartesian3());
          const front = C.Cartesian3.dot(toPt, camera.directionWC) > 0;
          const win = front ? toWin(scene, world) : undefined;
          const dist = C.Cartesian3.magnitude(toPt);
          return { a, win, dist };
        })
        .filter((x) => x.win)
        .sort((x, y) => x.dist - y.dist);
      for (const { a, win } of anchors) {
        const ent = this.viewer.entities.getById(a.id);
        if (!ent?.label) continue;
        let chosen = cands[0];
        for (const [cx, cy] of cands) {
          const rect = { x: win.x + cx - a.w / 2, y: win.y + cy - a.h / 2, w: a.w, h: a.h };
          const hit = placed.some(
            (p) => !(rect.x + rect.w < p.x || rect.x > p.x + p.w || rect.y + rect.h < p.y || rect.y > p.y + p.h),
          );
          if (!hit) { chosen = [cx, cy]; break; }
        }
        placed.push({ x: win.x + chosen[0] - a.w / 2, y: win.y + chosen[1] - a.h / 2, w: a.w, h: a.h });
        ent.label.pixelOffset = new C.Cartesian2(chosen[0], chosen[1]);
      }
    };
    declutter();
    this.dimLabelDisposer = this.onCameraChange(declutter);
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 치수선 ${cmd.segments.length}개 · 라벨 ${cmd.labels.length}개 표시`);
  }

  clearDimensions(): void {
    this.dimLabelDisposer?.();
    this.dimLabelDisposer = null;
    this.dimLabelAnchors = [];
    this.removeAll(this.dimensionIds);
  }

  /** 팝업 접기/펼치기에 맞춰 치수선·라벨을 숨기거나 다시 그린다. */
  setDimensionsVisible(on: boolean): void {
    if (on) {
      if (this.lastDimensionsCommand) this.showDimensions(this.lastDimensionsCommand);
    } else {
      this.clearDimensions();
    }
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
  showHousingModel(type: HousingModelType): EarthworkEstimate | null {
    this.lastHousingModelType = type;
    this.lastEarthwork = null;
    this.showHousingModelExact(type);
    // 절토·성토 추정과 3D 정지면은 showHousingModelExact 안에서 계산·표시한다.
    return this.lastEarthwork;
  }

  setEarthworkMode(mode: "original" | "graded"): EarthworkEstimate | null {
    if (!this.lastHousingModelType) {
      throw new Error("먼저 건물 모델을 선택해야 토공 전·후 모습을 전환할 수 있습니다.");
    }
    this.earthworkMode = mode;
    this.lastEarthwork = null;
    this.showHousingModelExact(this.lastHousingModelType);
    this.viewer.scene?.requestRender?.();
    this.note(mode === "original" ? "✓ 토공 전 원지형 모델 표시" : "✓ 평탄화 토공 모델 표시");
    return this.lastEarthwork;
  }

  showRequestedHousingModel(
    type: HousingModelType,
    floors: number | undefined,
    mode: "original" | "graded",
    hideEnvelope = true,
  ): EarthworkEstimate | null {
    const cmd = this.lastMassCommand;
    if (!cmd || cmd.flat_only) {
      throw new Error("먼저 선택한 필지의 건축 가능 규모를 진단해야 합니다.");
    }
    if (floors && Number.isFinite(floors)) {
      const targetFloors = Math.max(1, Math.min(60, Math.round(floors)));
      const floorHeight = Math.max(2.7, cmd.height_m / Math.max(1, cmd.floors));
      this.lastMassCommand = {
        ...cmd,
        floors: targetFloors,
        full_floors: targetFloors,
        top_floor_ratio: 1,
        top_footprint_geometry: cmd.footprint_geometry ?? null,
        height_m: floorHeight * targetFloors,
      };
    }
    if (hideEnvelope) {
      // 자연어로 특정 모델 하나를 요청했을 때 규제 한계 매스를 다른 건물처럼
      // 함께 남기지 않는다. 계산 데이터는 lastMassCommand에 그대로 보존한다.
      this.removeAll(this.massIds);
      this.focusEntity = null;
    }
    this.earthworkMode = mode;
    this.lastHousingModelType = type;
    this.lastEarthwork = null;
    this.showHousingModelExact(type);
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 요청 모델만 표시 (${type}, ${this.lastMassCommand?.floors ?? cmd.floors}층, ${mode})`);
    return this.lastEarthwork;
  }

  /** 창문·외벽·토공 표현을 모두 치우고 진단 당시의 단순 LOD1 매스만 복원한다. */
  private showLod1Only(): void {
    const cmd = this.lastMassCommand;
    if (!cmd) {
      throw new Error("먼저 선택한 필지의 건축 가능 규모를 진단해야 합니다.");
    }
    this.clearTerrainClipping();
    this.clearEarthworkPrimitives();
    this.removeAll(this.housingModelIds);
    this.removeAll(this.massIds);
    this.focusEntity = null;
    this.lastEarthwork = null;
    this.extrudeMass(cmd);
    this.viewer.scene?.requestRender?.();
    this.note("✓ 상세 모델 숨김 · LOD1 매스만 표시");
  }

  /** 입체 모델을 지우고 같은 계산값의 건축면적 평면 윤곽만 남긴다. */
  private showBuildingFootprintOnly(): void {
    const cmd = this.lastMassCommand;
    if (!cmd) {
      throw new Error("먼저 선택한 필지의 건축 가능 규모를 진단해야 합니다.");
    }
    this.clearTerrainClipping();
    this.clearEarthworkPrimitives();
    this.removeAll(this.housingModelIds);
    this.removeAll(this.massIds);
    this.focusEntity = null;
    this.lastEarthwork = null;
    this.extrudeMass({ ...cmd, flat_only: true, height_m: 0 });
    // 평면 표시용 복제 명령이 원래 LOD1 높이·층수 정보를 덮어쓰지 않게 한다.
    this.lastMassCommand = cmd;
    this.viewer.scene?.requestRender?.();
    this.note("✓ 건축면적 윤곽만 표시");
  }

  /** 건축 매스와 모델을 모두 숨기되 재표시할 계산 명령은 보존한다. */
  private hideBuildingShape(): void {
    this.clearTerrainClipping();
    this.clearEarthworkPrimitives();
    this.removeAll(this.housingModelIds);
    this.removeAll(this.massIds);
    this.focusEntity = null;
    this.lastEarthwork = null;
    this.viewer.scene?.requestRender?.();
    this.note("✓ 건물 윤곽 숨김");
  }

  /** 숨김·평면 전환 전에 보던 3D 형상을 복원한다. */
  private restoreBuildingShape(): void {
    if (!this.lastMassCommand) {
      throw new Error("건축 가능한 필지의 규모를 먼저 진단해야 3D 모델을 표시할 수 있습니다.");
    }
    if (this.lastHousingModelType) {
      this.clearTerrainClipping();
      this.clearEarthworkPrimitives();
      this.removeAll(this.massIds);
      this.removeAll(this.housingModelIds);
      this.focusEntity = null;
      this.lastEarthwork = null;
      this.showHousingModelExact(this.lastHousingModelType);
      this.note("✓ 직전 3D 상세 모델 복원");
      return;
    }
    this.showLod1Only();
  }

  private showHousingModelLegacy(type: HousingModelType): void {
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
      factory: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#B0B7BD", roof: "#37474F" },
      commercial: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#E7DCC3", roof: "#5D4037" },
      warehouse: { occupancy: 0.995, maxAspect: 20, floors: Math.max(1, cmd.floors), body: "#C4CDD3", roof: "#455A64" },
    }[type]!;
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
    this.clearTerrainClipping();
    this.clearEarthworkPrimitives();

    const footprint = cmd.footprint_geometry ? largestRing(cmd.footprint_geometry) : null;
    if (!footprint) throw new Error("건폐율 적용 건축면적 형상을 찾지 못했습니다.");
    // 규제로 오목하게 잘린 형상을 그대로 세우면 여러 동처럼 보인다. 실제 잘린
    // 형상(footprint)은 법정 윤곽(주황)·토공 계산에만 쓰고, 눈에 보이는 건물
    // 본체·창문·지붕은 같은 면적의 한 덩어리 매스(bodyFootprint)로 세운다.
    const bodyFootprint = coherentMass(footprint);
    const topRatio = Math.max(0, Math.min(1, cmd.top_floor_ratio ?? 1));
    const fullFloors = Math.max(0, Math.min(cmd.floors, cmd.full_floors ?? cmd.floors));
    const hasPartialTop = fullFloors < cmd.floors && topRatio > 0 && topRatio < 0.999;
    const topFootprint = hasPartialTop
      ? scalePolygon(bodyFootprint, topRatio)
      : bodyFootprint;
    const floorHeight = cmd.height_m / Math.max(1, cmd.floors);
    const lowerHeight = hasPartialTop ? fullFloors * floorHeight : cmd.height_m;
    const ws3d = window.ws3d;
    // 창문마다 지형 상대고도를 쓰면 경사면/LOD 차이로 같은 층에서도 높이가
    // 달라진다. 건물 전체가 공유할 하나의 기준 표고를 정해 절대고도로 그린다.
    const heightNone = (window as any).Cesium?.HeightReference?.NONE ?? 0;
    const footprintCenter = centroid(footprint);
    // 발판(계획고)은 '건축면적 전체 격자'의 중앙값으로 잡는다. 꼭짓점만 쓰면
    // 한쪽으로 치우쳐 전부 성토가 되어 절토가 안 생겼다(사용자 지적).
    const gridMedian = this.footprintTerrainMedian(footprint);
    const commonGround =
      gridMedian ?? this.terrainHeight(footprintCenter[0], footprintCenter[1]);
    const palette = {
      detached: { body: "#E8D8B8", roof: "#9E3F2C" },
      lowrise: { body: "#D9E0E3", roof: "#455A64" },
      slim: { body: "#CFDFEC", roof: "#29475F" },
      // 공장: 금속 외피·진회색 평지붕. 상가: 밝은 외피·유리 많은 저층 상업.
      factory: { body: "#AEB6BC", roof: "#37474F" },
      commercial: { body: "#EADFC6", roof: "#5D4037" },
      // 창고: 공장과 같은 산업형(금속 외피·롤러셔터), 조금 밝은 회색.
      warehouse: { body: "#C4CDD3", roof: "#455A64" },
    }[type]!;
    // 스타일: 창문 밀도·출입구·유리 처리를 유형에 맞게 바꾸는 기준.
    // 창고는 공장과 동일한 산업형으로 처리한다.
    const style: "residential" | "factory" | "commercial" =
      type === "factory" || type === "warehouse"
        ? "factory"
        : type === "commercial"
          ? "commercial"
          : "residential";
    const isWarehouse = type === "warehouse";
    const bodyColor = ws3d.common.Color.fromCssColorString(palette.body);
    const roofColor = ws3d.common.Color.fromCssColorString(palette.roof);
    const bandColor = ws3d.common.Color.fromCssColorString("#8B9295");
    // 창고 창문의 쇠창살(금속 바) 색.
    const barColor = ws3d.common.Color.fromCssColorString("#2E3438");
    const glassColor = ws3d.common.Color.fromCssColorString(
      style === "commercial" ? "#4FA3C7" : "#3F86AD",
    );
    const frameColor = ws3d.common.Color.fromCssColorString("#EEF5F7");
    // 공장은 회색 롤러셔터, 상가는 유리문 느낌.
    const doorColor = ws3d.common.Color.fromCssColorString(
      style === "factory" ? "#6E767C" : style === "commercial" ? "#3F86AD" : "#6C402D",
    );
    let serial = 0;
    const addEntity = (definition: any) => {
      const id = `housing-exact-${Date.now()}-${serial++}`;
      this.viewer.entities.add({ id, ...definition });
      this.housingModelIds.push(id);
      return id;
    };
    const flat = (ring: number[][]) => ring.flatMap(([lon, lat]) => [lon, lat]);

    // --- 경사지 정지(整地): 절토·성토 3D 표현 + 토공량 추정 ---
    // 건물 발판(계획고 = commonGround) 기준으로, 필지 둘레의 지반과 계획고 사이
    // 면을 그린다. 지반이 계획고보다 낮으면 '성토(쌓기)', 높으면 '절토(깎기)'다.
    this.lastEarthwork = this.estimateEarthwork(footprint, commonGround);
    // 건물보다 2m 바깥에서 지형을 자르고, 토공벽도 같은 경계에 세운다.
    // 벽을 경계 안쪽에 두면 그 사이에 남은 얇은 원지형이 뒷면을 다시 가린다.
    const clippingFootprint = expandRingMeters(footprint, 2);
    const gradingFootprint = clippingFootprint;
    if (this.earthworkMode === "graded") {
      // 클리핑 전에 반드시 원지형 높이를 확정한다. 클리핑 후 getHeight를 부르면
      // VWorld LOD에 따라 0/undefined가 섞여 지구 내부까지 내려가는 검은 벽이 된다.
      const closed = densifyRing(gradingFootprint);
      const terr = closed.map(([lon, lat]) => this.terrainHeight(lon, lat));
      this.applyTerrainClipping(clippingFootprint, commonGround);
      const padPos = ws3d.common.Cartesian3.fromDegreesArray(flat(clippingFootprint));
      const fillColor = ws3d.common.Color.fromCssColorString("#D2A45C"); // 성토(쌓기): 흙색
      const cutColor = ws3d.common.Color.fromCssColorString("#8D5A3B"); // 절토(깎기): 짙은 흙
      const earthworkBottom = Math.min(commonGround, ...terr) - 2;
      // VWorld 지형과 토공면이 같은 깊이에 놓이면 카메라 방향에 따라 서로를
      // 번갈아 가린다. 실제 계획고 계산은 유지하고 표시 메시만 12cm 들어 올린다.
      const visualPlatform = commonGround + 0.12;
      const C = (window as any).Cesium;
      const supportsPrimitiveEarthwork =
        C?.Primitive && C?.GeometryInstance && C?.PolygonGeometry &&
        C?.PolygonHierarchy && C?.WallGeometry && C?.PerInstanceColorAppearance &&
        C?.ColorGeometryInstanceAttribute;
      if (supportsPrimitiveEarthwork) {
        const vertexFormat = C.PerInstanceColorAppearance.VERTEX_FORMAT;
        const primitiveRing =
          clippingFootprint.length > 1 &&
          clippingFootprint[0][0] === clippingFootprint[clippingFootprint.length - 1][0] &&
          clippingFootprint[0][1] === clippingFootprint[clippingFootprint.length - 1][1]
            ? clippingFootprint.slice(0, -1)
            : clippingFootprint;
        const instance = (geometry: any, colorValue: any) =>
          new C.GeometryInstance({
            geometry,
            attributes: {
              color: C.ColorGeometryInstanceAttribute.fromColor(colorValue),
            },
          });
        const instances: any[] = [
          instance(
            new C.PolygonGeometry({
              polygonHierarchy: new C.PolygonHierarchy(
                primitiveRing.map(([lon, lat]) => C.Cartesian3.fromDegrees(lon, lat)),
              ),
              height: visualPlatform,
              extrudedHeight: earthworkBottom,
              closeTop: true,
              closeBottom: true,
              vertexFormat,
            }),
            C.Color.fromCssColorString("#D2A45C"),
          ),
        ];
        for (let i = 0; i < closed.length - 1; i += 1) {
          const next = i + 1;
          const segmentGround = (terr[i] + terr[next]) / 2;
          if (Math.abs(segmentGround - commonGround) <= 0.03) continue;
          const isCut = segmentGround > commonGround;
          const wallColor = C.Color.fromCssColorString(isCut ? "#8D5A3B" : "#D2A45C");
          const groundA = terr[i] + 0.08;
          const groundB = terr[next] + 0.08;
          const minHeights = [
            Math.min(groundA, visualPlatform),
            Math.min(groundB, visualPlatform),
          ];
          const maxHeights = [
            Math.max(groundA, visualPlatform),
            Math.max(groundB, visualPlatform),
          ];
          const addWallFace = (
            a: number,
            b: number,
            minimums: number[],
            maximums: number[],
          ) => {
            instances.push(
              instance(
                new C.WallGeometry({
                  positions: C.Cartesian3.fromDegreesArray([
                    closed[a][0], closed[a][1], closed[b][0], closed[b][1],
                  ]),
                  minimumHeights: minimums,
                  maximumHeights: maximums,
                  vertexFormat,
                }),
                wallColor,
              ),
            );
          };
          // VWorld 번들이 Primitive의 cull 설정을 덮어쓰는 경우를 대비해 동일 벽을
          // 역방향으로도 생성한다. 앞·뒤 어느 쪽에서 보더라도 한 면은 정면이 된다.
          addWallFace(i, next, minHeights, maxHeights);
          addWallFace(
            next,
            i,
            [minHeights[1], minHeights[0]],
            [maxHeights[1], maxHeights[0]],
          );
        }
        const primitive = this.viewer.scene.primitives.add(
          new C.Primitive({
            geometryInstances: instances,
            asynchronous: false,
            appearance: new C.PerInstanceColorAppearance({
              closed: true,
              flat: true,
              translucent: false,
              renderState: {
                cull: { enabled: false },
                depthTest: { enabled: true },
                depthMask: true,
                // 토공면을 지형보다 화면 쪽으로 아주 조금 당겨 z-fighting과
                // VWorld 타일 스커트의 앞·뒤 교대 가림을 방지한다.
                polygonOffset: { enabled: true, factor: -2, units: -8 },
              },
            }),
          }),
        );
        this.earthworkPrimitives.push(primitive);
        this.note("✓ 양면 평탄화 토체 메시 적용");
      } else {
        // 매우 오래된 VWorld 번들에서는 기존 Entity 입체를 안전 폴백으로 쓴다.
        addEntity({
          polygon: {
            hierarchy: padPos,
            height: visualPlatform,
            heightReference: heightNone,
            extrudedHeight: earthworkBottom,
            extrudedHeightReference: heightNone,
            material: fillColor.withAlpha(1),
            outline: false,
            closeTop: true,
            closeBottom: true,
          },
        });
        this.note("⚠ Primitive 미지원: Entity 평탄화 토체로 표시");
      }
    }

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

    // 실제 건물 외피는 불투명하게 유지한다. 법정 한계 매스는 별도 주황 윤곽으로
    // 이미 구분되므로 건물을 투명하게 만들 필요가 없다.
    if (fullFloors > 0) addLayer(bodyFootprint, 0.5, lowerHeight, bodyColor.withAlpha(1));
    // 소수층은 백엔드에서 다시 내부 이격해 목표 면적비를 정확히 맞춘 형상이다.
    if (hasPartialTop) addLayer(topFootprint, lowerHeight, cmd.height_m, bodyColor.withAlpha(1));

    // 각 층 경계에 얇은 슬래브 띠를 넣어 외관에서도 층수를 읽을 수 있게 한다.
    for (let floor = 1; floor <= fullFloors; floor += 1) {
      const z = floor * floorHeight;
      addLayer(bodyFootprint, z - 0.07, z + 0.07, bandColor.withAlpha(0.9));
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
    const entrance = edgeInfo(bodyFootprint)[0];
    const sameEdge = (left: any, right: any) =>
      !!left && !!right &&
      left.a[0] === right.a[0] && left.a[1] === right.a[1] &&
      left.b[0] === right.b[0] && left.b[1] === right.b[1];

    const addWindows = (ring: number[][], floor: number) => {
      // box position은 창문의 중심 높이다. 이전 55%(최대 1.7m)는 창틀 상단이
      // 다음 층 슬래브에 걸쳐 보였으므로, 층 바닥에서 약 42% 지점으로 내린다.
      const z = 0.5 + floor * floorHeight + Math.min(1.35, floorHeight * 0.42);
      // 공장은 창이 드물고(산업), 상가 1층은 큰 통유리 쇼윈도로 표현한다.
      const spacing = style === "factory" ? 6 : 3;
      const isShopfront = style === "commercial" && floor === 0;
      for (const edge of edgeInfo(ring)) {
        const count = isShopfront
          ? Math.max(1, Math.min(24, Math.floor(edge.length / 2)))
          : Math.max(1, Math.min(18, Math.floor(edge.length / spacing)));
        const windowWidth = isShopfront
          ? Math.min(2.6, Math.max(1.4, edge.length / (count * 1.4)))
          : Math.min(1.35, Math.max(0.75, edge.length / (count * 2)));
        const glassH = isShopfront ? Math.min(2.6, floorHeight * 0.72) : 1.12;
        for (let i = 0; i < count; i += 1) {
          const t = (i + 1) / (count + 1);
          // 1층 정면 중앙은 출입문과 좌우 여유 폭을 위해 비운다.
          if (floor === 0 && sameEdge(edge, entrance) && Math.abs(t - 0.5) * edge.length < 1.65) {
            continue;
          }
          const lon = edge.a[0] + (edge.b[0] - edge.a[0]) * t;
          const lat = edge.a[1] + (edge.b[1] - edge.a[1]) * t;
          addFacadeBox(lon, lat, z, edge.heading, [windowWidth + 0.12, 0.13, glassH + 0.18], frameColor);
          addFacadeBox(lon, lat, z, edge.heading, [windowWidth, 0.18, glassH], glassColor);
          // 창고는 창문에 쇠창살(가로 금속 바 3줄)을 덧대 산업용 느낌을 준다.
          if (isWarehouse) {
            const bars = 3;
            for (let b = 1; b <= bars; b += 1) {
              const bz = z - glassH / 2 + (glassH * b) / (bars + 1);
              addFacadeBox(
                lon, lat, bz, edge.heading,
                [windowWidth + 0.06, 0.22, 0.05], barColor,
              );
            }
          }
        }
      }
    };
    for (let floor = 0; floor < fullFloors; floor += 1) addWindows(bodyFootprint, floor);
    if (hasPartialTop) addWindows(topFootprint, fullFloors);

    // 가장 긴 1층 벽 중앙에 출입구를 둔다. 공장은 넓은 롤러셔터, 상가는 넓은
    // 유리 출입구, 주택은 일반 현관.
    if (entrance) {
      const [doorW, doorH] =
        style === "factory" ? [3.4, 3.4] : style === "commercial" ? [2.6, 2.6] : [1.35, 2.2];
      addFacadeBox(
        (entrance.a[0] + entrance.b[0]) / 2,
        (entrance.a[1] + entrance.b[1]) / 2,
        doorH / 2 + 0.2,
        entrance.heading,
        [doorW, 0.2, doorH],
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
    cb: (lon: number, lat: number, jibun: string, pnu: string) => void,
    onError?: (message: string) => void,
  ): () => void {
    const canvas = this.viewer.scene?.canvas as HTMLCanvasElement | undefined;
    if (!canvas) return () => {};
    const ws3d = window.ws3d;
    const handler = new ws3d.common.ScreenSpaceEventHandler(canvas);
    let selectionGeneration = 0;
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
        const requestGeneration = ++selectionGeneration;
        void this.selectParcelAt(lon, lat)
          .then((selected) => {
            // 연속 클릭의 응답 순서가 뒤집혀도 과거 요청이 최신 PNU를
            // 덮어쓰지 못하게 가장 최근 요청만 반영한다.
            if (requestGeneration !== selectionGeneration) return;
            // 3D 깊이 버퍼의 원시 좌표를 그대로 질문에 쓰지 않는다. 서버가 실제
            // 지적 필지를 반환한 뒤 그 경계 중심을 현재 선택으로 확정해야 화면의
            // 필지와 다음 진단 좌표가 항상 일치한다.
            cb(selected.lon, selected.lat, selected.jibun, selected.pnu);
          })
          .catch((error: unknown) => {
            if (requestGeneration !== selectionGeneration) return;
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
  private async selectParcelAt(
    lon: number,
    lat: number,
  ): Promise<{ lon: number; lat: number; jibun: string; pnu: string }> {
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
      return {
        lon: focusLon,
        lat: focusLat,
        jibun: parcel.jibun ?? "선택한 필지",
        pnu: parcel.pnu ?? "",
      };
    }
    throw new Error("필지 경계의 중심 좌표를 계산할 수 없습니다.");
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
    // 기존 선을 먼저 지우면 네트워크 조회 시간 동안 지적도가 사라졌다가
    // 다시 나타나 깜박인다. 새 경계를 완성한 뒤 한 번에 교체한다.
    const generation = ++this.cadastreLoadGeneration;
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
      const parcelFeatures: Array<{
        geometry: GeoJSONPolygon;
        address?: string;
        pnu?: string;
      }> = Array.isArray(data.features)
        ? data.features
        : Array.isArray(data.geometries)
          ? data.geometries.map((geometry: GeoJSONPolygon) => ({ geometry }))
          : (data.response?.result?.featureCollection?.features ?? [])
              .map((feature: any) => ({
                geometry: feature?.geometry,
                address: feature?.properties?.addr ?? "",
                pnu: feature?.properties?.pnu ?? "",
              }))
              .filter((feature: any) => Boolean(feature.geometry));
      const ws3d = window.ws3d;
      // 주변 연속지적도는 선택 필지보다 뒤로 물러나 보이는 회청색 보조선이다.
      const lineColor = ws3d.common.Color.fromCssColorString("#CFD8DC").withAlpha(1);
      const nextIds: string[] = [];
      // Cesium Entity 라벨을 필지 최대 1,000개에 모두 만들면 지적도 로딩과
      // 카메라 이동이 크게 느려진다. 화면 중심에 가까운 지번만 제한해 표시한다.
      const orderedFeatures = [...parcelFeatures].sort((a, b) => {
        const ar = largestRing(a.geometry);
        const br = largestRing(b.geometry);
        const ac = ar ? centroid(ar) : [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
        const bc = br ? centroid(br) : [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
        const ad = (ac[0] - lon) ** 2 + (ac[1] - lat) ** 2;
        const bd = (bc[0] - lon) ** 2 + (bc[1] - lat) ** 2;
        return ad - bd;
      });
      let labelCount = 0;
      const maxLabels = 40;
      if (generation !== this.cadastreLoadGeneration) return this.cadastreIds.length;
      for (const feature of orderedFeatures) {
        const geometry = feature.geometry;
        for (const ring of outerRings(geometry)) {
          const id = `cadastre-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          this.viewer.entities.add({
            id,
            polyline: {
              positions: ws3d.common.Cartesian3.fromDegreesArray(
                ring.flatMap(([x, y]) => [x, y]),
              ),
              clampToGround: true,
              width: 1.4,
              material: lineColor,
              depthFailMaterial: lineColor,
            },
          });
          nextIds.push(id);
        }
        const ring = largestRing(geometry);
        const address = String(feature.address ?? "").trim();
        const lotMatch = address.match(/(?:산\s*)?\d+(?:-\d+)?\s*$/);
        if (ring && lotMatch && labelCount < maxLabels) {
          const [labelLon, labelLat] = centroid(ring);
          // RELATIVE_TO_GROUND는 지형 타일이 늦게 도착할 때 라벨이 임시 높이에서
          // 실제 높이로 튀어 오른다. 현재 지형 높이가 준비된 필지만 절대고도로
          // 한 번에 표시하고, 아직 준비되지 않은 라벨은 다음 자동 갱신 때 그린다.
          const cartographic = ws3d.common.Cartographic.fromDegrees(labelLon, labelLat);
          const terrainHeight = this.viewer.scene?.globe?.getHeight?.(cartographic);
          if (!Number.isFinite(terrainHeight)) continue;
          const labelId = `cadastre-label-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          this.viewer.entities.add({
            id: labelId,
            position: ws3d.common.Cartesian3.fromDegrees(
              labelLon,
              labelLat,
              Number(terrainHeight) + 1.2,
            ),
            label: {
              text: lotMatch[0].replace(/\s+/g, " ").trim(),
              font: "600 11px 'Malgun Gothic', sans-serif",
              fillColor: ws3d.common.Color.WHITE,
              showBackground: false,
              pixelOffset: new ws3d.common.Cartesian2(0, 0),
              heightReference: (window as any).Cesium?.HeightReference?.NONE ?? 0,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
          });
          nextIds.push(labelId);
          labelCount += 1;
        }
      }
      if (nextIds.length === 0) {
        throw new Error("현재 화면 범위에서 조회된 필지 경계가 없습니다.");
      }
      // 더 늦게 시작한 조회가 있으면 이 결과는 화면에 반영하지 않는다.
      if (generation !== this.cadastreLoadGeneration) {
        this.removeAll(nextIds);
        return this.cadastreIds.length;
      }
      const previousIds = this.cadastreIds;
      this.cadastreIds = nextIds;
      this.removeAll(previousIds);
    } catch (err) {
      console.warn("[MapBridge] 연속지적도 조회 실패:", err);
      throw err;
    }
    return this.cadastreIds.length;
  }

  private clearCadastre(): void {
    this.cadastreLoadGeneration += 1;
    this.removeAll(this.cadastreIds);
  }

  /**
   * 주제도 오버레이 — 진단 필지 주변 연속지적도(지적선)를 3D 위에 그린다.
   * 치수선이 가리키는 필지 경계·도로 필지를 눈으로 확인할 수 있게 한다.
   * loadCadastre(2D 모드용)를 3D 표시에 재사용한다. on=false면 지운다.
   */
  async setCadastreOverlay(on: boolean, lon?: number, lat?: number): Promise<number> {
    this.cadastreEnabled = on;
    if (!on) {
      if (this.cadastreMoveEndHandler) {
        this.viewer.scene?.camera?.moveEnd?.removeEventListener?.(
          this.cadastreMoveEndHandler,
        );
        this.cadastreMoveEndHandler = null;
      }
      if (this.cadastreReloadTimer != null) {
        window.clearTimeout(this.cadastreReloadTimer);
        this.cadastreReloadTimer = null;
      }
      this.clearCadastre();
      return 0;
    }

    // ON 상태에서는 지도를 다른 지역으로 이동한 뒤에도 새 화면 중심의
    // 지적도를 자동으로 다시 조회한다.
    if (!this.cadastreMoveEndHandler) {
      this.cadastreMoveEndHandler = () => {
        if (!this.cadastreEnabled) return;
        if (this.cadastreReloadTimer != null) window.clearTimeout(this.cadastreReloadTimer);
        this.cadastreReloadTimer = window.setTimeout(() => {
          this.cadastreReloadTimer = null;
          const focus = this.currentGroundFocus();
          if (focus) void this.loadCadastre(focus.lon, focus.lat).catch((err) => {
            this.note(`✗ 지적도 자동 갱신 실패: ${(err as Error)?.message ?? err}`);
          });
        }, 350);
      };
      this.viewer.scene?.camera?.moveEnd?.addEventListener?.(
        this.cadastreMoveEndHandler,
      );
    }

    const current = this.currentGroundFocus();
    const focusLon = lon ?? current?.lon ?? this.lastFocus?.lon;
    const focusLat = lat ?? current?.lat ?? this.lastFocus?.lat;
    if (focusLon == null || focusLat == null) return 0;
    return this.loadCadastre(focusLon, focusLat);
  }

  private currentGroundFocus(): { lon: number; lat: number } | null {
    try {
      const ws3d = window.ws3d;
      const scene = this.viewer.scene;
      const canvas = scene.canvas;
      const center = new ws3d.common.Cartesian2(
        canvas.clientWidth / 2,
        canvas.clientHeight / 2,
      );
      const ray = scene.camera.getPickRay(center);
      const world = ray && scene.globe.pick(ray, scene);
      if (!world) return null;
      const carto = ws3d.common.Cartographic.fromCartesian(world);
      return {
        lon: ws3d.common.CesiumMath.toDegrees(carto.longitude),
        lat: ws3d.common.CesiumMath.toDegrees(carto.latitude),
      };
    } catch {
      return null;
    }
  }

  /**
   * 주제도 오버레이 — 진단 필지 주변 용도지역을 색 폴리곤으로 깐다.
   * (지적편집도처럼) 지적선 아래에 반투명으로 깔아 어느 용도지역인지 보이게 한다.
   */
  async setZoningOverlay(on: boolean, lon?: number, lat?: number): Promise<number> {
    this.clearZoningOverlay();
    if (!on) return 0;
    const focusLon = lon ?? this.lastFocus?.lon;
    const focusLat = lat ?? this.lastFocus?.lat;
    if (focusLon == null || focusLat == null) return 0;
    const dx = 0.004;
    const dy = 0.003;
    let polygons: Array<{ zone: string; color: string; geometry: GeoJSONPolygon }> = [];
    try {
      const res = await fetch(
        `/api/zoning-area?west=${focusLon - dx}&south=${focusLat - dy}` +
          `&east=${focusLon + dx}&north=${focusLat + dy}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      polygons = (await res.json()).polygons ?? [];
    } catch (err) {
      this.note(`✗ 용도지역 주제도 조회 실패: ${(err as Error)?.message ?? err}`);
      return 0;
    }
    const ws3d = window.ws3d;
    const relativeToGround =
      (window as any).Cesium?.HeightReference?.RELATIVE_TO_GROUND ?? 2;
    // 용도지역 색 폴리곤을 그리면서, 라벨용으로 지역별 링(경계)을 모은다.
    const byZone: Record<string, { color: string; rings: number[][][] }> = {};
    for (const p of polygons) {
      if (!byZone[p.zone]) byZone[p.zone] = { color: p.color, rings: [] };
      for (const ring of outerRings(p.geometry)) {
        if (ring.length < 3) continue;
        byZone[p.zone].rings.push(ring);
        const flat = ring.flatMap(([lon2, lat2]) => [lon2, lat2]);
        const id = `map-zoning-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const zColor = ws3d.common.Color.fromCssColorString(p.color);
        this.viewer.entities.add({
          id,
          polygon: {
            hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
            material: zColor.withAlpha(0.6),
            // 지형 표면에 '입혀서'(드레이프) 그린다. TERRAIN=0.
            classificationType: (window as any).Cesium?.ClassificationType?.TERRAIN ?? 0,
          },
        });
        this.zoningOverlayIds.push(id);
      }
    }

    // 라벨은 '그 용도지역 색 구역 안'에, 선택 필지에서 가장 가까운 지점에 둔다.
    // 경계에 걸치면 어느 색이 어느 지역인지 모호하므로, 지역 안쪽으로 충분히
    // 밀어 넣어 색 위에 확실히 얹는다.
    const mPerDegLat = 111320;
    const mPerDegLon = 111320 * Math.cos((focusLat * Math.PI) / 180);
    const inRing = (x: number, y: number, ring: number[][]): boolean => {
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
          inside = !inside;
        }
      }
      return inside;
    };
    // 각 용도지역 라벨을 '그 지역 색 안'이면서 필지에서 가장 가까운 점에 둔다.
    // 필지 주변을 나선형(가까운 반경부터)으로 돌며, 건물을 덮지 않게 최소 40m
    // 밖에서 그 지역 안에 드는 첫 점을 쓴다. 고정 방향으로 밀면 경계를 넘어
    // 옆 지역(다른 색) 위로 넘어가므로(계획관리가 농림 위로 간 원인) 그렇게 안 한다.
    const labelPoint = (rings: number[][][]): [number, number] | null => {
      for (let radius = 40; radius <= 160; radius += 15) {
        for (let a = 0; a < 360; a += 20) {
          const rad = (a * Math.PI) / 180;
          const lon = focusLon + (Math.cos(rad) * radius) / mPerDegLon;
          const lat = focusLat + (Math.sin(rad) * radius) / mPerDegLat;
          if (rings.some((r) => inRing(lon, lat, r))) return [lon, lat];
        }
      }
      return null;
    };
    // 지역 중심점(그 지역 색 안이 보장된 대표점) — 확대 시 라벨이 여기로 간다.
    const zoneCenter = (rings: number[][][], near: [number, number]): [number, number] => {
      const largest = rings.reduce((a, b) => (b.length > a.length ? b : a), rings[0]);
      let cx = 0, cy = 0;
      for (const [lo, la] of largest) { cx += lo; cy += la; }
      cx /= largest.length; cy /= largest.length;
      return rings.some((r) => inRing(cx, cy, r)) ? [cx, cy] : near;
    };
    for (const [zone, info] of Object.entries(byZone)) {
      const pt = labelPoint(info.rings);
      if (!pt) continue; // 근처에서 그 지역 색을 못 찾으면 라벨 생략
      const [labLon, labLat] = pt;
      const [cenLon, cenLat] = zoneCenter(info.rings, pt);
      const id = `map-zoning-label-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const fill = ws3d.common.Color.fromCssColorString(info.color);
      this.viewer.entities.add({
        id,
        position: ws3d.common.Cartesian3.fromDegrees(labLon, labLat, 1),
        label: {
          text: zone,
          font: "bold 23px 'Malgun Gothic', sans-serif",
          fillColor: fill,
          outlineColor: ws3d.common.Color.BLACK,
          outlineWidth: 6,
          style: 2, // FILL_AND_OUTLINE
          showBackground: false,
          heightReference: relativeToGround,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this.zoningOverlayIds.push(id);
      this.zoningLabelAnchors.push({
        id, nearLon: labLon, nearLat: labLat, centerLon: cenLon, centerLat: cenLat,
      });
    }

    // 구글식 viewport-clamp: 라벨은 '지역 중심(center)'에 두되, 중심이 화면 밖으로
    // 나가면 화면 가장자리(중심 방향)에 붙여 항상 보이게 한다. 특정 지점으로
    // 미끄러지지 않아 자연스럽다. 중심이 카메라 뒤면 near(필지 근처)로 폴백.
    const updateLabelPositions = () => {
      const C = (window as any).Cesium;
      const scene: any = this.viewer.scene;
      if (!C?.SceneTransforms || !scene?.camera) return;
      const canvas = scene.canvas;
      const W = canvas.clientWidth || canvas.width || 0;
      const H = canvas.clientHeight || canvas.height || 0;
      const margin = 72;
      const camera = scene.camera;
      const toWin = C.SceneTransforms.wgs84ToWindowCoordinates?.bind(C.SceneTransforms)
        ?? C.SceneTransforms.worldToWindowCoordinates?.bind(C.SceneTransforms);
      for (const a of this.zoningLabelAnchors) {
        const ent = this.viewer.entities.getById(a.id);
        if (!ent) continue;
        const cGround = this.terrainHeight(a.centerLon, a.centerLat);
        const world = C.Cartesian3.fromDegrees(a.centerLon, a.centerLat, cGround + 1);
        // 중심이 카메라 앞인지(뒤면 화면좌표가 뒤집혀 엉뚱하게 나온다).
        const toPt = C.Cartesian3.subtract(world, camera.positionWC, new C.Cartesian3());
        const front = C.Cartesian3.dot(toPt, camera.directionWC) > 0;
        const win = front && toWin ? toWin(scene, world) : undefined;
        if (win && win.x >= margin && win.x <= W - margin && win.y >= margin && win.y <= H - margin) {
          ent.position = world; // 중심이 화면 안 → 그대로
          continue;
        }
        if (win) {
          // 화면 밖 → 가장자리로 클램프한 뒤, 그 화면점의 지형 위 지점으로 되돌린다.
          const sx = Math.min(W - margin, Math.max(margin, win.x));
          const sy = Math.min(H - margin, Math.max(margin, win.y));
          const ray = camera.getPickRay(new C.Cartesian2(sx, sy));
          const picked = ray ? scene.globe.pick(ray, scene) : undefined;
          ent.position = picked ?? world;
        } else {
          // 중심이 카메라 뒤 등 투영 불가 → 필지 근처로.
          ent.position = C.Cartesian3.fromDegrees(
            a.nearLon, a.nearLat, this.terrainHeight(a.nearLon, a.nearLat) + 1,
          );
        }
      }
    };
    updateLabelPositions();
    this.zoningLabelDisposer = this.onCameraChange(updateLabelPositions);
    this.viewer.scene?.requestRender?.();
    this.note(`✓ 용도지역 주제도 ${polygons.length}개 · 지역명 ${Object.keys(byZone).length}개 표시`);
    return polygons.length;
  }

  clearZoningOverlay(): void {
    this.zoningLabelDisposer?.();
    this.zoningLabelDisposer = null;
    this.zoningLabelAnchors = [];
    this.removeAll(this.zoningOverlayIds);
  }

  /** 서버에서 계산한 COP30 DEM 셀을 그대로 색칠한다. */
  setSlopeGrid(
    on: boolean,
  ): {
    cells: number;
    maxSlope: number;
    avgSlope: number;
    minElev: number;
    maxElev: number;
    source: string;
    resolutionM: number;
  } {
    this.clearSlopeGrid();
    const data = this.slopeData;
    const empty = {
      cells: 0, maxSlope: 0, avgSlope: 0, minElev: 0, maxElev: 0,
      source: data?.source ?? "COP30 DEM", resolutionM: data?.resolution_m ?? 30,
    };
    if (!on) return empty;
    if (!data || !data.cells.length) {
      this.note("✗ 경사도: 수집된 DEM 분석 데이터가 없습니다. 필지를 다시 진단하세요.");
      return empty;
    }
    const ws3d = window.ws3d;
    let count = 0;
    for (const cell of data.cells) {
      for (const ring of outerRings(cell.geometry)) {
        if (ring.length < 3) continue;
        const flat = ring.flatMap(([x, y]) => [x, y]);
        count += 1;
        const id = `map-slope-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        this.viewer.entities.add({
          id,
          polygon: {
            hierarchy: ws3d.common.Cartesian3.fromDegreesArray(flat),
            material: ws3d.common.Color.fromCssColorString(
              slopeColor(cell.slope_deg),
            ).withAlpha(0.55),
            classificationType: (window as any).Cesium?.ClassificationType?.TERRAIN ?? 0,
          },
        });
        this.slopeGridIds.push(id);
      }
    }
    this.viewer.scene?.requestRender?.();
    this.note(
      `✓ ${data.source} ${data.resolution_m}m 격자 ${count}칸 · ` +
      `표고 ${data.min_elevation_m}–${data.max_elevation_m}m · ` +
      `경사 최대 ${data.max_slope_deg}° · 평균 ${data.mean_slope_deg}°`,
    );
    return {
      cells: count,
      maxSlope: data.max_slope_deg,
      avgSlope: data.mean_slope_deg,
      minElev: data.min_elevation_m,
      maxElev: data.max_elevation_m,
      source: data.source,
      resolutionM: data.resolution_m,
    };
  }

  clearSlopeGrid(): void {
    this.removeAll(this.slopeGridIds);
  }

  /** 현재 카메라 방위각(도, 0=정북). 나침반 회전용. */
  cameraHeadingDeg(): number {
    try {
      const h = this.viewer.scene.camera.heading;
      const deg = (h * 180) / Math.PI;
      return ((deg % 360) + 360) % 360;
    } catch {
      return 0;
    }
  }

  /** 최근 진단 필지 중심 좌표(태양 방위 계산용). */
  focusLonLat(): { lon: number; lat: number } | null {
    if (this.lastFocus) return { lon: this.lastFocus.lon, lat: this.lastFocus.lat };
    // 진단한 필지가 없어도 해/달이 항상 뜨도록, 지도 카메라가 보는 위치를 쓴다.
    // (태양 방위는 좌표가 몇 km 달라도 거의 같아 카메라 위치로 충분하다.)
    try {
      const cam: any =
        (this.viewer as any)?.scene?.camera ?? (this.viewer as any)?.camera;
      const carto = cam?.positionCartographic;
      const toDeg = window.ws3d?.common?.CesiumMath?.toDegrees;
      if (carto && toDeg) {
        return { lon: toDeg(carto.longitude), lat: toDeg(carto.latitude) };
      }
    } catch {
      /* 카메라 접근 실패 시 표시 생략 */
    }
    return null;
  }

  private clearMass(): void {
    this.cameraGeneration += 1;
    this.clearTerrainClipping();
    this.clearEarthworkPrimitives();
    this.removeAll(this.massIds);
    this.removeAll(this.housingModelIds);
    this.focusEntity = null;
    this.lastMassCommand = null;
    this.lastHousingModelType = null;
    this.lastEarthwork = null;
  }

  private clearParcel(): void {
    this.removeAll(this.parcelIds);
  }
}
