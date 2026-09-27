const MAGIC = "TLC1";
const VERSION = 1;
const HEADER_BYTES = 16;

export interface CachedLine {
  coordinates: Array<[number, number]>;
}

export function encodeLines(lines: readonly CachedLine[]): ArrayBuffer {
  const pointCount = lines.reduce((sum, line) => sum + line.coordinates.length, 0);
  const offsetsBytes = (lines.length + 1) * 4;
  const buffer = new ArrayBuffer(HEADER_BYTES + offsetsBytes + pointCount * 8);
  const view = new DataView(buffer);
  for (let index = 0; index < MAGIC.length; index += 1) view.setUint8(index, MAGIC.charCodeAt(index));
  view.setUint32(4, VERSION, true);
  view.setUint32(8, lines.length, true);
  view.setUint32(12, pointCount, true);
  const offsets = new Uint32Array(buffer, HEADER_BYTES, lines.length + 1);
  const coords = new Int32Array(buffer, HEADER_BYTES + offsetsBytes, pointCount * 2);
  let offset = 0;
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    offsets[lineIndex] = offset;
    for (const [lon, lat] of lines[lineIndex].coordinates) {
      coords[offset * 2] = Math.round(lon * 1e6);
      coords[offset * 2 + 1] = Math.round(lat * 1e6);
      offset += 1;
    }
  }
  offsets[lines.length] = offset;
  return buffer;
}

export function decodeLines(buffer: ArrayBuffer): { version: number; lines: CachedLine[] } {
  if (buffer.byteLength < HEADER_BYTES) throw new Error("Некорректный TLC1");
  const view = new DataView(buffer);
  const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (magic !== MAGIC) throw new Error("Некорректный TLC1: неверная сигнатура");
  const version = view.getUint32(4, true);
  if (version !== VERSION) throw new Error(\`Неподдерживаемая версия TLC1: \${version}\`);
  const lineCount = view.getUint32(8, true);
  const pointCount = view.getUint32(12, true);
  const offsetsStart = HEADER_BYTES;
  const offsetsEnd = offsetsStart + (lineCount + 1) * 4;
  if (offsetsEnd + pointCount * 8 > buffer.byteLength) throw new Error("Некорректный TLC1: обрезанный payload");
  const offsets = new Uint32Array(buffer, offsetsStart, lineCount + 1);
  const coords = new Int32Array(buffer, offsetsEnd, pointCount * 2);
  const lines: CachedLine[] = new Array(lineCount);
  for (let lineIndex = 0; lineIndex < lineCount; lineIndex += 1) {
    const start = offsets[lineIndex];
    const end = offsets[lineIndex + 1];
    const line: Array<[number, number]> = new Array(end - start);
    for (let point = start; point < end; point += 1) {
      line[point - start] = [coords[point * 2] / 1e6, coords[point * 2 + 1] / 1e6];
    }
    lines[lineIndex] = { coordinates: line };
  }
  return { version, lines };
}
