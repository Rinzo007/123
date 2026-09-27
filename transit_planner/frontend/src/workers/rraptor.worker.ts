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

type JourneyResult = {
  found: boolean;
  arrivalMin: number;
  generalizedMin: number;
  transfers: number;
  routeIds: Int32Array;
  boardStops: Int32Array;
  alightStops: Int32Array;
  departureMin: Float64Array;
  arrivalByLegMin: Float64Array;
  walkToMin: number;
  walkFromMin: number;
  waitMin: number;
  departureShiftMin: number;
};

const WALK_WEIGHT = 1.39;
const WAIT_WEIGHT = 1.37;
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
  let best = {
    generalizedMin: infinity,
    arrivalMin: infinity,
    transfers: 0,
    round: -1,
    walkToMin: 0,
    walkFromMin: 0,
    waitMin: 0,
    departureShiftMin: 0,
  };

  let bestRouteIds = new Int32Array(0);
  let bestBoardStops = new Int32Array(0);
  let bestAlightStops = new Int32Array(0);
  let bestDepartures = new Float64Array(0);
  let bestArrivals = new Float64Array(0);

  const roundArrival = Array.from(
    { length: input.maxTransfers + 2 },
    () => new Float64Array(input.stopCount).fill(infinity),
  );
  const roundPrevious = Array.from(
    { length: input.maxTransfers + 2 },
    () => new Int32Array(input.stopCount).fill(-1),
  );
  const roundRoute = Array.from(
    { length: input.maxTransfers + 2 },
    () => new Int32Array(input.stopCount).fill(-1),
  );
  const roundBoard = Array.from(
    { length: input.maxTransfers + 2 },
    () => new Int32Array(input.stopCount).fill(-1),
  );
  const roundDeparture = Array.from(
    { length: input.maxTransfers + 2 },
    () => new Float64Array(input.stopCount).fill(infinity),
  );

  const evaluateDeparture = (departureMin: number) => {
    for (const values of roundArrival) values.fill(infinity);
    for (const values of roundPrevious) values.fill(-1);
    for (const values of roundRoute) values.fill(-1);
    for (const values of roundBoard) values.fill(-1);
    for (const values of roundDeparture) values.fill(infinity);

    const originAccess = input.accessTimeMin[input.origin];
    if (!Number.isFinite(originAccess)) return;
    roundArrival[0][input.origin] = departureMin + originAccess;

    for (let round = 0; round <= input.maxTransfers; round += 1) {
      const current = roundArrival[round];
      const next = roundArrival[round + 1];

      for (let route = 0; route < input.routeCount; route += 1) {
        const routeStart = input.routeOffsets[route];
        const routeStopCount = input.routeStopCounts[route];
        if (routeStopCount < 2) continue;

        const departureStart = input.routeDepartureOffsets[route];
        const departureEnd = input.routeDepartureOffsets[route + 1];
        if (departureStart >= departureEnd) continue;

        let boardIndex = -1;
        let boardDepartureIndex = -1;

        for (let local = 0; local < routeStopCount; local += 1) {
          const stop = input.routeStops[routeStart + local];
          const arrival = current[stop];
          if (!Number.isFinite(arrival)) continue;
          const candidate = firstDeparture(
            input.departures,
            departureStart,
            departureEnd,
            arrival,
          );
          if (candidate < 0) continue;
          const candidateDeparture = input.departures[candidate];
          if (
            boardDepartureIndex < 0 ||
            candidateDeparture < input.departures[boardDepartureIndex]
          ) {
            boardIndex = local;
            boardDepartureIndex = candidate;
          }
        }

        if (boardIndex < 0) continue;

        const trainDeparture = input.departures[boardDepartureIndex];
        let currentTime = trainDeparture;
        for (let local = boardIndex; local < routeStopCount; local += 1) {
          if (local > boardIndex) {
            currentTime += input.segmentTimes[
              input.routeSegmentOffsets[route] + local - 1
            ];
          }

          const stop = input.routeStops[routeStart + local];
          if (currentTime >= next[stop]) continue;

          next[stop] = currentTime;
          roundPrevious[round + 1][stop] =
            input.routeStops[routeStart + boardIndex];
          roundRoute[round + 1][stop] = route;
          roundBoard[round + 1][stop] =
            input.routeStops[routeStart + boardIndex];
          roundDeparture[round + 1][stop] = trainDeparture;
        }
      }

      // RAPTOR footpaths: transfer between nearby stops after this round.
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
          roundPrevious[round + 1][to] = from;
          roundRoute[round + 1][to] = -1;
          roundBoard[round + 1][to] = -1;
          roundDeparture[round + 1][to] = infinity;
        }
      }

      const destinationTransit = next[input.destination];
      if (!Number.isFinite(destinationTransit)) continue;
      const walkFrom = input.egressTimeMin[input.destination];
      if (!Number.isFinite(walkFrom)) continue;

      const arrival = destinationTransit + walkFrom;
      let wait = 0;
      let routeLegs = 0;
      let reconstructedRound = round + 1;
      let cursor = input.destination;
      let safety = 0;
      const routes: number[] = [];
      const boards: number[] = [];
      const alights: number[] = [];
      const departures: number[] = [];
      const arrivals: number[] = [];

      while (reconstructedRound > 0 && safety++ < input.maxTransfers * 4 + 8) {
        const routeId = roundRoute[reconstructedRound][cursor];
        const previous = roundPrevious[reconstructedRound][cursor];
        if (previous < 0) break;

        if (routeId >= 0) {
          const board = roundBoard[reconstructedRound][cursor];
          const trainDeparture = roundDeparture[reconstructedRound][cursor];
          routes.push(routeId);
          boards.push(board);
          alights.push(cursor);
          departures.push(trainDeparture);
          arrivals.push(roundArrival[reconstructedRound][cursor]);
          routeLegs += 1;
          wait += Math.max(0, trainDeparture - roundArrival[reconstructedRound - 1][board]);
        }

        cursor = previous;
        reconstructedRound -= 1;
      }

      const transfers = Math.max(0, routeLegs - 1);
      const walkTo = originAccess;
      const shift = Math.max(0, departureMin - input.departureMin);
      const generalized =
        (arrival - departureMin) +
        WAIT_WEIGHT * wait +
        WALK_WEIGHT * (walkTo + walkFrom) +
        SHIFT_WEIGHT * shift +
        TRANSFER_PENALTY_MIN * transfers;

      if (generalized >= best.generalizedMin) continue;

      routes.reverse();
      boards.reverse();
      alights.reverse();
      departures.reverse();
      arrivals.reverse();

      best = {
        generalizedMin: generalized,
        arrivalMin: arrival,
        transfers,
        round: round + 1,
        walkToMin: walkTo,
        walkFromMin: walkFrom,
        waitMin: wait,
        departureShiftMin: shift,
      };
      bestRouteIds = Int32Array.from(routes);
      bestBoardStops = Int32Array.from(boards);
      bestAlightStops = Int32Array.from(alights);
      bestDepartures = Float64Array.from(departures);
      bestArrivals = Float64Array.from(arrivals);
    }
  };

  const windowEnd = input.departureMin + Math.max(0, input.rangeWindowMin);
  for (
    let departure = input.departureMin;
    departure <= windowEnd + 1e-9;
    departure += 1
  ) {
    evaluateDeparture(departure);
  }

  const result: JourneyResult = {
    found: Number.isFinite(best.generalizedMin),
    arrivalMin: best.arrivalMin,
    generalizedMin: best.generalizedMin,
    transfers: best.transfers,
    routeIds: bestRouteIds,
    boardStops: bestBoardStops,
    alightStops: bestAlightStops,
    departureMin: bestDepartures,
    arrivalByLegMin: bestArrivals,
    walkToMin: best.walkToMin,
    walkFromMin: best.walkFromMin,
    waitMin: best.waitMin,
    departureShiftMin: best.departureShiftMin,
  };

  self.postMessage(
    { type: "result", job: input.job, result },
    {
      transfer: [
        result.routeIds.buffer,
        result.boardStops.buffer,
        result.alightStops.buffer,
        result.departureMin.buffer,
        result.arrivalByLegMin.buffer,
      ],
    },
  );
};
