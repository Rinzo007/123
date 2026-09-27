import type { NetworkPayload } from "../types";
import { planningPreview, probe, keepNetwork, type PlanningPreview, type ScenarioProbe } from "../simulation/preview";
import { routeWithRaptor, type RaptorJourney } from "./routing";

import {
  EvaluationClient,
  solveDemand,
  solveMatrix,
  type DemandBatch,
  type DemandOutput,
  type MatrixInput,
  type MatrixOutput,
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
export type MatrixResult = MatrixOutput;

export function solveDemandStrategy(
  batch: DemandBatch,
): Promise<DemandBatchResult> {
  return solveDemand(batch);
}

export function solveRoadMatrix(
  input: MatrixInput,
): Promise<MatrixResult> {
  return solveMatrix(input);
}

export function createEvaluationClient(workerCount?: number): EvaluationClient {
  return new EvaluationClient(workerCount);
}

export function disposeComputationWorkers(): void {
  // EvaluationClient owns the actual evaluation worker pool.
}
