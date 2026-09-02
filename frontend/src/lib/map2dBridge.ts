/**
 * WebGL 없이 도는 2D 지도.
 *
 * VWorld 3D 엔진(ws3d/Cesium)은 WebGL 이 없으면 뷰어 자체를 못 만든다. 회사
 * 정책이나 VDI 로 WebGL 이 막힌 자리에서는 지도를 아예 못 쓰게 되므로,
 * OpenLayers(Canvas 2D)로 같은 화면을 대신 그린다.
 *
 * 데이터는 3D 와 완전히 같은 것을 쓴다 — 배경 타일은 VWorld CDN, 필지·용도지역·
 * 경사도는 우리 백엔드(`/api/parcels`, `/api/zoning-area`, `/api/parcel-at`)가
 * 주는 GeoJSON 이다. 그래서 판정 결과와 지도가 어긋날 일이 없다.
 *
 * 배경 타일에 `cdn.vworld.kr` 을 쓰는 이유: 키가 필요 없어 Referer 도메인
 * 검증을 타지 않는다. `api.vworld.kr/req/wmts` 는 키와 등록 도메인이 맞아야
 * 하는데, 그건 서버 이전 때마다 어긋나는 알려진 함정이다.
 * (타일 스킴은 EPSG:3857 표준 구글 스킴 — `{z}/{x}/{y}`.)
 */
import Feature from "ol/Feature";
import Map from "ol/Map";
import View from "ol/View";
import { unByKey } from "ol/Observable";
import { LineString, Point, Polygon } from "ol/geom";
import { Draw } from "ol/interaction";
import { Tile as TileLayer, Vector as VectorLayer } from "ol/layer";
import { fromLonLat, toLonLat } from "ol/proj";
import { XYZ, Vector as VectorSource } from "ol/source";
import { getArea, getLength } from "ol/sphere";
import { Circle, Fill, Stroke, Style, Text } from "ol/style";
import "ol/ol.css";

import type {
  EarthworkEstimate,
  GeoJSONPolygon,
  HousingModelType,
  MapCommand,
} from "./mapBridge";
import type { MapCapabilities, MapSurface, SlopeGridResult } from "./mapSurface";

/** VWorld 2D 타일. 키 불필요, EPSG:3857 표준 스킴. */
const TILE_URL = "https://cdn.vworld.kr/2d/{layer}/service/{z}/{x}/{y}.{ext}";

export type BaseLayerId = "midnight" | "Base" | "Hybrid" | "Satellite";

const TILE_EXT: Record<BaseLayerId, string> = {
  midnight: "png",
  Base: "png",
  Hybrid: "png",
  Satellite: "jpeg",
};

/**
 * 레이어별 마지막 줌 단계. 이걸 넘겨서 요청하면 404 가 오고 배경이 **빈 채로**
 * 남는다 — 필지를 확대해 보는 화면(줌 19~20)이 정확히 그 구간이라 놓치기 쉽다.
 * 실측값이다(아산시 음봉면 신수리 기준으로 층별 확인).
 *   midnight 18 / Base·Hybrid·Satellite 19
 * OL 은 source 의 maxZoom 을 알면 그 이상에서 마지막 타일을 늘려 쓴다.
 */
const TILE_MAX_ZOOM: Record<BaseLayerId, number> = {
  midnight: 18,
  Base: 19,
  Hybrid: 19,
  Satellite: 19,
};

const EMPTY_SLOPE: SlopeGridResult = {
  cells: 0,
  maxSlope: 0,
  avgSlope: 0,
  minElev: 0,
  maxElev: 0,
  source: "",
  resolutionM: 0,
};

/** Polygon / MultiPolygon 을 외곽 링 목록으로 통일한다. (mapBridge 와 같은 규칙) */
function outerRings(geometry: GeoJSONPolygon | undefined | null): number[][][] {
  if (!geometry) return [];
  const coords: any = (geometry as any).coordinates ?? [];
  if (geometry.type === "Polygon") return coords.length ? [coords[0]] : [];
  if (geometry.type === "MultiPolygon") {
    return coords.filter((poly: any) => poly?.length).map((poly: any) => poly[0]);
  }
  return [];
}

function toMercatorRing(ring: number[][]): number[][] {
  return ring.map(([lon, lat]) => fromLonLat([lon, lat]));
}

/** CSS 색 문자열에 알파를 입힌다. Cesium 과 달리 OL 은 문자열을 그대로 받는다. */
function withAlpha(color: string | undefined, alpha: number): string {
  const css = color?.trim() || "#7fb2c8";
  const hex = /^#([0-9a-f]{6})$/i.exec(css);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  const rgb = /^rgb\(([^)]+)\)$/i.exec(css);
  if (rgb) return `rgba(${rgb[1]}, ${alpha})`;
  return css;
}

/**
 * 3D 카메라 고도를 2D 줌으로 옮긴다.
 *
 * 백엔드는 필지 크기에 맞춰 고도(m)를 보내므로 그 의도를 살려야 한다.
 * 고도 2500m ≒ 줌 17, 500m ≒ 줌 19.3 이 되도록 맞췄다.
 */
function altitudeToZoom(altitude: number): number {
  const safe = Math.max(50, altitude || 2500);
  return Math.min(20, Math.max(6, Math.log2(327_680_000 / safe)));
}

export class Map2DBridge implements MapSurface {
  readonly capabilities: MapCapabilities = {
    // 매싱·절토는 높이를 세우는 표현이라 평면으로 대체할 수 없다.
    massing: false,
    earthwork: false,
    // 경사도는 백엔드가 셀 도형과 경사도(deg)를 함께 주므로 색으로 깔 수 있다.
    slope: true,
    viewModeToggle: false,
    heightMeasure: false,
  };

  private readonly map: Map;
  private readonly baseLayer: TileLayer<XYZ>;

  // 레이어는 3D 구현과 같은 수명 규칙을 따른다.
  //  - parcel/zone/dimension: clear_mass 로 지워지는 진단 표시
  //  - persist: 분할 오버레이 등 clear_mass 로도 남는 지속 표시
  //  - cadastre/zoning/slope: 사용자가 버튼으로 켜고 끄는 레이어
  private readonly parcelSrc = new VectorSource();
  private readonly zoneSrc = new VectorSource();
  private readonly persistSrc = new VectorSource();
  private readonly dimensionSrc = new VectorSource();
  private readonly cadastreSrc = new VectorSource();
  private readonly zoningSrc = new VectorSource();
  private readonly slopeSrc = new VectorSource();
  private readonly measureSrc = new VectorSource();

  private lastFocus: { lon: number; lat: number } | null = null;
  private lastDimensions: Extract<MapCommand, { type: "show_dimensions" }> | null = null;
  private dimensionsVisible = true;

  private slopeData: Extract<MapCommand, { type: "set_slope_data" }> | null = null;
  private cadastreEnabled = false;
  private cadastreReloadTimer: number | null = null;
  private cadastreGeneration = 0;

  private measureDraw: Draw | null = null;
  private footprintFeatures: Feature[] = [];

  constructor(target: HTMLElement, baseLayer: BaseLayerId = "midnight") {
    this.baseLayer = new TileLayer({
      source: this.tileSource(baseLayer),
    });

    this.map = new Map({
      target,
      layers: [
        this.baseLayer,
        // 아래에서 위로: 용도지역(면) → 경사도 → 지적선 → 지속 → 필지 → 치수 → 측정
        this.vectorLayer(this.zoningSrc, 10),
        this.vectorLayer(this.slopeSrc, 20),
        this.vectorLayer(this.cadastreSrc, 30),
        this.vectorLayer(this.persistSrc, 40, true),
        this.vectorLayer(this.zoneSrc, 45),
        this.vectorLayer(this.parcelSrc, 50),
        this.vectorLayer(this.dimensionSrc, 60, true),
        this.vectorLayer(this.measureSrc, 70),
      ],
      view: new View({
        center: fromLonLat([127.0, 37.55]),
        zoom: 12,
        maxZoom: 20,
        minZoom: 6,
        // 2D 는 항상 북향이다. 회전을 막아 나침반·치수 라벨과 어긋나지 않게 한다.
        enableRotation: false,
      }),
      controls: [],
    });

    // 지적도가 켜져 있으면 이동한 화면 중심으로 다시 조회한다(3D 와 같은 동작).
    this.map.on("moveend", () => {
      if (!this.cadastreEnabled) return;
      if (this.cadastreReloadTimer != null) window.clearTimeout(this.cadastreReloadTimer);
      this.cadastreReloadTimer = window.setTimeout(() => {
        this.cadastreReloadTimer = null;
        const focus = this.centerLonLat();
        if (focus) void this.loadCadastre(focus.lon, focus.lat);
      }, 350);
    });
  }

  private tileSource(layer: BaseLayerId): XYZ {
    return new XYZ({
      url: TILE_URL.replace("{layer}", layer).replace("{ext}", TILE_EXT[layer]),
      crossOrigin: "anonymous",
      maxZoom: TILE_MAX_ZOOM[layer],
      attributions: "© VWorld",
    });
  }

  private vectorLayer(
    source: VectorSource,
    zIndex: number,
    declutter = false,
  ): VectorLayer<VectorSource> {
    return new VectorLayer({
      source,
      zIndex,
      // 스타일은 피처마다 심어 둔다 — 레이어 단위로 강제하면 조각별 색을 잃는다.
      style: (feature) => feature.get("style") ?? null,
      // 치수·라벨은 좁은 필지에서 서로 겹쳐 글자가 뭉개진다. 겹치는 라벨을
      // 떨어뜨려 읽을 수 있는 것만 남긴다(3D 는 깊이 정렬로 해결하는 문제다).
      declutter,
    });
  }

  setBaseLayer(layer: BaseLayerId): void {
    this.baseLayer.setSource(this.tileSource(layer));
  }

  /** OL 은 컨테이너 크기 변화를 스스로 못 잡는 경우가 있어 밖에서 불러 준다. */
  updateSize(): void {
    this.map.updateSize();
  }

  dispose(): void {
    if (this.cadastreReloadTimer != null) window.clearTimeout(this.cadastreReloadTimer);
    this.map.setTarget(undefined);
  }

  private centerLonLat(): { lon: number; lat: number } | null {
    const center = this.map.getView().getCenter();
    if (!center) return null;
    const [lon, lat] = toLonLat(center);
    return { lon, lat };
  }

  // ------------------------------------------------------------ 피처 만들기

  private polygonFeature(
    geometry: GeoJSONPolygon,
    style: Style,
    props: Record<string, unknown> = {},
  ): Feature[] {
    return outerRings(geometry).map((ring) => {
      const feature = new Feature({ geometry: new Polygon([toMercatorRing(ring)]) });
      feature.set("style", style);
      for (const [k, v] of Object.entries(props)) feature.set(k, v);
      return feature;
    });
  }

  private areaStyle(color: string, label?: string, alpha = 0.35): Style {
    return new Style({
      fill: new Fill({ color: withAlpha(color, alpha) }),
      stroke: new Stroke({ color: withAlpha(color, 0.95), width: 1.5 }),
      text: label
        ? new Text({
            text: label,
            font: "12px system-ui, sans-serif",
            fill: new Fill({ color: "#ffffff" }),
            stroke: new Stroke({ color: "rgba(0,0,0,0.75)", width: 3 }),
            overflow: true,
          })
        : undefined,
    });
  }

  // ---------------------------------------------------------------- 명령 실행

  execute(commands: MapCommand[]): void {
    for (const command of commands) {
      try {
        switch (command.type) {
          case "clear_mass":
            this.parcelSrc.clear();
            this.footprintFeatures = [];
            this.zoneSrc.clear();
            this.dimensionSrc.clear();
            this.lastDimensions = null;
            break;
          case "clear_division_overlay":
            this.persistSrc.clear();
            break;
          case "fly_to":
            this.moveTo(command.lon, command.lat, command.altitude);
            break;
          case "highlight_parcel":
            this.highlightParcel(command.geometry, command.label, command.color);
            break;
          case "show_zone_pieces":
            this.showZonePieces(command);
            break;
          case "show_restriction_pieces":
            this.showRestrictionPieces(command);
            break;
          case "show_dimensions":
            this.lastDimensions = command;
            if (this.dimensionsVisible) this.showDimensions(command);
            break;
          case "extrude_mass":
            // 높이는 세울 수 없지만 건물이 대지의 어디에 앉는지는 평면으로도
            // 그대로 보여줄 수 있다. 층수·높이는 라벨로 남긴다.
            this.showFootprint(command);
            break;
          case "show_building_footprint":
          case "show_building_shape":
            this.setFootprintVisible(true);
            break;
          case "hide_building_shape":
            this.setFootprintVisible(false);
            break;
          case "set_slope_data":
            this.slopeData = command;
            break;
          // 아래는 높이를 세우는 3D 전용 표현이라 평면으로 대체할 수 없다.
          // 조용히 넘기지 않고 무엇이 빠졌는지 남긴다.
          case "show_lod1":
          case "show_housing_model":
          case "set_earthwork_mode":
            console.info(`[Map2D] 3D 전용 명령 건너뜀: ${command.type}`);
            break;
          default:
            // set_layers·run_tool·show_panel 등은 MapCanvas/App 이 처리한다.
            break;
        }
      } catch (error) {
        console.error(`[Map2D] 명령 처리 실패 (${command.type}):`, error);
      }
    }
  }

  private highlightParcel(geometry: GeoJSONPolygon, label: string, color: string): void {
    this.parcelSrc
      .getFeatures()
      .filter((f) => f.get("role") === "parcel")
      .forEach((f) => this.parcelSrc.removeFeature(f));
    const style = new Style({
      fill: new Fill({ color: withAlpha(color, 0.18) }),
      stroke: new Stroke({ color: withAlpha(color, 1), width: 3 }),
      text: new Text({
        text: label,
        font: "600 13px system-ui, sans-serif",
        fill: new Fill({ color: "#ffffff" }),
        stroke: new Stroke({ color: "rgba(0,0,0,0.8)", width: 3.5 }),
        overflow: true,
      }),
    });
    this.parcelSrc.addFeatures(this.polygonFeature(geometry, style, { role: "parcel" }));
    const ring = outerRings(geometry)[0];
    if (ring?.length) {
      const lon = ring.reduce((s, p) => s + p[0], 0) / ring.length;
      const lat = ring.reduce((s, p) => s + p[1], 0) / ring.length;
      this.lastFocus = { lon, lat };
    }
  }

  private showZonePieces(command: Extract<MapCommand, { type: "show_zone_pieces" }>): void {
    const target = command.persist ? this.persistSrc : this.zoneSrc;
    if (!command.persist) this.zoneSrc.clear();
    for (const piece of command.pieces) {
      const label = piece.share_pct != null ? `${piece.zone} ${piece.share_pct}%` : piece.zone;
      target.addFeatures(
        this.polygonFeature(piece.geometry, this.areaStyle(piece.color, label)),
      );
    }
  }

  private showRestrictionPieces(
    command: Extract<MapCommand, { type: "show_restriction_pieces" }>,
  ): void {
    for (const piece of command.pieces) {
      if (!piece.geometry) continue; // 도형 없는 제약은 범례로만 안내한다(3D 와 동일).
      this.parcelSrc.addFeatures(
        this.polygonFeature(piece.geometry, this.areaStyle(piece.color, piece.label, 0.28)),
      );
    }
  }

  private showFootprint(command: Extract<MapCommand, { type: "extrude_mass" }>): void {
    // 건축면적 윤곽이 따로 오면 그걸 쓴다 — 매스 외곽(geometry)은 상부까지
    // 포함한 형상이라 대지에 앉는 자리와 다를 수 있다.
    const geometry = command.footprint_geometry ?? command.geometry;
    if (!geometry) return;
    this.setFootprintVisible(false);
    const color = command.color || "#ffd166";
    const label = command.floors ? `${command.label} · 지상 ${command.floors}층` : command.label;
    const style = new Style({
      fill: new Fill({ color: withAlpha(color, 0.3) }),
      stroke: new Stroke({ color: withAlpha(color, 1), width: 2 }),
      text: new Text({
        text: label,
        font: "600 12px system-ui, sans-serif",
        fill: new Fill({ color: "#ffffff" }),
        stroke: new Stroke({ color: "rgba(0,0,0,0.8)", width: 3.5 }),
        overflow: true,
      }),
    });
    this.footprintFeatures = this.polygonFeature(geometry, style, { role: "footprint" });
    this.parcelSrc.addFeatures(this.footprintFeatures);
  }

  private setFootprintVisible(on: boolean): void {
    for (const feature of this.footprintFeatures) {
      const present = this.parcelSrc.hasFeature(feature);
      if (on && !present) this.parcelSrc.addFeature(feature);
      if (!on && present) this.parcelSrc.removeFeature(feature);
    }
  }

  private showDimensions(
    command: Extract<MapCommand, { type: "show_dimensions" }>,
  ): void {
    const target = command.persist ? this.persistSrc : this.dimensionSrc;
    if (!command.persist) this.dimensionSrc.clear();

    for (const segment of command.segments) {
      const line = new LineString(
        segment.positions.map(([lon, lat]) => fromLonLat([lon, lat])),
      );
      const feature = new Feature({ geometry: line });
      feature.set(
        "style",
        new Style({
          stroke: new Stroke({
            color: withAlpha(segment.color ?? "#ffffff", 0.95),
            width: segment.width ?? 2,
            // 이격선은 점선으로 그어 실선(대지 경계)과 구분한다.
            lineDash: segment.kind?.includes("setback") ? [6, 4] : undefined,
          }),
          // placement:"line" 은 쓰지 않는다. 이격선은 대개 세로라 글자가 90°
          // 누워 세로쓰기처럼 뭉개진다. 선 중점에 가로로 놓는다.
          text: segment.label
            ? new Text({
                text: segment.label,
                font: "600 12px system-ui, sans-serif",
                fill: new Fill({ color: "#ffffff" }),
                stroke: new Stroke({ color: "rgba(0,0,0,0.85)", width: 3.5 }),
                overflow: true,
              })
            : undefined,
        }),
      );
      target.addFeature(feature);
    }

    for (const label of command.labels) {
      const feature = new Feature({
        geometry: new Point(fromLonLat([label.lon, label.lat])),
      });
      feature.set(
        "style",
        new Style({
          text: new Text({
            text: label.text,
            font: "600 12px system-ui, sans-serif",
            fill: new Fill({ color: label.color ?? "#ffffff" }),
            stroke: new Stroke({ color: "rgba(0,0,0,0.8)", width: 3.5 }),
            offsetY: label.offset ? -14 : 0,
          }),
        }),
      );
      target.addFeature(feature);
    }
  }

  // ------------------------------------------------------------- MapSurface

  moveTo(lon: number, lat: number, altitude = 2500): void {
    this.lastFocus = { lon, lat };
    this.map.getView().animate({
      center: fromLonLat([lon, lat]),
      zoom: altitudeToZoom(altitude),
      duration: 500,
    });
  }

  setDimensionsVisible(on: boolean): void {
    this.dimensionsVisible = on;
    if (on) {
      if (this.lastDimensions) this.showDimensions(this.lastDimensions);
    } else {
      this.dimensionSrc.clear();
    }
  }

  /** 건물 매싱은 높이 표현이라 2D 에 없다. 화면은 capabilities 로 버튼을 감춘다. */
  showHousingModel(_type: HousingModelType): EarthworkEstimate | null {
    console.info("[Map2D] 건물 매싱은 3D 전용이라 표시하지 않는다.");
    return null;
  }

  toScreenAboveGround(lon: number, lat: number): { x: number; y: number } | null {
    try {
      const pixel = this.map.getPixelFromCoordinate(fromLonLat([lon, lat]));
      if (!pixel) return null;
      return { x: pixel[0], y: pixel[1] };
    } catch {
      return null;
    }
  }

  onCameraChange(cb: () => void): () => void {
    // postrender 는 이동·줌·리사이즈를 모두 덮어 팝업이 지도와 어긋나지 않는다.
    const key = this.map.on("postrender", cb);
    return () => unByKey(key);
  }

  onMapClick(
    cb: (lon: number, lat: number, jibun: string, pnu: string) => void,
    onError?: (message: string) => void,
  ): () => void {
    const handler = async (event: any) => {
      if (this.measureDraw) return; // 측정 중 클릭은 측정이 가져간다.
      const [lon, lat] = toLonLat(event.coordinate);
      try {
        const response = await fetch(`/api/parcel-at?lon=${lon}&lat=${lat}`);
        if (!response.ok) {
          const detail = (await response.text()).slice(0, 160);
          throw new Error(`HTTP ${response.status}${detail ? ` - ${detail}` : ""}`);
        }
        const parcel = await response.json();
        if (!parcel?.geometry) throw new Error("응답에 필지 geometry가 없습니다.");
        this.highlightParcel(parcel.geometry, parcel.jibun ?? "선택 필지", "#3fa7c4");
        cb(lon, lat, parcel.jibun ?? "", parcel.pnu ?? "");
      } catch (error) {
        onError?.(`필지 조회 실패: ${error instanceof Error ? error.message : error}`);
      }
    };
    this.map.on("singleclick", handler);
    return () => this.map.un("singleclick", handler);
  }

  /** 2D 는 시점 전환이 없다. 3D 요청은 받아만 두고 화면은 그대로 둔다. */
  async setViewMode(_mode: "2d" | "3d"): Promise<void> {
    return;
  }

  async setCadastreOverlay(on: boolean, lon?: number, lat?: number): Promise<number> {
    this.cadastreEnabled = on;
    if (!on) {
      this.cadastreSrc.clear();
      return 0;
    }
    const focus = this.centerLonLat();
    const focusLon = lon ?? focus?.lon ?? this.lastFocus?.lon;
    const focusLat = lat ?? focus?.lat ?? this.lastFocus?.lat;
    if (focusLon == null || focusLat == null) return 0;
    return this.loadCadastre(focusLon, focusLat);
  }

  private async loadCadastre(lon: number, lat: number): Promise<number> {
    // 새 경계를 다 만든 뒤 한 번에 교체한다 — 먼저 지우면 조회 시간 동안 깜박인다.
    const generation = ++this.cadastreGeneration;
    const dx = 0.004;
    const dy = 0.003;
    const response = await fetch(
      `/api/parcels?west=${lon - dx}&south=${lat - dy}&east=${lon + dx}&north=${lat + dy}`,
    );
    if (!response.ok) throw new Error(`지적도 조회 실패: HTTP ${response.status}`);
    const data = await response.json();
    if (generation !== this.cadastreGeneration) return 0; // 더 최신 조회가 있다.

    const style = new Style({
      stroke: new Stroke({ color: "rgba(255,255,255,0.55)", width: 1 }),
    });
    const features: Feature[] = [];
    for (const parcel of data?.parcels ?? []) {
      if (!parcel?.geometry) continue;
      features.push(...this.polygonFeature(parcel.geometry, style));
    }
    this.cadastreSrc.clear();
    this.cadastreSrc.addFeatures(features);
    return features.length;
  }

  async setZoningOverlay(on: boolean, lon?: number, lat?: number): Promise<number> {
    this.zoningSrc.clear();
    if (!on) return 0;
    const focus = this.centerLonLat();
    const focusLon = lon ?? this.lastFocus?.lon ?? focus?.lon;
    const focusLat = lat ?? this.lastFocus?.lat ?? focus?.lat;
    if (focusLon == null || focusLat == null) return 0;

    const dx = 0.004;
    const dy = 0.003;
    const response = await fetch(
      `/api/zoning-area?west=${focusLon - dx}&south=${focusLat - dy}` +
        `&east=${focusLon + dx}&north=${focusLat + dy}`,
    );
    if (!response.ok) throw new Error(`용도지역 조회 실패: HTTP ${response.status}`);
    const data = await response.json();

    const features: Feature[] = [];
    for (const zone of data?.zones ?? data?.polygons ?? []) {
      if (!zone?.geometry) continue;
      features.push(
        ...this.polygonFeature(zone.geometry, this.areaStyle(zone.color, zone.zone, 0.25)),
      );
    }
    this.zoningSrc.addFeatures(features);
    return features.length;
  }

  setSlopeGrid(on: boolean): SlopeGridResult {
    this.slopeSrc.clear();
    const data = this.slopeData;
    if (!on || !data?.cells?.length) return EMPTY_SLOPE;

    for (const cell of data.cells) {
      // 완만(초록) → 급경사(빨강). 25도를 상한으로 잡는다(개발행위 허가 기준대).
      const t = Math.min(1, Math.max(0, cell.slope_deg / 25));
      const color = `rgb(${Math.round(60 + 195 * t)}, ${Math.round(200 - 150 * t)}, 90)`;
      this.slopeSrc.addFeatures(
        this.polygonFeature(
          cell.geometry,
          new Style({
            fill: new Fill({ color: withAlpha(color, 0.45) }),
            stroke: new Stroke({ color: withAlpha(color, 0.6), width: 0.5 }),
          }),
        ),
      );
    }
    return {
      cells: data.cells.length,
      maxSlope: data.max_slope_deg,
      avgSlope: data.mean_slope_deg,
      minElev: data.min_elevation_m,
      maxElev: data.max_elevation_m,
      source: data.source,
      resolutionM: data.resolution_m,
    };
  }

  /** 2D 는 회전을 막아 두었으므로 항상 북향이다. */
  cameraHeadingDeg(): number {
    return 0;
  }

  focusLonLat(): { lon: number; lat: number } | null {
    return this.lastFocus ?? this.centerLonLat();
  }

  // ------------------------------------------------------------------ 측정

  /**
   * 거리·면적 측정. 3D 는 VWorld 내장 도구를 쓰지만 그건 엔진에 묶여 있어
   * 2D 에서는 OpenLayers Draw 로 직접 구현한다. 길이·면적은 타원체상에서
   * 계산하므로(getLength/getArea) 투영 왜곡이 들어가지 않는다.
   */
  startMeasure(mode: "line" | "area", onNote: (message: string) => void): void {
    this.stopMeasure();
    const draw = new Draw({
      source: this.measureSrc,
      type: mode === "line" ? "LineString" : "Polygon",
      style: new Style({
        fill: new Fill({ color: "rgba(63,167,196,0.25)" }),
        stroke: new Stroke({ color: "#3fa7c4", width: 2, lineDash: [6, 4] }),
        image: new Circle({ radius: 4, fill: new Fill({ color: "#3fa7c4" }) }),
      }),
    });
    draw.on("drawend", (event: any) => {
      const geometry = event.feature.getGeometry();
      const text =
        mode === "line"
          ? `${getLength(geometry).toFixed(1)} m`
          : `${getArea(geometry).toFixed(1)} m² (${(getArea(geometry) / 3.3058).toFixed(1)}평)`;
      event.feature.set(
        "style",
        new Style({
          fill: new Fill({ color: "rgba(63,167,196,0.2)" }),
          stroke: new Stroke({ color: "#3fa7c4", width: 2 }),
          text: new Text({
            text,
            font: "600 12px system-ui, sans-serif",
            fill: new Fill({ color: "#ffffff" }),
            stroke: new Stroke({ color: "rgba(0,0,0,0.8)", width: 3.5 }),
          }),
        }),
      );
      onNote(text);
    });
    this.map.addInteraction(draw);
    this.measureDraw = draw;
    onNote(
      mode === "line"
        ? "거리 측정: 지도를 클릭해 점을 찍고 더블클릭으로 끝냅니다."
        : "면적 측정: 꼭짓점을 클릭하고 더블클릭으로 끝냅니다.",
    );
  }

  stopMeasure(): void {
    if (this.measureDraw) {
      this.map.removeInteraction(this.measureDraw);
      this.measureDraw = null;
    }
  }

  eraseMeasure(): void {
    this.stopMeasure();
    this.measureSrc.clear();
  }
}
