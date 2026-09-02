/**
 * 지도 구현이 App 에 제공해야 하는 최소 표면.
 *
 * 3D(`MapBridge`, VWorld ws3d/Cesium)와 2D(`Map2DBridge`, OpenLayers)가 이걸
 * 함께 구현한다. App·MapCanvas·MapCompass 는 이 타입만 알면 되므로, WebGL 이
 * 없는 환경에서 2D 로 갈아끼워도 호출부를 고칠 필요가 없다.
 *
 * 여기 없는 3D 전용 기능(건물 매싱·절토·지형 조회)은 2D 구현이 no-op 로
 * 받아넘기고 `capabilities` 로 무엇이 빠졌는지 알린다. 화면은 그 값을 보고
 * 해당 버튼을 감춘다 — 눌러도 아무 일이 없는 버튼을 남기지 않기 위해서다.
 */
import type { EarthworkEstimate, HousingModelType, MapCommand } from "./mapBridge";

export interface SlopeGridResult {
  cells: number;
  maxSlope: number;
  avgSlope: number;
  minElev: number;
  maxElev: number;
  source: string;
  resolutionM: number;
}

export interface MapCapabilities {
  /** 3D 전용: 건물 매싱·LOD1·주택 모델 */
  massing: boolean;
  /** 3D 전용: 절토·성토 추정 */
  earthwork: boolean;
  /** 3D 전용: 표고 기반 경사도 격자 */
  slope: boolean;
  /** 3D 전용: 2D/3D 시점 전환 */
  viewModeToggle: boolean;
  /** 높이 측정(거리·면적은 2D 도 가능) */
  heightMeasure: boolean;
}

export interface MapSurface {
  readonly capabilities: MapCapabilities;

  execute(commands: MapCommand[]): void;
  moveTo(lon: number, lat: number, altitude?: number, tilt?: number): void;

  setDimensionsVisible(on: boolean): void;
  showHousingModel(type: HousingModelType): EarthworkEstimate | null;

  toScreenAboveGround(
    lon: number,
    lat: number,
    heightAboveGround?: number,
  ): { x: number; y: number } | null;

  onCameraChange(cb: () => void): () => void;
  onMapClick(
    cb: (lon: number, lat: number, jibun: string, pnu: string) => void,
    onError?: (message: string) => void,
  ): () => void;

  setViewMode(mode: "2d" | "3d"): Promise<void>;
  setCadastreOverlay(on: boolean, lon?: number, lat?: number): Promise<number>;
  setZoningOverlay(on: boolean, lon?: number, lat?: number): Promise<number>;
  setSlopeGrid(on: boolean): SlopeGridResult;

  cameraHeadingDeg(): number;
  focusLonLat(): { lon: number; lat: number } | null;
}
