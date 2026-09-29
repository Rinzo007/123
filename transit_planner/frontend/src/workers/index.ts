import type { NetworkPayload } from "../types";
import { planningPreview, probe, keepNetwork, type PlanningPreview, type ScenarioProbe } from "../simulation/preview";
import { routeWithRaptor, type RaptorJourney } from "./routing";

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

export function disposeComputationWorkers(): void {
  // Reference-оценка и reference-воркер выбора спроса удалены: их движки содержали
  // цикл равновесия и frequency-based insertion, которых нет ни в одном игровом
  // модуле, а исходников для проверки нет. Спрос считает demand-воркер, оценку
  // пассажиропотоков - assignment-воркер; пулы у них собственные.
}
