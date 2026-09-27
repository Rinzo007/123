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

    const access = input.accessTimeMin[input.origin];
    if (!Number.isFinite(access)) return;
    roundArrival[0][input.origin] = departureMin + access;

    for (let round = 0; round <= input.maxTransfers; round += 1) {
      const current = roundArrival[round];
      const next = roundArrival[round + 1];

      for (let route = 0; route < input.routeCount; route += 1) {
        const routeStart = input.routeOffsets[route];
        const routeLength = input.routeStopCounts[route];
        if (routeLength < 2) continue;

        const departureStart = input.routeDepartureOffsets[route];
        const departureEnd = input.routeDepartureOffsets[route + 1];
        let boardIndex = -1;
        let boardDeparture = infinity;

        for (let local = 0; local < routeLength; local += 1) {
          const stop = input.routeStops[routeStart + local];
          const arrivalAtStop = current[stop];
          if (!Number.isFinite(arrivalAtStop)) continue;
          const departureIndex = firstDeparture(
            input.departures,
            departureStart,
            departureEnd,
            arrivalAtStop,
          );
          if (departureIndex < 0) continue;
          const candidate = input.departures[departureIndex];
          if (candidate < boardDeparture) {
            boardDeparture = candidate;
            boardIndex = local;
          }
        }

        if (boardIndex < 0) continue;

        let arrival = boardDeparture;
        for (let local = boardIndex; local < routeLength; local += 1) {
          if (local > boardIndex) {
            arrival += input.segmentTimes[
              input.routeSegmentOffsets[route] + local - 1
            ];
          }
          const stop = input.routeStops[routeStart + local];
          if (arrival >= next[stop]) continue;
          next[stop] = arrival;
          roundPrevious[round + 1][stop] = input.routeStops[routeStart + boardIndex];
          roundRoute[round + 1][stop] = route;
          roundBoard[round + 1][stop] = input.routeStops[routeStart + boardIndex];
          roundDeparture[round + 1][stop] = boardDeparture;
        }
      }

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

      const transitArrival = next[input.destination];
      const walkFrom = input.egressTimeMin[input.destination];
      if (!Number.isFinite(transitArrival) || !Number.isFinite(walkFrom)) continue;

      const arrivalWithEgress = transitArrival + walkFrom;
      const routes: number[] = [];
      const boards: number[] = [];
      const alights: number[] = [];
      const departures: number[] = [];
      const arrivals: number[] = [];
      let cursor = input.destination;
      let cursorRound = round + 1;
      let routeLegs = 0;
      let wait = 0;
      let guard = 0;

      while (cursorRound > 0 && guard++ < input.maxTransfers * 4 + 8) {
        const previous = roundPrevious[cursorRound][cursor];
        if (previous < 0) break;
        const routeId = roundRoute[cursorRound][cursor];
        if (routeId >= 0) {
          const board = roundBoard[cursorRound][cursor];
          const scheduledDeparture = roundDeparture[cursorRound][cursor];
          routes.push(routeId);
          boards.push(board);
          alights.push(cursor);
          departures.push(scheduledDeparture);
          arrivals.push(roundArrival[cursorRound][cursor]);
          routeLegs += 1;
          const previousArrival = roundArrival[cursorRound - 1][board];
          wait += Math.max(0, scheduledDeparture - previousArrival);
        }
        cursor = previous;
        cursorRound -= 1;
      }

      const transfers = Math.max(0, routeLegs - 1);
      const walkTo = access;
      const shift = Math.max(0, departureMin - input.departureMin);
      const generalized =
        (arrivalWithEgress - departureMin) +
        WAIT_WEIGHT * wait +
        WALK_WEIGHT * (walkTo + walkFrom) +
        SHIFT_WEIGHT * shift +
        TRANSFER_PENALTY_MIN * transfers;

      if (generalized >= bestGeneralized) continue;

      routes.reverse();
      boards.reverse();
      alights.reverse();
      departures.reverse();
      arrivals.reverse();
      bestGeneralized = generalized;
      bestArrival = arrivalWithEgress;
      bestTransfers = transfers;
      bestWalkTo = walkTo;
      bestWalkFrom = walkFrom;
      bestWait = wait;
      bestShift = shift;
      bestRouteIds = Int32Array.from(routes);
      bestBoardStops = Int32Array.from(boards);
      bestAlightStops = Int32Array.from(alights);
      bestDepartures = Float64Array.from(departures);
      bestArrivals = Float64Array.from(arrivals);
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
