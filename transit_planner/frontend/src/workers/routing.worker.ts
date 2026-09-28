/**
 * Thin worker wrapper around the rRAPTOR kernel.
 *
 * All routing logic lives in `routing-kernel.ts` so the assignment worker can
 * import it directly instead of nesting a second worker per OD pair, and so
 * the kernel can be tested in Node with a stubbed `self`.
 */
import { routeAlternatives, routeSingle, type RaptorRequest } from "./routing-kernel";

self.onmessage = (event: MessageEvent<RaptorRequest>) => {
  const input = event.data;
  if (input.type !== "route") return;

  if (input.maxAlternatives <= 1) {
    const single = routeSingle(input);
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
        info: { patternDepartures: single.patternDepartures },
      },
      {
        transfer: [
          single.patterns.buffer,
          single.boards.buffer,
          single.alights.buffer,
          single.departureMin.buffer,
          single.arrivalByLegMin.buffer,
          single.patternDepartures.buffer,
        ],
      },
    );
    return;
  }

  const { passes, alternatives } = routeAlternatives(input);
  self.postMessage({
    type: "alternatives",
    job: input.job,
    passes,
    alternatives: alternatives.map((journey) => ({
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
