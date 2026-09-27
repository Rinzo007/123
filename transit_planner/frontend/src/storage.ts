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
  const current = await readObject<unknown>(key);
  if (current !== null) return current;
  const legacy = await migrateLegacyProject();
  if (legacy !== null) {
    await saveProject(id, legacy);
    return legacy;
  }
  return null;
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
