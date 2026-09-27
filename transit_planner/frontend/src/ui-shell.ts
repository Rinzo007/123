export type AppSection = "network" | "stations" | "routes" | "timetable" | "fleet" | "demand" | "simulation" | "economics" | "analytics";

export interface UiShellState {
  section: AppSection;
  editorTool: "select" | "node" | "track";
  rightPanel: boolean;
  bottomPanel: boolean;
}

export const DEFAULT_UI_STATE: UiShellState = {
  section: "network",
  editorTool: "select",
  rightPanel: true,
  bottomPanel: true,
};

export function setSection(state: UiShellState, section: AppSection): UiShellState {
  return { ...state, section };
}
