const MAGIC = "TKBL";
const MAGIC_BYTES = new TextEncoder().encode(MAGIC);
const VERSION = 1;
const HEADER_BYTES = 24;
const SENTINEL = -32768;

export type TkblBuilding = {
  points: Array<[number, number]>;
};

export type TkblHeader = {
  version: number;
  buildingCount: number;
  pointCount: number;
  originX: number;
  originY: number;
};

export function encodeBuildings(buildings: readonly TkblBuilding[]): ArrayBuffer {
  const polygons = buildings.filter((building) => building.points.length >= 3);
  const pointCount = polygons.reduce((sum, building) => sum + building.points.length, 0);
  if (!pointCount) {
    const empty = new ArrayBuffer(HEADER_BYTES);
    const emptyView = new DataView(empty);
    emptyView.setUint32(0, 0x4c424b54, true);
    emptyView.setUint32(4, VERSION, true);
    return empty;
  }

  let originX = Math.round(Math.min(...polygons.flatMap((building) => building.points.map(([x]) => x))) * 1e6);
  let originY = Math.round(Math.min(...polygons.flatMap((building) => building.points.map(([, y]) => y))) * 1e6);

  const payload: number[] = [];
  const offsets = new Uint32Array(polygons.length + 1);
  let currentX = 0;
  let currentY = 0;
  let pointOffset = 0;

  for (let index = 0; index < polygons.length; index += 1) {
    offsets[index] = pointOffset;
    for (const [lon, lat] of polygons[index].points) {
      const targetX = Math.round(lon * 1e6) - originX;
      const targetY = Math.round(lat * 1e6) - originY;
      const dx = targetX - currentX;
      const dy = targetY - currentY;
      const view = new DataView(new ArrayBuffer(dx === SENTINEL || dy === SENTINEL || dx < -32767 || dx > 32767 || dy < -32767 || dy > 32767 ? 12 : 4));
      if (view.byteLength === 4) {
        view.setInt16(0, dx, true);
        view.setInt16(2, dy, true);
        for (let byte = 0; byte < 4; byte += 1) payload.push(view.getUint8(byte));
      } else {
        view.setInt16(0, SENTINEL, true);
        view.setInt16(2, SENTINEL, true);
        view.setInt32(4, targetX, true);
        view.setInt32(8, targetY, true);
        for (let byte = 0; byte < 12; byte += 1) payload.push(view.getUint8(byte));
      }
      currentX = targetX;
      currentY = targetY;
      pointOffset += 1;
    }
  }
  offsets[polygons.length] = pointOffset;

  const buffer = new ArrayBuffer(
    HEADER_BYTES + offsets.byteLength + payload.length,
  );
  const view = new DataView(buffer);
  view.setUint32(0, 0x4c424b54, true);
  view.setUint32(4, VERSION, true);
  view.setUint32(8, polygons.length, true);
  view.setUint32(12, pointCount, true);
  view.setInt32(16, originX, true);
  view.setInt32(20, originY, true);
  new Uint8Array(buffer, HEADER_BYTES, offsets.byteLength).set(new Uint8Array(offsets.buffer));
  new Uint8Array(buffer, HEADER_BYTES + offsets.byteLength).set(new Uint8Array(payload));
  return buffer;
}

export function decodeBuildings(buffer: ArrayBuffer): { header: TkblHeader; buildings: TkblBuilding[] } {
  if (buffer.byteLength < HEADER_BYTES) throw new Error("Некорректный TKBL");
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x4c424b54) throw new Error("Некорректный TKBL: неверная сигнатура");
  const version = view.getUint32(4, true);
  if (version !== VERSION) throw new Error(\`Неподдерживаемая версия TKBL: \${version}\`);
  const buildingCount = view.getUint32(8, true);
  const pointCount = view.getUint32(12, true);
  const originX = view.getInt32(16, true);
  const originY = view.getInt32(20, true);
  const offsetsStart = HEADER_BYTES;
  const offsetsEnd = offsetsStart + (buildingCount + 1) * 4;
  if (offsetsEnd > buffer.byteLength) throw new Error("Некорректный TKBL: повреждённые offsets");
  const offsets = new Uint32Array(buffer, offsetsStart, buildingCount + 1);
  const buildings: TkblBuilding[] = new Array(buildingCount);
  let cursor = offsetsEnd;
  let currentX = 0;
  let currentY = 0;
  let decodedPoints = 0;

  for (let index = 0; index < buildingCount; index += 1) {
    const count = offsets[index + 1] - offsets[index];
    const points: Array<[number, number]> = new Array(count);
    for (let pointIndex = 0; pointIndex < count; pointIndex += 1) {
      if (cursor + 4 > buffer.byteLength) throw new Error("Некорректный TKBL: обрезанный payload");
      const dx = view.getInt16(cursor, true);
      const dy = view.getInt16(cursor + 2, true);
      cursor += 4;
      if (dx === SENTINEL && dy === SENTINEL) {
        if (cursor + 8 > buffer.byteLength) throw new Error("Некорректный TKBL: обрезанный абсолютный point");
        currentX = view.getInt32(cursor, true);
        currentY = view.getInt32(cursor + 4, true);
        cursor += 8;
      } else {
        currentX += dx;
        currentY += dy;
      }
      points[pointIndex] = [(originX + currentX) / 1e6, (originY + currentY) / 1e6];
      decodedPoints += 1;
    }
    buildings[index] = { points };
  }

  if (decodedPoints !== pointCount) throw new Error("Некорректный TKBL: point count mismatch");
  return {
    header: { version, buildingCount, pointCount, originX, originY },
    buildings,
  };
}
