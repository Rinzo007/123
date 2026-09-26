import { writable, derived } from "svelte/store";
import type { NetworkPayload, StopDraft, TransitMode } from "../types";

export const stops = writable<StopDraft[]>([]);
export const mode = writable<TransitMode>("bus");
export const routeName = writable("Новый маршрут");
export const headways = writable<Record<string, number>>({ early: 20, am: 10, mid: 12, pm: 10, eve: 20 });
export const networkPayload = writable<NetworkPayload | null>(null);
export const networkDirty = writable(false);
export const stopCount = derived(stops, ($stops) => $stops.length);
