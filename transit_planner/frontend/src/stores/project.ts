import { writable } from "svelte/store";
import type { NetworkPayload } from "../types";

export type ProjectState = {
  id: string;
  network: NetworkPayload | null;
  scenarioBase: unknown;
  initialized: boolean;
  dirty: boolean;
};

export const project = writable<ProjectState>({
  id: "current",
  network: null,
  scenarioBase: null,
  initialized: false,
  dirty: false,
});

export function markProjectDirty() {
  project.update((value) => ({ ...value, dirty: true }));
}

export function markProjectClean() {
  project.update((value) => ({ ...value, dirty: false }));
}
