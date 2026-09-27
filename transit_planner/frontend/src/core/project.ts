import type { NetworkPayload, StopDraft, TransitMode } from "../types";

export const PROJECT_FORMAT = "transit-planner-project" as const;
export const PROJECT_VERSION = 2;

export interface ProjectScenario {
  network: NetworkPayload;
  origin: { lon: number; lat: number };
  destination: { lon: number; lat: number };
  trips: number;
  farePerTransitTrip: number;
  annualDays: number;
}

export interface TransitProject {
  format: typeof PROJECT_FORMAT;
  version: number;
  name: string;
  routeName: string;
  mode: TransitMode;
  headways: Record<string, number>;
  stops: StopDraft[];
  network: NetworkPayload;
  previewTrips: number;
  economics: { farePerTransitTrip: number; annualDays: number };
  scenarioBase: ProjectScenario | null;
  metadata: {
    createdAt: string;
    updatedAt: string;
    dataProvider: "overture";
    overtureRelease?: string;
  };
}

export function createProject(input: Omit<TransitProject, "format" | "version" | "metadata">): TransitProject {
  const now = new Date().toISOString();
  return {
    ...input,
    format: PROJECT_FORMAT,
    version: PROJECT_VERSION,
    metadata: {
      createdAt: now,
      updatedAt: now,
      dataProvider: "overture",
    },
  };
}

export function touchProject(project: TransitProject): TransitProject {
  return {
    ...project,
    metadata: { ...project.metadata, updatedAt: new Date().toISOString() },
  };
}
