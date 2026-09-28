import type { NetworkPayload } from "../types";
import { planningPreview, probe, keepNetwork, type PlanningPreview, type ScenarioProbe } from "../simulation/preview";
import { routeWithRaptor, type RaptorJourney } from "./routing";

import {
  EvaluationClient,
  solveDemand,
  type DemandBatch,
  type DemandOutput,
} from "./reference-runtime";

export type EvaluationSummary = {
  lines: number;
  stops: number;
  dailyDepartures: number;
};

export type { PlanningPreview, ScenarioProbe, RaptorJourney };
export { planningPreview, probe, keepNetwork, routeWithRaptor };

export type NetworkCounts = {
  lines: number;
  stops: number;
  dailyDepartures: number;
  fleetEstimate: number;
};

export function networkCounts(network: NetworkPayload): NetworkCounts {
  const preview: PlanningPreview = planningPreview(network);
  return {
    lines: preview.lines,
    stops: preview.stops,
    dailyDepartures: preview.dailyDepartures,
    fleetEstimate: preview.fleetEstimate,
  };
}

export type DemandBatchResult = DemandOutput;

export function solveDemandStrategy(
  batch: DemandBatch,
): Promise<DemandBatchResult> {
  return solveDemand(batch);
}

export function createEvaluationClient(workerCount?: number): EvaluationClient {
  return new EvaluationClient(workerCount);
}

export function disposeComputationWorkers(): void {
  // EvaluationClient owns the actual evaluation worker pool.
}
