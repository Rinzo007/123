const MAGIC = "TKBL";
const VERSION = 1;
const HEADER_BYTES = 16;

export interface TkblLine {
  coordinates: Array<[number, number]>;
}

export interface TkblHeader {
  version: number;
  lineCount: number;
  pointCount: number;
}

export function encodeLines(lines: readonly TkblLine[]): ArrayBuffer {
  const pointCount = lines.reduce((sum, line) => sum + line.coordinates.length, 0);
  const offsetsBytes = (lines.length + 1) * 4;
  const totalBytes = HEADER_BYTES + offsetsBytes + pointCount * 8;
  const buffer = new ArrayBuffer(totalBytes);
  const bytes = new Uint8Array(buffer, 0, 4);
  for (let i = 0; i < MAGIC.length; i += 1) bytes[i] = MAGIC.charCodeAt(i);

  const view = new DataView(buffer);
  view.setUint16(4, VERSION, true);
  view.setUint16(6, 0, true);
  view.setUint32(8, lines.length, true);
  view.setUint32(12, pointCount, true);

  const offsets = new Uint32Array(buffer, HEADER_BYTES, lines.length + 1);
  const coordinates = new Int32Array(buffer, HEADER_BYTES + offsetsBytes, pointCount * 2);
  let pointOffset = 0;
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    offsets[lineIndex] = pointOffset;
    for (const [lon, lat] of line.coordinates) {
      coordinates[pointOffset * 2] = Math.round(lon * 1_000_000);
      coordinates[pointOffset * 2 + 1] = Math.round(lat * 1_000_000);
      pointOffset += 1;
    }
  }
  offsets[lines.length] = pointOffset;
  return buffer;
}

export function decodeLines(buffer: ArrayBuffer): { header: TkblHeader; lines: TkblLine[] } {
  if (buffer.byteLength < HEADER_BYTES) throw new Error("Некорректный TKBL: короткий заголовок");
  const bytes = new Uint8Array(buffer, 0, 4);
  const magic = String.fromCharCode(...bytes);
  if (magic !== MAGIC) throw new Error("Некорректный TKBL: неверная сигнатура");

  const view = new DataView(buffer);
  const version = view.getUint16(4, true);
  if (version !== VERSION) throw new Error(\`Неподдерживаемая версия TKBL: \${version}\`);
  const lineCount = view.getUint32(8, true);
  const pointCount = view.getUint32(12, true);
  const offsetsStart = HEADER_BYTES;
  const offsetsEnd = offsetsStart + (lineCount + 1) * 4;
  const coordinatesStart = offsetsEnd;
  if (coordinatesStart + pointCount * 8 > buffer.byteLength) {
    throw new Error("Некорректный TKBL: повреждённый массив координат");
  }

  const offsets = new Uint32Array(buffer, offsetsStart, lineCount + 1);
  const coordinates = new Int32Array(buffer, coordinatesStart, pointCount * 2);
  const lines: TkblLine[] = new Array(lineCount);
  for (let lineIndex = 0; lineIndex < lineCount; lineIndex += 1) {
    const start = offsets[lineIndex];
    const end = offsets[lineIndex + 1];
    const line: Array<[number, number]> = new Array(end - start);
    for (let index = start; index < end; index += 1) {
      line[index - start] = [
        coordinates[index * 2] / 1_000_000,
        coordinates[index * 2 + 1] / 1_000_000,
      ];
    }
    lines[lineIndex] = { coordinates: line };
  }
  return { header: { version, lineCount, pointCount }, lines };
}
