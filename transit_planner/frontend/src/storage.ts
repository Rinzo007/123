export interface StoredProject {
  id: string;
  data: unknown;
  updatedAt: number;
}

const DB_NAME = "transit-planner";
const DB_VERSION = 1;
const STORE_NAME = "projects";
const SETTINGS_KEY = "transit-planner-ui";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
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
