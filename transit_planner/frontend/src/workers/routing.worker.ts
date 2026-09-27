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
  origin: number;
  destination: number;
  departureMin: number;
};

const WALK_WEIGHT = 1.65;
const WAIT_WEIGHT = 1.72;
const SHIFT_WEIGHT = 0.4;
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

self.onmessage = (event: MessageEvent<RaptorRequest>) => {
  const input = event.data;
  if (input.type !== "route") return;

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

  let bestRouteIds = new Int32Array(0);
  let bestBoardStops = new Int32Array(0);
  let bestAlightStops = new Int32Array(0);
  let bestDepartures = new Float64Array(0);
  let bestArrivals = new Float64Array(0);

  // Carried-forward labels per round (RAPTOR Algorithm 1, stage 1) plus
  // provenance so reconstruction can jump straight to the previous round.
  const arrivalByRound = Array.from(
    { length: rounds },
    () => new Float64Array(stops).fill(infinity),
  );
  const routeByRound = Array.from(
    { length: rounds },
    () => new Int32Array(stops).fill(-1),
  );
  const boardByRound = Array.from(
    { length: rounds },
    () => new Int32Array(stops).fill(-1),
  );
  const departureByRound = Array.from(
    { length: rounds },
    () => new Float64Array(stops).fill(infinity),
  );
  const prevStopByRound = Array.from(
    { length: rounds },
    () => new Int32Array(stops).fill(-1),
  );
  const prevRoundByRound = Array.from(
    { length: rounds },
    () => new Int32Array(stops).fill(-1),
  );
  const labelRound = new Int32Array(stops).fill(-1);

  const reconstruct = (departureMin: number, access: number, walkFrom: number) => {
    const routes: number[] = [];
    const boards: number[] = [];
    const alights: number[] = [];
    const departures: number[] = [];
    const arrivals: number[] = [];
    let cursor = input.destination;
    let r = labelRound[cursor];
    if (r < 0 || !Number.isFinite(arrivalByRound[r][cursor])) return null;
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
        const scheduledDeparture = departureByRound[r][cursor];
        routes.push(routeId);
        boards.push(board);
        alights.push(cursor);
        departures.push(scheduledDeparture);
        arrivals.push(arrivalByRound[r][cursor]);
        wait += Math.max(0, scheduledDeparture - arrivalByRound[fromRound][board]);
      }
      cursor = fromStop;
      r = fromRound;
    }
    if (cursor !== input.origin) return null;

    const transfers = Math.max(0, routes.length - 1);
    const shift = Math.max(0, departureMin - input.departureMin);
    const arrivalWithEgress = finalArrival + walkFrom;
    const generalized =
      (arrivalWithEgress - departureMin) +
      WAIT_WEIGHT * wait +
      WALK_WEIGHT * (access + walkFrom) +
      SHIFT_WEIGHT * shift +
      TRANSFER_PENALTY_MIN * transfers;

    routes.reverse();
    boards.reverse();
    alights.reverse();
    departures.reverse();
    arrivals.reverse();
    return {
      generalized,
      arrivalWithEgress,
      transfers,
      walkTo: access,
      walkFrom,
      wait,
      shift,
      routeIds: Int32Array.from(routes),
      boardStops: Int32Array.from(boards),
      alightStops: Int32Array.from(alights),
      departures: Float64Array.from(departures),
      arrivals: Float64Array.from(arrivals),
    };
  };

  const evaluateDeparture = (departureMin: number) => {
    for (const values of arrivalByRound) values.fill(infinity);
    for (const values of routeByRound) values.fill(-1);
    for (const values of boardByRound) values.fill(-1);
    for (const values of departureByRound) values.fill(infinity);
    for (const values of prevStopByRound) values.fill(-1);
    for (const values of prevRoundByRound) values.fill(-1);
    labelRound.fill(-1);

    const access = input.accessTimeMin[input.origin];
    if (!Number.isFinite(access)) return;
    arrivalByRound[0][input.origin] = departureMin + access;
    labelRound[input.origin] = 0;
    let bestRoundArrival = arrivalByRound[0][input.destination];

    for (let round = 0; round <= input.maxTransfers; round += 1) {
      const current = arrivalByRound[round];
      const next = arrivalByRound[round + 1];

      // Stage 1: carry forward previous-round labels (and provenance).
      next.set(current);
      routeByRound[round + 1].set(routeByRound[round]);
      boardByRound[round + 1].set(boardByRound[round]);
      departureByRound[round + 1].set(departureByRound[round]);
      prevStopByRound[round + 1].set(prevStopByRound[round]);
      prevRoundByRound[round + 1].set(prevRoundByRound[round]);

      // Stage 2: scan each route, boarding the trip that minimizes
      // departureTime - cumulativeRunningTime (paper Section 3).
      for (let route = 0; route < input.routeCount; route += 1) {
        const routeStart = input.routeOffsets[route];
        const routeLength = input.routeStopCounts[route];
        if (routeLength < 2) continue;

        const departureStart = input.routeDepartureOffsets[route];
        const departureEnd = input.routeDepartureOffsets[route + 1];
        const segmentStart = input.routeSegmentOffsets[route];

        let bestKey = infinity;
        let boardDeparture = infinity;
        let boardStop = -1;
        let cumulativeTime = 0;

        for (let local = 0; local < routeLength; local += 1) {
          const stop = input.routeStops[routeStart + local];
          const arrivalAtStop = current[stop];
          if (Number.isFinite(arrivalAtStop)) {
            const departureIndex = firstDeparture(
              input.departures,
              departureStart,
              departureEnd,
              arrivalAtStop,
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
              routeByRound[round + 1][stop] = route;
              boardByRound[round + 1][stop] = boardStop;
              departureByRound[round + 1][stop] = boardDeparture;
              prevStopByRound[round + 1][stop] = boardStop;
              prevRoundByRound[round + 1][stop] = round;
              labelRound[stop] = round + 1;
            }
          }

          if (local < routeLength - 1) {
            cumulativeTime += input.segmentTimes[segmentStart + local];
          }
        }
      }

      // Stage 3: foot-paths. Walking does not increase the trip count.
      for (let from = 0; from < input.stopCount; from += 1) {
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
          labelRound[to] = round + 1;
        }
      }

      if (!Number.isFinite(next[input.destination]) && bestRoundArrival === infinity) {
        continue;
      }
      if (next[input.destination] >= bestRoundArrival) continue;
      bestRoundArrival = next[input.destination];

      const walkFrom = input.egressTimeMin[input.destination];
      if (!Number.isFinite(walkFrom)) continue;
      const journey = reconstruct(departureMin, access, walkFrom);
      if (journey === null || journey.generalized >= bestGeneralized) continue;

      bestGeneralized = journey.generalized;
      bestArrival = journey.arrivalWithEgress;
      bestTransfers = journey.transfers;
      bestWalkTo = journey.walkTo;
      bestWalkFrom = journey.walkFrom;
      bestWait = journey.wait;
      bestShift = journey.shift;
      bestRouteIds = journey.routeIds;
      bestBoardStops = journey.boardStops;
      bestAlightStops = journey.alightStops;
      bestDepartures = journey.departures;
      bestArrivals = journey.arrivals;
    }
  };

  const end = input.departureMin + Math.max(0, input.rangeWindowMin);
  for (let departure = input.departureMin; departure <= end; departure += 1) {
    evaluateDeparture(departure);
  }

  self.postMessage(
    {
      type: "result",
      job: input.job,
      result: {
        found: Number.isFinite(bestGeneralized),
        arrivalMin: bestArrival,
        generalizedMin: bestGeneralized,
        transfers: bestTransfers,
        routeIds: bestRouteIds,
        boardStops: bestBoardStops,
        alightStops: bestAlightStops,
        departureMin: bestDepartures,
        arrivalByLegMin: bestArrivals,
        walkToMin: bestWalkTo,
        walkFromMin: bestWalkFrom,
        waitMin: bestWait,
        departureShiftMin: bestShift,
      },
    },
    {
      transfer: [
        bestRouteIds.buffer,
        bestBoardStops.buffer,
        bestAlightStops.buffer,
        bestDepartures.buffer,
        bestArrivals.buffer,
      ],
    },
  );
};
