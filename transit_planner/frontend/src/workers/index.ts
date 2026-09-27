import type { NetworkPayload } from "../types";
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
  const dailyDepartures = network.services.reduce((total, service) => {
    return total + Object.entries(service.headway_by_period).reduce((sum, [periodId, headway]) => {
      const period = network.periods.find((item) => item.id === periodId);
      if (!period || headway <= 0) return sum;
      return sum + Math.ceil((period.end_minute - period.start_minute) / headway);
    }, 0);
  }, 0);
  const fleetEstimate = network.services.reduce((fleet, service) => {
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route) return fleet;
    const activeHeadways = Object.entries(service.headway_by_period)
      .map(([periodId, headway]) => ({ period: network.periods.find((item) => item.id === periodId), headway }))
      .filter((item) => item.period && item.headway > 0);
    if (!activeHeadways.length) return fleet;
    const cycleMinutes = activeHeadways.reduce(
      (max, item) => Math.max(max, route.stop_ids.length * 2 + 4),
      0,
    );
    return fleet + Math.max(1, Math.ceil(cycleMinutes / Math.min(...activeHeadways.map((item) => item.headway))));
  }, 0);

  return Promise.resolve({
    evaluation: {
      lines: network.routes.length,
      stops: network.stops.length,
      dailyDepartures,
    },
    operations: { dailyDepartures, fleetEstimate },
  });
}
