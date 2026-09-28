/**
 * rRAPTOR kernel with crowding/penalty support and alternative selection.
 *
 * One `routeOnce` call is a full RAPTOR sweep for a given penalty state; the
 * handler then repeats it with growing route penalties and banned patterns,
 * exactly like `routing.py::shortest_alternatives`, and keeps the
 * Pareto-optimal survivors.
 *
 * All penalties arrive already expanded per pattern or per segment: the worker
 * stays a pure kernel, the caller owns the (route, from, to) key mapping. That
 * also lets a two-way route be banned in both directions, which a per-pattern
 * penalty loop cannot express on its own.
 *
 * Reported in-vehicle times are the *true* segment times, never the inflated
 * ones: crowding must steer the choice without corrupting the utility.
 */
import { paretoFilterJourneys, type AlternativeJourney } from "../journey-alternatives";

type RaptorRequest = {
  type: "route";
  job: number;
  stopCount: number;
  routeCount: number;
  maxTransfers: number;
  rangeWindowMin: number;
  accessTimeMin: Float64Array;
  egressTimeMin: Float64Array;
  transferOffsets: Int32Array;
  transferTargets: Int32Array;
  transferTimes: Float64Array;
  routeOffsets: Int32Array;
  routeStopCounts: Int32Array;
  routeStops: Int32Array;
  routeDepartureOffsets: Int32Array;
  departures: Float64Array;
  routeSegmentOffsets: Int32Array;
  segmentTimes: Float64Array;
  departureMin: number;
  walkWeight: number;
  waitWeight: number;
  shiftWeight: number;
  /** Extra generalized cost per pattern; 0 when unused. */
  routePenalties: Float64Array;
  /** 1 marks a pattern the caller has ruled out. */
  bannedRoutes: Uint8Array;
  /** Extra in-vehicle minutes per segment; 0 when unused. */
  segmentPenalties: Float64Array;
  /** Alternatives to look for; 1 keeps the historical single-journey shape. */
  maxAlternatives: number;
  diversityPenaltyMin: number;
};

interface RaptorRide {
  pattern: number;
  boardStop: number;
  alightStop: number;
  boardLocal: number;
  alightLocal: number;
  inVehicleMin: number;
  waitMin: number;
}

interface SingleJourney {
  found: boolean;
  arrivalMin: number;
  generalizedMin: number;
  transfers: number;
  rides: RaptorRide[];
  patterns: Int32Array;
  boards: Int32Array;
  alights: Int32Array;
  accessStop: number;
  egressStop: number;
  departureMin: Float64Array;
  arrivalByLegMin: Float64Array;
  walkToMin: number;
  walkFromMin: number;
  waitMin: number;
  departureShiftMin: number;
}

interface AlternativeResult extends AlternativeJourney {
  patternIds: number[];
  arrivalMin: number;
  generalizedMin: number;
  inVehicleMin: number;
  walkToMin: number;
  walkFromMin: number;
  transfers: number;
  durationMin: number;
  waitMin: number;
  shiftMin: number;
  rides: RaptorRide[];
}

const TRANSFER_PENALTY_MIN = 405 / 60;

function firstDeparture(
  departures: Float64Array,
  start: number,
  end: number,
  arrival: number,
): number {
  let left = start;
  let right = end;
  while (left < right) {
    const middle = (left + right) >> 1;
    if (departures[middle] < arrival) left = middle + 1;
    else right = middle;
  }
  return left < end ? left : -1;
}

/**
 * One full sweep: every departure in the range, best journey by generalized
 * cost. Penalties are applied here and nowhere else.
 */
function routeOnce(
  input: RaptorRequest,
  routePenalties: Float64Array,
  bannedRoutes: Uint8Array,
  segmentPenalties: Float64Array,
): SingleJourney {
  const infinity = Number.POSITIVE_INFINITY;
  const rounds = input.maxTransfers + 2;
  const stops = input.stopCount;

  let bestGeneralized = infinity;
  let bestArrival = infinity;
  let bestTransfers = 0;
  let bestWalkTo = 0;
  let bestWalkFrom = 0;
  let bestWait = 0;
  let bestShift = 0;
  let bestRides: RaptorRide[] = [];
  let bestPatterns = new Int32Array(0);
  let bestBoards = new Int32Array(0);
  let bestAlights = new Int32Array(0);
  let bestAccessStop = -1;
  let bestEgressStop = -1;
  let bestDepartures = new Float64Array(0);
  let bestArrivals = new Float64Array(0);

  // Carried-forward labels per round (RAPTOR Algorithm 1, stage 1) plus
  // provenance so reconstruction can jump straight to the previous round.
  const arrivalByRound = Array.from({ length: rounds }, () => new Float64Array(stops).fill(infinity));
  const routeByRound = Array.from({ length: rounds }, () => new Int32Array(stops).fill(-1));
  const boardByRound = Array.from({ length: rounds }, () => new Int32Array(stops).fill(-1));
  const departureByRound = Array.from({ length: rounds }, () => new Float64Array(stops).fill(infinity));
  const prevStopByRound = Array.from({ length: rounds }, () => new Int32Array(stops).fill(-1));
  const prevRoundByRound = Array.from({ length: rounds }, () => new Int32Array(stops).fill(-1));
  const accessSourceByRound = Array.from({ length: rounds }, () => new Int32Array(stops).fill(-1));
  const labelRound = new Int32Array(stops).fill(-1);

  const reconstruct = (departureMin: number, target: number, targetRound: number) => {
    const patterns: number[] = [];
    const boards: number[] = [];
    const alights: number[] = [];
    const departures: number[] = [];
    const arrivals: number[] = [];
    let cursor = target;
    let r = targetRound;
    if (r < 0 || r >= rounds || !Number.isFinite(arrivalByRound[r][cursor])) return null;
    const finalArrival = arrivalByRound[r][cursor];
    let wait = 0;
    let guard = 0;

    while (r > 0 && guard++ < rounds * 2) {
      const routeId = routeByRound[r][cursor];
      const fromStop = prevStopByRound[r][cursor];
      const fromRound = prevRoundByRound[r][cursor];
      if (fromStop < 0 || fromRound < 0) return null;
      if (routeId >= 0) {
        const board = boardByRound[r][cursor];
        const scheduled = departureByRound[r][cursor];
        patterns.push(routeId);
        boards.push(board);
        alights.push(cursor);
        departures.push(scheduled);
        arrivals.push(arrivalByRound[r][cursor]);
        wait += Math.max(0, scheduled - arrivalByRound[fromRound][board]);
      }
      cursor = fromStop;
      r = fromRound;
    }
    if (r !== 0) return null;
    // Цепочка без транзитных ног (чистая ходьба от доступа) здесь не
    // считается найденным маршрутом: планировщик транзита такие результаты
    // помечает not-found (см. journey.ts).
    if (patterns.length === 0) return null;
    const accessStop = accessSourceByRound[0][cursor];
    const access = accessStop >= 0 ? input.accessTimeMin[accessStop] : Number.POSITIVE_INFINITY;
    const walkFrom = input.egressTimeMin[target];
    if (cursor !== accessStop || !Number.isFinite(access) || !Number.isFinite(walkFrom)) {
      return null;
    }

    const transfers = Math.max(0, patterns.length - 1);
    const shift = Math.max(0, departureMin - input.departureMin);
    const arrivalWithEgress = finalArrival + walkFrom;
    let generalized = (arrivalWithEgress - departureMin)
      + input.waitWeight * wait
      + input.walkWeight * (access + walkFrom)
      + input.shiftWeight * shift
      + TRANSFER_PENALTY_MIN * transfers;

    patterns.reverse();
    boards.reverse();
    alights.reverse();
    departures.reverse();
    arrivals.reverse();

    // Штрафы маршрутов начисляются на выбранную связку, а не на весь путь.
    for (const pattern of patterns) {
      generalized += Math.max(0, routePenalties[pattern] ?? 0);
    }

    // Ноги собираются из настоящих времён сегментов: раздутые штрафом переполненности
    // времена влияют на выбор, но не должны попадать в полезность поездки.
    const rides: RaptorRide[] = [];
    let inVehicle = 0;
    for (let i = 0; i < patterns.length; i += 1) {
      const pattern = patterns[i];
      const start = input.routeOffsets[pattern];
      const count = input.routeStopCounts[pattern];
      const segmentStart = input.routeSegmentOffsets[pattern];
      const boardLocal = indexOfStop(input.routeStops, start, count, boards[i]);
      const alightLocal = indexOfStop(input.routeStops, start, count, alights[i]);
      if (boardLocal < 0 || alightLocal < 0 || alightLocal <= boardLocal) return null;
      let legMinutes = 0;
      for (let local = boardLocal; local < alightLocal; local += 1) {
        legMinutes += input.segmentTimes[segmentStart + local];
      }
      inVehicle += legMinutes;
      rides.push({
        pattern,
        boardStop: boards[i],
        alightStop: alights[i],
        boardLocal,
        alightLocal,
        inVehicleMin: legMinutes,
        waitMin: Math.max(0, departures[i] - arrivals[i - 1 < 0 ? 0 : i - 1]),
      });
    }

    return {
      generalized,
      arrivalWithEgress,
      transfers,
      accessStop,
      egressStop: target,
      walkTo: access,
      walkFrom,
      wait,
      shift,
      patterns: Int32Array.from(patterns),
      boards: Int32Array.from(boards),
      alights: Int32Array.from(alights),
      departures: Float64Array.from(departures),
      arrivals: Float64Array.from(arrivals),
      rides,
      inVehicle,
    };
  };

  const evaluateDeparture = (departureMin: number) => {
    for (const values of arrivalByRound) values.fill(infinity);
    for (const values of routeByRound) values.fill(-1);
    for (const values of boardByRound) values.fill(-1);
    for (const values of departureByRound) values.fill(infinity);
    for (const values of prevStopByRound) values.fill(-1);
    for (const values of prevRoundByRound) values.fill(-1);
    for (const values of accessSourceByRound) values.fill(-1);
    labelRound.fill(-1);

    let hasAccess = false;
    for (let stop = 0; stop < stops; stop += 1) {
      const access = input.accessTimeMin[stop];
      if (!Number.isFinite(access)) continue;
      arrivalByRound[0][stop] = departureMin + access;
      accessSourceByRound[0][stop] = stop;
      labelRound[stop] = 0;
      hasAccess = true;
    }
    if (!hasAccess) return;
    let bestArrivalTotal = infinity;

    for (let round = 0; round <= input.maxTransfers; round += 1) {
      const current = arrivalByRound[round];
      const next = arrivalByRound[round + 1];

      // Stage 1: carry forward labels (and provenance).
      next.set(current);
      routeByRound[round + 1].set(routeByRound[round]);
      boardByRound[round + 1].set(boardByRound[round]);
      departureByRound[round + 1].set(departureByRound[round]);
      prevStopByRound[round + 1].set(prevStopByRound[round]);
      prevRoundByRound[round + 1].set(prevRoundByRound[round]);
      accessSourceByRound[round + 1].set(accessSourceByRound[round]);

      // Stage 2: scan each pattern, boarding the trip that minimizes
      // departureTime - cumulativeRunningTime (paper Section 3).
      for (let pattern = 0; pattern < input.routeCount; pattern += 1) {
        if (bannedRoutes[pattern]) continue;
        const routeStart = input.routeOffsets[pattern];
        const routeLength = input.routeStopCounts[pattern];
        if (routeLength < 2) continue;

        const departureStart = input.routeDepartureOffsets[pattern];
        const departureEnd = input.routeDepartureOffsets[pattern + 1];
        const segmentStart = input.routeSegmentOffsets[pattern];

        let bestKey = infinity;
        let boardDeparture = infinity;
        let boardStop = -1;
        let cumulativeTime = 0;

        for (let local = 0; local < routeLength; local += 1) {
          const stop = input.routeStops[routeStart + local];
          const arrivalAtStop = current[stop];
          if (Number.isFinite(arrivalAtStop)) {
            const departureIndex = firstDeparture(
              input.departures, departureStart, departureEnd, arrivalAtStop,
            );
            if (departureIndex >= 0) {
              const scheduled = input.departures[departureIndex];
              const key = scheduled - cumulativeTime;
              if (key < bestKey) {
                bestKey = key;
                boardDeparture = scheduled;
                boardStop = stop;
              }
            }
          }

          if (boardStop >= 0) {
            const candidate = bestKey + cumulativeTime;
            if (candidate < next[stop]) {
              next[stop] = candidate;
              routeByRound[round + 1][stop] = pattern;
              boardByRound[round + 1][stop] = boardStop;
              departureByRound[round + 1][stop] = boardDeparture;
              prevStopByRound[round + 1][stop] = boardStop;
              prevRoundByRound[round + 1][stop] = round;
              accessSourceByRound[round + 1][stop] = accessSourceByRound[round][boardStop];
              labelRound[stop] = round + 1;
            }
          }

          if (local < routeLength - 1) {
            cumulativeTime += input.segmentTimes[segmentStart + local]
              + Math.max(0, segmentPenalties[segmentStart + local] ?? 0);
          }
        }
      }

      // Stage 3: foot-paths. Walking does not increase the trip count.
      for (let from = 0; from < stops; from += 1) {
        const fromArrival = next[from];
        if (!Number.isFinite(fromArrival)) continue;
        for (
          let edge = input.transferOffsets[from];
          edge < input.transferOffsets[from + 1];
          edge += 1
        ) {
          const to = input.transferTargets[edge];
          const candidate = fromArrival + input.transferTimes[edge];
          if (candidate >= next[to]) continue;
          next[to] = candidate;
          routeByRound[round + 1][to] = -1;
          boardByRound[round + 1][to] = -1;
          departureByRound[round + 1][to] = infinity;
          prevStopByRound[round + 1][to] = from;
          prevRoundByRound[round + 1][to] = labelRound[from];
          accessSourceByRound[round + 1][to] = accessSourceByRound[round][from];
          labelRound[to] = round + 1;
        }
      }

      for (let target = 0; target < stops; target += 1) {
        const egress = input.egressTimeMin[target];
        if (!Number.isFinite(egress)) continue;
        const arrival = next[target];
        if (!Number.isFinite(arrival)) continue;
        const total = arrival + egress;
        if (total >= bestArrivalTotal) continue;
        const journey = reconstruct(departureMin, target, round + 1);
        if (journey === null || journey.generalized >= bestGeneralized) continue;

        bestArrivalTotal = total;
        bestGeneralized = journey.generalized;
        bestArrival = journey.arrivalWithEgress;
        bestTransfers = journey.transfers;
        bestAccessStop = journey.accessStop;
        bestEgressStop = journey.egressStop;
        bestWalkTo = journey.walkTo;
        bestWalkFrom = journey.walkFrom;
        bestWait = journey.wait;
        bestShift = journey.shift;
        bestRides = journey.rides;
        bestPatterns = journey.patterns;
        bestBoards = journey.boards;
        bestAlights = journey.alights;
        bestDepartures = journey.departures;
        bestArrivals = journey.arrivals;
      }
    }
  };

  const end = input.departureMin + Math.max(0, input.rangeWindowMin);
  for (let departure = input.departureMin; departure <= end; departure += 1) {
    evaluateDeparture(departure);
  }

  return {
    found: Number.isFinite(bestGeneralized),
    arrivalMin: bestArrival,
    generalizedMin: bestGeneralized,
    transfers: bestTransfers,
    rides: bestRides,
    patterns: bestPatterns,
    boards: bestBoards,
    alights: bestAlights,
    accessStop: bestAccessStop,
    egressStop: bestEgressStop,
    departureMin: bestDepartures,
    arrivalByLegMin: bestArrivals,
    walkToMin: bestWalkTo,
    walkFromMin: bestWalkFrom,
    waitMin: bestWait,
    departureShiftMin: bestShift,
  };
}

function indexOfStop(stops: Int32Array, start: number, count: number, target: number): number {
  for (let i = 0; i < count; i += 1) {
    if (stops[start + i] === target) return i;
  }
  return -1;
}

self.onmessage = (event: MessageEvent<RaptorRequest>) => {
  const input = event.data;
  if (input.type !== "route") return;

  if (input.maxAlternatives <= 1) {
    const single = routeOnce(input, input.routePenalties, input.bannedRoutes, input.segmentPenalties);
    const patternDepartures = new Int32Array(input.routeCount);
    for (let pattern = 0; pattern < input.routeCount; pattern += 1) {
      patternDepartures[pattern] =
        input.routeDepartureOffsets[pattern + 1] - input.routeDepartureOffsets[pattern];
    }
    self.postMessage(
      {
        type: "result",
        job: input.job,
        result: {
          found: single.found,
          arrivalMin: single.arrivalMin,
          generalizedMin: single.generalizedMin,
          transfers: single.transfers,
          routeIds: single.patterns,
          boardStops: single.boards,
          alightStops: single.alights,
          accessStop: single.accessStop,
          egressStop: single.egressStop,
          departureMin: single.departureMin,
          arrivalByLegMin: single.arrivalByLegMin,
          walkToMin: single.walkToMin,
          walkFromMin: single.walkFromMin,
          waitMin: single.waitMin,
          departureShiftMin: single.departureShiftMin,
        },
        info: { patternDepartures },
      },
      {
        transfer: [
          single.patterns.buffer,
          single.boards.buffer,
          single.alights.buffer,
          single.departureMin.buffer,
          single.arrivalByLegMin.buffer,
          patternDepartures.buffer,
        ],
      },
    );
    return;
  }

  // Diversity loop: re-route with growing penalties on the patterns already
  // used, then keep the Pareto-optimal survivors.
  const penalties = Float64Array.from(input.routePenalties);
  const basePenalties = Float64Array.from(input.routePenalties);
  const banned = Uint8Array.from(input.bannedRoutes);
  const collected: AlternativeResult[] = [];
  const seen = new Set<string>();
  const weights = { walk: input.walkWeight, wait: input.waitWeight, transferPenaltyMin: TRANSFER_PENALTY_MIN };

  for (let rank = 0; rank < input.maxAlternatives; rank += 1) {
    const single = routeOnce(input, penalties, banned, input.segmentPenalties);
    if (!single.found) break;
    const patterns = single.rides.map((ride) => ride.pattern);
    const sequence = patterns.join("|");
    if (seen.has(sequence)) break;
    seen.add(sequence);

    const legs = [
      { kind: "walk" as const, fromId: "", toId: "", durationMin: single.walkToMin, waitMin: 0 },
      ...single.rides.map((ride) => ({
        kind: "transit" as const,
        routeId: String(ride.pattern),
        fromId: String(ride.boardStop),
        toId: String(ride.alightStop),
        durationMin: ride.inVehicleMin,
        waitMin: ride.waitMin,
      })),
      { kind: "walk" as const, fromId: "", toId: "", durationMin: single.walkFromMin, waitMin: 0 },
    ];
    const inVehicleMin = single.rides.reduce((sum, ride) => sum + ride.inVehicleMin, 0);
    collected.push({
      routeIds: patterns.map(String),
      legs,
      transfers: single.transfers,
      durationMin: single.arrivalMin - input.departureMin,
      perceivedTimeMin: inVehicleMin
        + weights.walk * (single.walkToMin + single.walkFromMin)
        + weights.wait * single.waitMin
        + weights.transferPenaltyMin * single.transfers,
      patternIds: patterns,
      arrivalMin: single.arrivalMin,
      generalizedMin: single.generalizedMin,
      inVehicleMin,
      walkToMin: single.walkToMin,
      walkFromMin: single.walkFromMin,
      waitMin: single.waitMin,
      shiftMin: single.departureShiftMin,
      rides: single.rides,
    });

    const increment = input.diversityPenaltyMin * (rank + 1);
    for (const pattern of patterns) {
      const next = Math.max(
        penalties[pattern] ?? 0,
        (basePenalties[pattern] ?? 0) + increment,
      );
      penalties[pattern] = next;
      banned[pattern] = 1;
    }
  }

  const kept = paretoFilterJourneys(collected, weights);
  self.postMessage({
    type: "alternatives",
    job: input.job,
    passes: collected.length,
    alternatives: kept.map((journey) => ({
      patternIds: journey.patternIds,
      transfers: journey.transfers,
      durationMin: journey.durationMin,
      arrivalMin: journey.arrivalMin,
      generalizedMin: journey.generalizedMin,
      inVehicleMin: journey.inVehicleMin,
      waitMin: journey.waitMin,
      walkToMin: journey.walkToMin,
      walkFromMin: journey.walkFromMin,
      shiftMin: journey.shiftMin,
      rides: journey.rides,
    })),
  });
};
