export interface CityPackManifest {
  city: string;
  version: string;
  sha256: string;
  files: Record<string, number>;
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
  const manifest: CityPackManifest = {
    city,
    version,
    sha256: await sha256(payload),
    files: Object.fromEntries(entries.map(({ name, data }) => [name, data.byteLength])),
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
    if (expectedFiles[name] !== data.byteLength) return false;
  }

  const payload = concatBytes(entries.map(([name, data]) => ({ name, data })));
  return (await sha256(payload)) === pack.manifest.sha256;
}
