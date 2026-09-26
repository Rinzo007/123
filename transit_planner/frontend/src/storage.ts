export interface StoredProject {
  id: string;
  data: unknown;
  updatedAt: number;
}

const DB_NAME = "transit-planner";
const DB_VERSION = 2;
const STORE_NAME = "projects";
const DATASET_STORE_NAME = "datasets";
const SETTINGS_KEY = "transit-planner-ui";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(DATASET_STORE_NAME)) {
        db.createObjectStore(DATASET_STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Не удалось открыть IndexedDB"));
  });
}

export async function saveProject(id: string, data: unknown): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put({ id, data, updatedAt: Date.now() } satisfies StoredProject);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("Не удалось сохранить проект"));
  });
  db.close();
}

export async function loadProject(id: string): Promise<unknown | null> {
  const db = await openDb();
  const value = await new Promise<StoredProject | undefined>((resolve, reject) => {
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(id);
    request.onsuccess = () => resolve(request.result as StoredProject | undefined);
    request.onerror = () => reject(request.error ?? new Error("Не удалось прочитать проект"));
  });
  db.close();
  return value?.data ?? null;
}

export function saveUiSettings(settings: Record<string, unknown>): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // localStorage may be unavailable in private/sandboxed contexts.
  }
}

export function loadUiSettings<T extends Record<string, unknown>>(fallback: T): T {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
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

export async function saveDataset<T>(id: string, data: T, ttlMs = 7 * 24 * 60 * 60 * 1000): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(DATASET_STORE_NAME, "readwrite");
    tx.objectStore(DATASET_STORE_NAME).put({
      id,
      data,
      updatedAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
    } satisfies CachedDataset<T>);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("Не удалось сохранить набор данных"));
  });
  db.close();
}

export async function loadDataset<T>(id: string): Promise<T | null> {
  const db = await openDb();
  const value = await new Promise<CachedDataset<T> | undefined>((resolve, reject) => {
    const request = db.transaction(DATASET_STORE_NAME, "readonly").objectStore(DATASET_STORE_NAME).get(id);
    request.onsuccess = () => resolve(request.result as CachedDataset<T> | undefined);
    request.onerror = () => reject(request.error ?? new Error("Не удалось прочитать набор данных"));
  });
  db.close();
  if (!value || value.expiresAt < Date.now()) return null;
  return value.data;
}

export async function clearDataset(id: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(DATASET_STORE_NAME, "readwrite");
    tx.objectStore(DATASET_STORE_NAME).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("Не удалось удалить набор данных"));
  });
  db.close();
}
