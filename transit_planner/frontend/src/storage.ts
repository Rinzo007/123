export interface StoredValue {
  key: string;
  value: unknown;
  updatedAt: number;
  expiresAt?: number;
  encoding?: "identity" | "gzip";
}

export interface StoredProject { id: string; data: unknown; updatedAt: number; }

const DB_NAME = "takt";
const DB_VERSION = 1;
const STORE_NAME = "kv";
const PROJECT_PREFIX = "takt-project-v4:";
const DATASET_PREFIX = "takt-geo-v2:";
const UI_KEY = "takt-ui-v1:";
const WRITER_PREFIX = "takt_network_writer_v1_";
const MAX_COMPRESSED_BYTES = 64 * 1024 * 1024;

type PendingToken = string;

class AtomicWriter {
  private readonly pending = new Map<string, PendingToken>();
  begin(key: string): PendingToken {
    const token = crypto.randomUUID();
    this.pending.set(key, token);
    localStorage.setItem(WRITER_PREFIX + key, token);
    return token;
  }
  confirm(key: string, token: PendingToken): void {
    if (this.pending.get(key) !== token) return;
    this.pending.delete(key);
    localStorage.removeItem(WRITER_PREFIX + key);
  }
  isPending(key: string): boolean {
    return this.pending.has(key) || localStorage.getItem(WRITER_PREFIX + key) !== null;
  }
}

const writer = new AtomicWriter();
const externalListeners = new Set<(key: string) => void>();
window.addEventListener("storage", (event) => {
  if (!event.key || !event.key.startsWith(WRITER_PREFIX) || event.newValue) return;
  const key = event.key.slice(WRITER_PREFIX.length);
  for (const listener of externalListeners) listener(key);
});

export function onExternalWrite(listener: (key: string) => void): () => void {
  externalListeners.add(listener);
  return () => externalListeners.delete(listener);
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME, { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Не удалось открыть IndexedDB"));
  });
}

function readValue(key: string): Promise<StoredValue | undefined> {
  return openDb().then((db) => new Promise((resolve, reject) => {
    if (writer.isPending(key)) { db.close(); resolve(undefined); return; }
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(key);
    request.onsuccess = () => { const value = request.result as StoredValue | undefined; db.close(); resolve(value); };
    request.onerror = () => { db.close(); reject(request.error ?? new Error("Не удалось прочитать IndexedDB")); };
  }));
}

function writeValues(records: StoredValue[]): Promise<void> {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    for (const record of records) store.put(record);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error ?? new Error("Не удалось записать IndexedDB")); };
  }));
}

function deleteValue(key: string): Promise<void> {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(key);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error ?? new Error("Не удалось удалить IndexedDB")); };
  }));
}

async function compress(data: unknown): Promise<ArrayBuffer> {
  const stream = new Blob([JSON.stringify(data)]).stream().pipeThrough(new CompressionStream("gzip"));
  return await new Response(stream).arrayBuffer();
}

function assertSize(value: ArrayBuffer): void {
  if (value.byteLength > MAX_COMPRESSED_BYTES) throw new Error("The game file is too large");
}

async function decompress(record: StoredValue): Promise<unknown> {
  if (record.encoding !== "gzip" || !(record.value instanceof ArrayBuffer)) throw new Error(`Неверная gzip-запись: ${record.key}`);
  const stream = new Blob([record.value]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

async function writeObject(key: string, data: unknown, expiresAt?: number): Promise<void> {
  const token = writer.begin(key);
  try {
    const value = await compress(data);
    assertSize(value);
    await writeValues([{ key, value, updatedAt: Date.now(), expiresAt, encoding: "gzip" }]);
  } finally { writer.confirm(key, token); }
}

async function readObject<T>(key: string): Promise<T | null> {
  const record = await readValue(key);
  if (!record) return null;
  if (record.expiresAt !== undefined && record.expiresAt < Date.now()) { await deleteValue(key); return null; }
  return await decompress(record) as T;
}

export async function saveProject(id: string, data: unknown): Promise<void> { await writeObject(PROJECT_PREFIX + id, data); }
export async function loadProject(id: string): Promise<unknown | null> { return readObject(PROJECT_PREFIX + id); }

export function saveUiSettings(settings: Record<string, unknown>): void {
  localStorage.setItem(UI_KEY, JSON.stringify({ ...settings, savedAt: Date.now() }));
}

export function loadUiSettings<T extends Record<string, unknown>>(defaults: T): T {
  const raw = localStorage.getItem(UI_KEY);
  if (raw === null) return defaults;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(
      `Сохранённые настройки интерфейса повреждены: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Сохранённые настройки интерфейса имеют неверную форму: ожидался JSON-объект");
  }
  return { ...defaults, ...(parsed as Partial<T>) };
}

export interface CachedDataset<T = unknown> { id: string; data: T; updatedAt: number; expiresAt: number; }

export async function saveDatasetBatch(records: ReadonlyArray<{ id: string; data: unknown; ttlMs?: number }>): Promise<void> {
  const now = Date.now();
  const stored: StoredValue[] = [];
  const tokens: Array<[string, PendingToken]> = [];
  try {
    for (const record of records) {
      const key = DATASET_PREFIX + record.id;
      const token = writer.begin(key);
      tokens.push([key, token]);
      const value = await compress(record.data);
      assertSize(value);
      stored.push({ key, value, updatedAt: now, expiresAt: now + (record.ttlMs ?? 7 * 24 * 60 * 60 * 1000), encoding: "gzip" });
    }
    await writeValues(stored);
  } finally { for (const [key, token] of tokens) writer.confirm(key, token); }
}

export async function saveDataset<T>(id: string, data: T, ttlMs = 7 * 24 * 60 * 60 * 1000): Promise<void> { await writeObject(DATASET_PREFIX + id, data, Date.now() + ttlMs); }
export async function loadDataset<T>(id: string): Promise<T | null> { return readObject<T>(DATASET_PREFIX + id); }

export async function saveBinaryDataset(id: string, data: ArrayBuffer, ttlMs = 7 * 24 * 60 * 60 * 1000): Promise<void> {
  assertSize(data);
  const key = DATASET_PREFIX + id;
  const token = writer.begin(key);
  try { await writeValues([{ key, value: data, updatedAt: Date.now(), expiresAt: Date.now() + ttlMs, encoding: "identity" }]); }
  finally { writer.confirm(key, token); }
}

export async function loadBinaryDataset(id: string): Promise<ArrayBuffer | null> {
  const record = await readValue(DATASET_PREFIX + id);
  if (!record) return null;
  if (record.expiresAt !== undefined && record.expiresAt < Date.now()) { await deleteValue(DATASET_PREFIX + id); return null; }
  if (!(record.value instanceof ArrayBuffer)) throw new Error(`Неверный бинарный набор данных: ${id}`);
  return record.value;
}

export async function clearDataset(id: string): Promise<void> {
  const key = DATASET_PREFIX + id;
  const token = writer.begin(key);
  try { await deleteValue(key); }
  finally { writer.confirm(key, token); }
}
