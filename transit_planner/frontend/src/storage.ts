export interface StoredValue {
  key: string;
  value: unknown;
  updatedAt: number;
  expiresAt?: number;
  encoding?: "identity" | "gzip";
}

export interface StoredProject {
  id: string;
  data: unknown;
  updatedAt: number;
}

const DB_NAME = "takt";
const DB_VERSION = 1;
const STORE_NAME = "kv";
const LEGACY_DB_NAME = "transit-planner";
const LEGACY_DB_VERSION = 2;
const LEGACY_STORE_NAME = "projects";
const PROJECT_PREFIX = "takt-project-v4:";
const DATASET_PREFIX = "takt-geo-v2:";
const UI_KEY = "takt-ui-v1:";
const WRITER_PREFIX = "takt_network_writer_v1_";
const MAX_COMPRESSED_BYTES = 64 * 1024 * 1024;
const LEGACY_LOCAL_PREFIXES = [
  "transit-planner-project:",
  "takt-project-v3:",
  "takt-project-v2:",
  "takt-project:",
];

type PendingToken = string;

class AtomicWriter {
  private readonly pending = new Map<string, PendingToken>();

  begin(key: string): PendingToken {
    const token = crypto.randomUUID();
    this.pending.set(key, token);
    try {
      localStorage.setItem(WRITER_PREFIX + key, token);
    } catch {
      // Cross-tab signalling is optional.
    }
    return token;
  }

  confirm(key: string, token: PendingToken): void {
    if (this.pending.get(key) !== token) return;
    this.pending.delete(key);
    try {
      localStorage.removeItem(WRITER_PREFIX + key);
    } catch {
      // Cross-tab signalling is optional.
    }
  }

  isPending(key: string): boolean {
    if (this.pending.has(key)) return true;
    try {
      return localStorage.getItem(WRITER_PREFIX + key) !== null;
    } catch {
      return false;
    }
  }
}

const writer = new AtomicWriter();

const externalListeners = new Set<(key: string) => void>();
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (!event.key || !event.key.startsWith(WRITER_PREFIX)) return;
    const key = event.key.slice(WRITER_PREFIX.length);
    if (!event.newValue) for (const listener of externalListeners) listener(key);
  });
}

export function onExternalWrite(listener: (key: string) => void): () => void {
  externalListeners.add(listener);
  return () => externalListeners.delete(listener);
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Не удалось открыть IndexedDB"));
  });
}

function readValue(key: string): Promise<StoredValue | undefined> {
  return openDb().then((db) => new Promise<StoredValue | undefined>((resolve, reject) => {
    if (writer.isPending(key)) {
      db.close();
      resolve(undefined);
      return;
    }
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(key);
    request.onsuccess = () => {
      const value = request.result as StoredValue | undefined;
      db.close();
      resolve(value);
    };
    request.onerror = () => {
      db.close();
      reject(request.error ?? new Error("Не удалось прочитать IndexedDB"));
    };
  }));
}

function writeValue(record: StoredValue): Promise<void> {
  return openDb().then((db) => new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(record);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Не удалось записать IndexedDB"));
    };
  }));
}

function deleteValue(key: string): Promise<void> {
  return openDb().then((db) => new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(key);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Не удалось удалить IndexedDB"));
    };
  }));
}

async function compress(data: unknown): Promise<{ encoding: "identity" | "gzip"; value: unknown }> {
  if (typeof CompressionStream === "undefined" || typeof Response === "undefined") {
    return { encoding: "identity", value: data };
  }
  try {
    const stream = new Blob([JSON.stringify(data)]).stream().pipeThrough(new CompressionStream("gzip"));
    const bytes = await new Response(stream).arrayBuffer();
    return { encoding: "gzip", value: bytes };
  } catch {
    return { encoding: "identity", value: data };
  }
}

function assertSize(value: unknown): void {
  if (value instanceof ArrayBuffer && value.byteLength > MAX_COMPRESSED_BYTES) {
    throw new Error("The game file is too large");
  }
}

async function decompress(record: StoredValue): Promise<unknown> {
  if (record.encoding !== "gzip") return record.value;
  if (!(record.value instanceof ArrayBuffer)) return record.value;
  if (typeof DecompressionStream === "undefined" || typeof Response === "undefined") return null;
  const stream = new Blob([record.value]).stream().pipeThrough(new DecompressionStream("gzip"));
  const json = await new Response(stream).text();
  return JSON.parse(json);
}

async function writeObject(key: string, data: unknown, expiresAt?: number): Promise<void> {
  const token = writer.begin(key);
  try {
    const compressed = await compress(data);
    assertSize(compressed.value);
    await writeValue({
      key,
      value: compressed.value,
      updatedAt: Date.now(),
      expiresAt,
      encoding: compressed.encoding,
    });
    writer.confirm(key, token);
  } catch (error) {
    writer.confirm(key, token);
    throw error;
  }
}

async function readObject<T>(key: string): Promise<T | null> {
  const record = await readValue(key);
  if (!record) return null;
  if (record.expiresAt !== undefined && record.expiresAt < Date.now()) {
    await deleteValue(key);
    return null;
  }
  return await decompress(record) as T;
}

async function readLegacyLocalProject(id: string): Promise<{ data: unknown; savedAt: number } | null> {
  if (typeof localStorage === "undefined") return null;
  const keys = LEGACY_LOCAL_PREFIXES.map((prefix) => prefix + id);
  keys.push("takt-project-current", "transit-planner-project-current", "transit-planner:current");
  let newest: { data: unknown; savedAt: number } | null = null;
  for (const key of keys) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const parsed = JSON.parse(raw) as { savedAt?: number; updatedAt?: number; data?: unknown };
      const data = parsed && "data" in parsed ? parsed.data : parsed;
      const savedAt = Number(parsed?.savedAt ?? parsed?.updatedAt ?? 0);
      if (!data || !Number.isFinite(savedAt)) continue;
      if (!newest || savedAt > newest.savedAt) newest = { data, savedAt };
    } catch {
      // Ignore malformed legacy records.
    }
  }
  return newest;
}

async function migrateLegacyProject(): Promise<unknown | null> {
  return new Promise((resolve) => {
    try {
      const request = indexedDB.open(LEGACY_DB_NAME, LEGACY_DB_VERSION);
      request.onsuccess = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(LEGACY_STORE_NAME)) {
          db.close();
          resolve(null);
          return;
        }
        const get = db.transaction(LEGACY_STORE_NAME, "readonly")
          .objectStore(LEGACY_STORE_NAME).get("current");
        get.onsuccess = () => {
          const result = get.result as StoredProject | undefined;
          db.close();
          resolve(result?.data ?? null);
        };
        get.onerror = () => {
          db.close();
          resolve(null);
        };
      };
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export async function saveProject(id: string, data: unknown): Promise<void> {
  await writeObject(PROJECT_PREFIX + id, data);
}

export async function loadProject(id: string): Promise<unknown | null> {
  const key = PROJECT_PREFIX + id;
  const currentRecord = await readValue(key);
  const current = currentRecord ? await decompress(currentRecord) : null;
  const legacyIdb = await migrateLegacyProject();
  const legacyLocal = await readLegacyLocalProject(id);

  const candidates = [
    currentRecord && current !== null ? { data: current, savedAt: currentRecord.updatedAt } : null,
    legacyIdb ? { data: legacyIdb, savedAt: 0 } : null,
    legacyLocal,
  ].filter((item): item is { data: unknown; savedAt: number } => Boolean(item));

  if (!candidates.length) return null;
  const newest = candidates.reduce((best, item) => item.savedAt > best.savedAt ? item : best);
  if (!currentRecord || newest.savedAt > currentRecord.updatedAt) {
    await saveProject(id, newest.data);
  }
  return newest.data;
}

export function saveUiSettings(settings: Record<string, unknown>): void {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify({ ...settings, savedAt: Date.now() }));
  } catch {
    // localStorage may be unavailable.
  }
}

export function loadUiSettings<T extends Record<string, unknown>>(fallback: T): T {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<T> & { savedAt?: number };
    return { ...fallback, ...(parsed as Partial<T>) };
  } catch {
    return fallback;
  }
}

export interface CachedDataset<T = unknown> {
  id: string;
  data: T;
  updatedAt: number;
  expiresAt: number;
}

export async function saveDatasetBatch(
  records: ReadonlyArray<{ id: string; data: unknown; ttlMs?: number }>,
): Promise<void> {
  const now = Date.now();
  const stored: StoredValue[] = [];
  const tokens: Array<[string, PendingToken]> = [];
  try {
    for (const record of records) {
      const key = DATASET_PREFIX + record.id;
      const token = writer.begin(key);
      tokens.push([key, token]);
      const compressed = await compress(record.data);
      assertSize(compressed.value);
      stored.push({
        key,
        value: compressed.value,
        updatedAt: now,
        expiresAt: now + (record.ttlMs ?? 7 * 24 * 60 * 60 * 1000),
        encoding: compressed.encoding,
      });
    }
    await writeValues(stored);
  } finally {
    for (const [key, token] of tokens) writer.confirm(key, token);
  }
}

export async function saveDataset<T>(
  id: string,
  data: T,
  ttlMs = 7 * 24 * 60 * 60 * 1000,
): Promise<void> {
  await writeObject(DATASET_PREFIX + id, data, Date.now() + ttlMs);
}

export async function loadDataset<T>(id: string): Promise<T | null> {
  return readObject<T>(DATASET_PREFIX + id);
}

export async function saveBinaryDataset(
  id: string,
  data: ArrayBuffer,
  ttlMs = 7 * 24 * 60 * 60 * 1000,
): Promise<void> {
  const key = DATASET_PREFIX + id;
  const token = writer.begin(key);
  try {
    await writeValue({
      key,
      value: data,
      updatedAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
      encoding: "identity",
    });
    writer.confirm(key, token);
  } catch (error) {
    writer.confirm(key, token);
    throw error;
  }
}

export async function loadBinaryDataset(id: string): Promise<ArrayBuffer | null> {
  const record = await readValue(DATASET_PREFIX + id);
  if (!record) return null;
  if (record.expiresAt !== undefined && record.expiresAt < Date.now()) {
    await deleteValue(DATASET_PREFIX + id);
    return null;
  }
  return record.value instanceof ArrayBuffer ? record.value : null;
}

export async function clearDataset(id: string): Promise<void> {
  const key = DATASET_PREFIX + id;
  const token = writer.begin(key);
  try {
    await deleteValue(key);
    writer.confirm(key, token);
  } catch (error) {
    writer.confirm(key, token);
    throw error;
  }
}
