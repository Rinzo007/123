const EARTH_RADIUS_M = 6378137;

export interface LocalMeters {
  x: number;
  y: number;
}

export function toLocalMeters(
  lon: number,
  lat: number,
  lon0: number,
  lat0: number,
): LocalMeters {
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  return {
    x: ((lon - lon0) * Math.PI) / 180 * EARTH_RADIUS_M * cosLat,
    y: ((lat - lat0) * Math.PI) / 180 * EARTH_RADIUS_M,
  };
}

export function fromLocalMeters(
  x: number,
  y: number,
  lon0: number,
  lat0: number,
): [number, number] {
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  return [
    lon0 + (x / Math.max(1e-9, EARTH_RADIUS_M * cosLat)) * 180 / Math.PI,
    lat0 + (y / EARTH_RADIUS_M) * 180 / Math.PI,
  ];
}
