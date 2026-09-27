import type { NetworkPayload } from "../types";
import { planningPreview, probe, keepNetwork, type PlanningPreview, type ScenarioProbe } from "../simulation/preview";
import { routeWithRaptor, type RaptorJourney } from "./routing";

import {
  EvaluationClient,
  solveDemand,
  solveMatrix,
  type DemandBatch,
  type MatrixInput,
} from "./reference-runtime";

export type EvaluationSummary = {
  lines: number;
  stops: number;
  dailyDepartures: number;
};

export type { PlanningPreview, ScenarioProbe, RaptorJourney };
export { planningPreview, probe, keepNetwork, routeWithRaptor };

export type ClientPreviewResult = {
  evaluation: EvaluationSummary;
  operations: {
    dailyDepartures: number;
    fleetEstimate: number;
  };
};

export type DemandBatchResult = Awaited<ReturnType<typeof solveDemand>>;
export type ReferenceMatrixResult = Awaited<ReturnType<typeof solveMatrix>>;

export function solveDemandStrategy(
  batch: DemandBatch,
): Promise<DemandBatchResult> {
  return solveDemand(batch);
}

export function solveRoadMatrix(
  input: MatrixInput,
): Promise<ReferenceMatrixResult> {
  return solveMatrix(input);
}

export function createEvaluationClient(workerCount?: number): EvaluationClient {
  return new EvaluationClient(workerCount);
}

export function disposeComputationWorkers(): void {
  // EvaluationClient owns the actual evaluation worker pool.
}

export function runClientPreview(network: NetworkPayload): Promise<ClientPreviewResult> {
  const preview: PlanningPreview = planningPreview(network);
  return Promise.resolve({
    evaluation: {
      lines: preview.lines,
      stops: preview.stops,
      dailyDepartures: preview.dailyDepartures,
    },
    operations: {
      dailyDepartures: preview.dailyDepartures,
      fleetEstimate: preview.fleetEstimate,
    },
  });
}
