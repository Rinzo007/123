export interface CityPackFileManifest {
  bytes: number;
  sha256: string;
}

export interface CityPackManifest {
  city: string;
  version: string;
  sha256: string;
  files: Record<string, CityPackFileManifest>;
  total: number;
}

export interface CityPack {
  manifest: CityPackManifest;
  files: Record<string, ArrayBuffer>;
}

async function sha256(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function concatBytes(parts: Array<{ name: string; data: ArrayBuffer }>): ArrayBuffer {
  const encoder = new TextEncoder();
  const normalized = [...parts].sort((left, right) => left.name.localeCompare(right.name));
  let total = 0;
  const encodedNames = normalized.map((item) => {
    const name = encoder.encode(item.name);
    total += 4 + name.byteLength + item.data.byteLength;
    return { ...item, name, length: item.data.byteLength };
  });
  const output = new Uint8Array(total);
  const view = new DataView(output.buffer);
  let offset = 0;
  for (const item of encodedNames) {
    view.setUint32(offset, item.name.byteLength, true);
    offset += 4;
    output.set(item.name, offset);
    offset += item.name.byteLength;
    output.set(new Uint8Array(item.data), offset);
    offset += item.data.byteLength;
  }
  return output.buffer;
}

export async function createCityPack(
  city: string,
  version: string,
  files: Record<string, ArrayBuffer>,
): Promise<CityPack> {
  const entries = Object.entries(files).map(([name, data]) => ({ name, data }));
  const payload = concatBytes(entries);
  const checksums = await Promise.all(entries.map(async ({ name, data }) => [
    name,
    { bytes: data.byteLength, sha256: await sha256(data) },
  ] as const));
  const manifest: CityPackManifest = {
    city,
    version,
    sha256: await sha256(payload),
    files: Object.fromEntries(checksums),
    total: entries.reduce((sum, { data }) => sum + data.byteLength, 0),
  };
  return {
    manifest,
    files: { ...files },
  };
}

export async function verifyCityPack(pack: CityPack): Promise<boolean> {
  const entries = Object.entries(pack.files);
  const expectedFiles = pack.manifest.files;
  if (entries.length !== Object.keys(expectedFiles).length) return false;

  for (const [name, data] of entries) {
    const expected = expectedFiles[name];
    if (!expected || expected.bytes !== data.byteLength) return false;
    if (expected.sha256 !== await sha256(data)) return false;
  }

  const payload = concatBytes(entries.map(([name, data]) => ({ name, data })));
  return (await sha256(payload)) === pack.manifest.sha256;
}


export type CityPackProgress = {
  file: string;
  index: number;
  total: number;
  bytes: number;
  loadedBytes: number;
};

async function fetchArrayBuffer(url: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(url, { signal, cache: "no-cache" });
  if (!response.ok) throw new Error(`Не удалось загрузить city pack: \${response.status} \${url}`);
  return response.arrayBuffer();
}

export async function loadCityPack(
  baseUrl: string,
  *,
  concurrency = 4,
  signal?: AbortSignal,
  onProgress?: (progress: CityPackProgress) => void,
): Promise<CityPack> {
  const manifestResponse = await fetch(`\${baseUrl.replace(/\/$/, "")}/manifest.json`, {
    signal,
    cache: "no-cache",
  });
  if (!manifestResponse.ok) {
    throw new Error(`Не удалось загрузить manifest city pack: \${manifestResponse.status}`);
  }
  const manifest = await manifestResponse.json() as CityPackManifest;
  const names = Object.keys(manifest.files);
  const files: Record<string, ArrayBuffer> = {};
  let next = 0;
  let loadedBytes = 0;

  async function worker(index: number): Promise<void> {
    while (true) {
      const fileIndex = next++;
      if (fileIndex >= names.length) return;
      const name = names[fileIndex];
      const data = await fetchArrayBuffer(
        `\${baseUrl.replace(/\/$/, "")}/\${encodeURIComponent(name)}`,
        signal,
      );
      const expected = manifest.files[name];
      if (!expected || data.byteLength !== expected.bytes || await sha256(data) !== expected.sha256) {
        throw new Error(`Нарушена целостность city pack: \${name}`);
      }
      files[name] = data;
      loadedBytes += data.byteLength;
      onProgress?.({
        file: name,
        index: fileIndex + 1,
        total: names.length,
        bytes: data.byteLength,
        loadedBytes,
      });
    }
  }

  const count = Math.max(1, Math.min(concurrency, names.length || 1));
  await Promise.all(Array.from({ length: count }, (_, index) => worker(index)));
  const pack = { manifest, files };
  if (!(await verifyCityPack(pack))) throw new Error("Контрольная сумма city pack не совпадает");
  return pack;
}
