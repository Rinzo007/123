const MAGIC = 0x54534b54; // "TKST" little-endian
const VERSION = 1;
const HEADER_BYTES = 24;

export interface StreetsBinary {
  version: number;
  vertices: Int32Array;
  edgeA: Uint32Array;
  edgeB: Uint32Array;
  edgeLen: Uint32Array;
  edgeName: Int32Array;
  flatOff: Uint32Array;
  edgeCls: Uint8Array;
  flatI: Int32Array;
  names: string[];
}

function align4(offset: number): number {
  return offset + ((4 - (offset % 4)) % 4);
}

export function decodeStreetsBin(buffer: ArrayBuffer): StreetsBinary {
  if (buffer.byteLength < HEADER_BYTES) throw new Error("Некорректный streets.bin");
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== MAGIC) throw new Error("Некорректный streets.bin: неверная сигнатура");
  const version = view.getUint32(4, true);
  if (version !== VERSION) throw new Error(\`Неподдерживаемая версия streets.bin: \${version}\`);
  const vertexCount = view.getUint32(8, true);
  const edgeCount = view.getUint32(12, true);
  const flatCount = view.getUint32(16, true);
  const namesBytes = view.getUint32(20, true);

  let offset = HEADER_BYTES;
  const take = (bytes: number): number => {
    const start = offset;
    offset += bytes;
    if (offset > buffer.byteLength) throw new Error("Некорректный streets.bin: повреждённый массив");
    return start;
  };

  const vertices = new Int32Array(buffer, take(vertexCount * 2 * 4), vertexCount * 2);
  const edgeA = new Uint32Array(buffer, take(edgeCount * 4), edgeCount);
  const edgeB = new Uint32Array(buffer, take(edgeCount * 4), edgeCount);
  const edgeLen = new Uint32Array(buffer, take(edgeCount * 4), edgeCount);
  const edgeName = new Int32Array(buffer, take(edgeCount * 4), edgeCount);
  const flatOff = new Uint32Array(buffer, take((edgeCount + 1) * 4), edgeCount + 1);
  const edgeCls = new Uint8Array(buffer, take(edgeCount), edgeCount);
  offset = align4(offset);
  const flatI = new Int32Array(buffer, take(flatCount * 2 * 4), flatCount * 2);
  const namesRaw = new Uint8Array(buffer, take(namesBytes), namesBytes);
  let names: string[] = [];
  try {
    const parsed = JSON.parse(new TextDecoder().decode(namesRaw)) as { names?: unknown };
    if (Array.isArray(parsed.names)) names = parsed.names.map(String);
  } catch {
    names = [];
  }
  return { version, vertices, edgeA, edgeB, edgeLen, edgeName, flatOff, edgeCls, flatI, names };
}

export function edgeCoordinates(
  streets: StreetsBinary,
  edgeIndex: number,
): Array<[number, number]> {
  const startNode = streets.edgeA[edgeIndex];
  let x = streets.vertices[startNode * 2];
  let y = streets.vertices[startNode * 2 + 1];
  const start = streets.flatOff[edgeIndex];
  const end = streets.flatOff[edgeIndex + 1];
  const points: Array<[number, number]> = [[x / 1e6, y / 1e6]];
  for (let cursor = start; cursor < end; cursor += 1) {
    x += streets.flatI[cursor * 2];
    y += streets.flatI[cursor * 2 + 1];
    points.push([x / 1e6, y / 1e6]);
  }
  return points;
}
