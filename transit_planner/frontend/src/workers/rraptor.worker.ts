type RaptorRequest = {
  type: "route";
  job: number;
  stopCount: number;
  routeCount: number;
  maxTransfers: number;
  rangeWindowMin: number;
  accessTimeMin: Float64Array;
  egressTimeMin: Float64Array;
  transferTimeMin: Float64Array;
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
const TRANSFER_PENALTY_MIN = 6.75;

function bestDeparture(departures: Float64Array, start: number, end: number, at: number): number {
  let left = start;
  let right = end;
  while (left < right) {
    const middle = (left + right) >> 1;
    if (departures[middle] < at) left = middle + 1;
    else right = middle;
  }
  return left < end ? departures[left] : Number.POSITIVE_INFINITY;
}

self.onmessage = (event: MessageEvent<RaptorRequest>) => {
  const input = event.data;
  if (input.type !== "route") return;

  const { stopCount, routeCount } = input;
  const inf = Number.POSITIVE_INFINITY;
  let bestGeneralized = inf;
  let bestArrival = inf;
  let bestTransfers = 0;
  let bestWalkTo = 0;
  let bestWalkFrom = 0;
  let bestWait = 0;
  let bestShift = 0;
  let bestRoutes = new Int32Array(0);
  let bestBoards = new Int32Array(0);
  let bestAlights = new Int32Array(0);
  let bestDepartures = new Float64Array(0);
  let bestLegArrivals = new Float64Array(0);

  const roundArrival = Array.from(
    { length: input.maxTransfers + 1 },
    () => new Float64Array(stopCount).fill(inf),
  );
  const roundPrev = Array.from(
    { length: input.maxTransfers + 1 },
    () => new Int32Array(stopCount).fill(-1),
  );
  const roundRoute = Array.from(
    { length: input.maxTransfers + 1 },
    () => new Int32Array(stopCount).fill(-1),
  );

  const access = input.accessTimeMin;
  const egress = input.egressTimeMin;

  for (
    let departure = input.departureMin;
    departure <= input.departureMin + input.rangeWindowMin + 1e-9;
    departure += Math.max(1, input.rangeWindowMin / 6)
  ) {
    for (const values of roundArrival) values.fill(inf);
    for (const values of roundPrev) values.fill(-1);
    for (const values of roundRoute) values.fill(-1);

    for (let stop = 0; stop < stopCount; stop += 1) {
      if (Number.isFinite(access[stop])) roundArrival[0][stop] = departure + access[stop];
    }

    let marked = new Uint8Array(stopCount);
    marked.fill(1);

    for (let round = 0; round <= input.maxTransfers; round += 1) {
      if (!marked[input.destination] && roundArrival[round][input.destination] < inf) {
        marked[input.destination] = 1;
      }

      const nextMarked = new Uint8Array(stopCount);
      let changed = true;
      while (changed) {
        changed = false;

        for (let route = 0; route < routeCount; route += 1) {
          const routeStart = input.routeOffsets[route];
          const routeEnd = input.routeOffsets[route + 1];
          const stopCountOnRoute = input.routeStopCounts[route];
          if (stopCountOnRoute < 2) continue;

          let boardIndex = -1;
          let boardAt = inf;

          for (let localStop = 0; localStop < stopCountOnRoute; localStop += 1) {
            const stop = input.routeStops[routeStart + localStop];
            const arrival = roundArrival[round][stop];
            if (arrival >= inf) continue;

            const depStart = input.routeDepartureOffsets[route];
            const depEnd = input.routeDepartureOffsets[route + 1];
            const departureIndex = bestDeparture(input.departures, depStart, depEnd, arrival);
            if (departureIndex >= depEnd) continue;

            const trainDeparture = input.departures[departureIndex];
            if (trainDeparture < boardAt) {
              boardIndex = localStop;
              boardAt = trainDeparture;
            }
          }

          if (boardIndex < 0) continue;

          let currentTime = boardAt;
          for (let localStop = boardIndex; localStop < stopCountOnRoute; localStop += 1) {
            const stop = input.routeStops[routeStart + localStop];
            if (localStop > boardIndex) {
              currentTime += input.segmentTimes[input.routeSegmentOffsets[route] + localStop - 1];
            }
            if (currentTime < roundArrival[round][stop]) continue;
            if (currentTime < roundArrival[round + 1]?.[stop] ?? false) {
              // Deliberately empty; roundArrival[round + 1] is maintained below.
            }
            if (round < input.maxTransfers) {
              if (currentTime < roundArrival[round + 1][stop]) {
                roundArrival[round + 1][stop] = currentTime;
                roundPrev[round + 1][stop] = input.routeStops[routeStart + boardIndex];
                roundRoute[round + 1][stop] = route;
                nextMarked[stop] = 1;
                changed = true;
              }
            }
          }
        }

        if (!changed) break;
        marked = nextMarked;
      }

      if (round + 1 > input.maxTransfers) continue;
      for (let stop = 0; stop < stopCount; stop += 1) {
        if (!nextMarked[stop]) continue;
        for (let other = 0; other < stopCount; other += 1) {
          if (other === stop || !Number.isFinite(input.transferTimeMin[stop * stopCount + other])) continue;
          const candidate = roundArrival[round + 1][stop] + input.transferTimeMin[stop * stopCount + other];
          if (candidate < roundArrival[round + 1][other]) {
            roundArrival[round + 1][other] = candidate;
            roundPrev[round + 1][other] = stop;
            roundRoute[round + 1][other] = -1;
          }
        }
      }

      if (Number.isFinite(roundArrival[round + 1]?.[input.destination] ?? inf)) {
        const transitArrival = roundArrival[round + 1][input.destination];
        const walkFrom = egress[input.destination];
        const totalArrival = transitArrival + (Number.isFinite(walkFrom) ? walkFrom : 0);
        const wait = Math.max(0, transitArrival - departure - (totalArrival - transitArrival));
        const transfers = round;
        const shift = Math.max(0, departure - input.departureMin);
        const generalized =
          (totalArrival - departure) +
          WAIT_WEIGHT * wait +
          WALK_WEIGHT * (access[input.origin] + (Number.isFinite(walkFrom) ? walkFrom : 0)) +
          SHIFT_WEIGHT * shift +
          transfers * TRANSFER_PENALTY_MIN;

        if (generalized < bestGeneralized) {
          bestGeneralized = generalized;
          bestArrival = totalArrival;
          bestTransfers = transfers;
          bestWalkTo = access[input.origin];
          bestWalkFrom = Number.isFinite(walkFrom) ? walkFrom : 0;
          bestWait = wait;
          bestShift = shift;
          const routes: number[] = [];
          const boards: number[] = [];
          const alights: number[] = [];
          const departuresLeg: number[] = [];
          const arrivalsLeg: number[] = [];
          let stop = input.destination;
          let r = round + 1;

          while (r >= 0 && stop >= 0) {
            const routeId = roundRoute[r][stop];
            const previous = roundPrev[r][stop];
            if (routeId >= 0 && previous >= 0) {
              routes.push(routeId);
              boards.push(previous);
              alights.push(stop);
              departuresLeg.push(roundArrival[Math.max(0, r - 1)][previous]);
              arrivalsLeg.push(roundArrival[r][stop]);
              stop = previous;
            } else if (previous >= 0) {
              stop = previous;
            } else {
              break;
            }
            r -= 1;
          }

          routes.reverse();
          boards.reverse();
          alights.reverse();
          departuresLeg.reverse();
          arrivalsLeg.reverse();
          bestRoutes = Int32Array.from(routes);
          bestBoards = Int32Array.from(boards);
          bestAlights = Int32Array.from(alights);
          bestDepartures = Float64Array.from(departuresLeg);
          bestLegArrivals = Float64Array.from(arrivalsLeg);
        }
      }
    }
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
        routeIds: bestRoutes,
        boardStops: bestBoards,
        alightStops: bestAlights,
        departureMin: bestDepartures,
        arrivalByLegMin: bestLegArrivals,
        walkToMin: bestWalkTo,
        walkFromMin: bestWalkFrom,
        waitMin: bestWait,
        departureShiftMin: bestShift,
      } satisfies JourneyResult,
    },
    {
      transfer: [
        bestRoutes.buffer,
        bestBoards.buffer,
        bestAlights.buffer,
        bestDepartures.buffer,
        bestLegArrivals.buffer,
      ],
    },
  );
};
