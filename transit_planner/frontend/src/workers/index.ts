import type { NetworkPayload } from "../types";
import { planningPreview, probe, keepNetwork, type PlanningPreview, type ScenarioProbe } from "../simulation/preview";

import {
  ReferenceEvaluationClient,
  solveDemand,
  solveMatrix,
  type ReferenceDemandBatch,
  type ReferenceMatrixInput,
} from "./reference-runtime";

export type EvaluationSummary = {
  lines: number;
  stops: number;
  dailyDepartures: number;
};

export type { PlanningPreview, ScenarioProbe, RaptorJourney };
export { planningPreview, probe, keepNetwork, routeWithRaptor };
export type { RaptorJourney };

export type ClientPreviewResult = {
  evaluation: EvaluationSummary;
  operations: {
    dailyDepartures: number;
    fleetEstimate: number;
  };
};

export type ReferenceDemandBatchResult = Awaited<ReturnType<typeof solveDemand>>;
export type ReferenceMatrixResult = Awaited<ReturnType<typeof solveMatrix>>;

export function solveDemandStrategy(
  batch: ReferenceDemandBatch,
): Promise<ReferenceDemandBatchResult> {
  return solveDemand(batch);
}

export function solveRoadMatrix(
  input: ReferenceMatrixInput,
): Promise<ReferenceMatrixResult> {
  return solveMatrix(input);
}

export function createEvaluationClient(workerCount?: number): ReferenceEvaluationClient {
  return new ReferenceEvaluationClient(workerCount);
}

export function disposeComputationWorkers(): void {
  // ReferenceEvaluationClient owns the actual evaluation worker pool.
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
