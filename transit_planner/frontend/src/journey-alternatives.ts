/**
 * Journey alternative selection: perceived time, Pareto dominance and the
 * diversity loop.
 *
 * Port of `routing.py::shortest_alternatives` (loop and penalty progression),
 * `_perceived_time_minutes` and `pareto_filter_journeys`.
 *
 * The loop takes the router as a callback so it can be compared against the
 * Python reference with a stubbed router: the routers themselves differ by
 * design, but the selection rules must not.
 * `tests/test_journey_alternatives_parity.py` does exactly that.
 */

/** `model.json` → `journey_choice`.walk_weight (Wardman Table 5 mean). */
export const JOURNEY_WALK_WEIGHT = 1.65;
/** `model.json` → `transfer`.base_s, in minutes. */
export const TRANSFER_PENALTY_MIN = 405 / 60;

export interface AlternativeLeg {
  kind: "transit" | "walk";
  routeId?: string | null;
  fromId: string;
  toId: string;
  durationMin: number;
  waitMin?: number;
}

export interface AlternativeJourney {
  routeIds: readonly string[];
  legs?: readonly AlternativeLeg[];
  transfers: number;
  /** Duration in minutes; used as the third Pareto axis. */
  durationMin: number;
  /** Perceived time when legs are supplied, otherwise given explicitly. */
  perceivedTimeMin?: number;
}

export interface PerceivedWeights {
  walk: number;
  wait: number;
  transferPenaltyMin: number;
}

export const DEFAULT_PERCEIVED_WEIGHTS: PerceivedWeights = {
  walk: JOURNEY_WALK_WEIGHT,
  wait: 1.0,
  transferPenaltyMin: TRANSFER_PENALTY_MIN,
};

/**
 * Generalized perceived time without artificial route penalties.
 *
 * Legs are used when present; otherwise the caller must supply
 * `perceivedTimeMin`, which keeps the metric comparable for journeys that came
 * from a worker returning only aggregates.
 */
export function perceivedTimeMinutes(
  journey: AlternativeJourney,
  weights: PerceivedWeights = DEFAULT_PERCEIVED_WEIGHTS,
): number {
  if (!journey.legs) {
    if (journey.perceivedTimeMin === undefined) {
      throw new Error("Journey needs legs or an explicit perceived time");
    }
    return journey.perceivedTimeMin;
  }
  let inVehicle = 0;
  let wait = 0;
  let walk = 0;
  for (const leg of journey.legs) {
    if (leg.kind === "transit") {
      inVehicle += leg.durationMin;
      wait += leg.waitMin ?? 0;
    } else {
      walk += leg.durationMin;
    }
  }
  return inVehicle + weights.walk * walk + weights.wait * wait
    + weights.transferPenaltyMin * journey.transfers;
}

/** True when *left* is at least as good on every axis and strictly better on one. */
export function dominates(
  left: readonly [number, number, number],
  right: readonly [number, number, number],
): boolean {
  const betterOrEqual = left[0] <= right[0] && left[1] <= right[1] && left[2] <= right[2];
  const strictlyBetter = left[0] < right[0] || left[1] < right[1] || left[2] < right[2];
  return betterOrEqual && strictlyBetter;
}

/**
 * Keep only journeys not dominated on (perceived time, transfers, duration).
 *
 * Duration stands in for arrival time because a journey does not store its
 * departure minute; for a fixed origin/destination/period the two orderings
 * coincide. Identical metric tuples do not dominate each other, so exact
 * duplicates are all kept — de-duplication is the diversity loop's job.
 */
export function paretoFilterJourneys<T extends AlternativeJourney>(
  journeys: readonly T[],
  weights: PerceivedWeights = DEFAULT_PERCEIVED_WEIGHTS,
): T[] {
  if (journeys.length <= 1) return [...journeys];
  const metrics = journeys.map(
    (journey) => [perceivedTimeMinutes(journey, weights), journey.transfers, journey.durationMin] as
      readonly [number, number, number],
  );
  return journeys.filter((_, index) => !metrics.some(
    (other, otherIndex) => otherIndex !== index && dominates(other, metrics[index]),
  ));
}

export interface SelectAlternativesInput<T extends AlternativeJourney> {
  maxAlternatives: number;
  diversityPenaltyMin: number;
  /** Base penalties that the diversity increments add on top of. */
  baseRoutePenalties?: ReadonlyMap<string, number>;
  /**
   * Router callback: given the current route penalties and the set of routes
   * already used by an accepted alternative, return the best journey or null.
   */
  route: (
    routePenalties: ReadonlyMap<string, number>,
    bannedRouteIds: ReadonlySet<string>,
  ) => T | null;
  weights?: PerceivedWeights;
}

export interface SelectAlternativesResult<T extends AlternativeJourney> {
  journeys: T[];
  /** Route penalties after the loop, for the next assignment iteration. */
  finalRoutePenalties: Map<string, number>;
  bannedRouteIds: Set<string>;
  /** Passes actually executed: can be below `maxAlternatives` when exhausted. */
  passes: number;
}

/**
 * Iteratively re-route with growing route penalties and banned routes, then
 * keep the Pareto-optimal survivors.
 *
 * The loop stops early when the router runs out of options or repeats a route
 * sequence, so the number of passes is at most `maxAlternatives` but is often
 * smaller.
 */
export function selectAlternatives<T extends AlternativeJourney>(
  input: SelectAlternativesInput<T>,
): SelectAlternativesResult<T> {
  if (input.maxAlternatives <= 0) {
    throw new Error("max_alternatives must be positive");
  }
  if (input.diversityPenaltyMin < 0) {
    throw new Error("diversity_penalty_min cannot be negative");
  }
  const base = input.baseRoutePenalties ?? new Map<string, number>();
  const penalties = new Map(base);
  const journeys: T[] = [];
  const sequences = new Set<string>();
  const banned = new Set<string>();
  let passes = 0;

  for (let rank = 0; rank < input.maxAlternatives; rank += 1) {
    const journey = input.route(penalties, banned);
    passes += 1;
    if (!journey) break;
    const sequence = journey.routeIds.join("|");
    if (sequences.has(sequence)) break;
    sequences.add(sequence);
    for (const routeId of journey.routeIds) banned.add(routeId);
    journeys.push(journey);
    const increment = input.diversityPenaltyMin * (rank + 1);
    for (const routeId of journey.routeIds) {
      penalties.set(
        routeId,
        Math.max(penalties.get(routeId) ?? 0, (base.get(routeId) ?? 0) + increment),
      );
    }
  }

  return {
    journeys: paretoFilterJourneys(journeys, input.weights),
    finalRoutePenalties: penalties,
    bannedRouteIds: banned,
    passes,
  };
}
