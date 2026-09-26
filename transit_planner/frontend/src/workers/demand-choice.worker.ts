import { calculateProbabilities, calculateUtilities, type ChoiceUtilities } from "./reference-model";

export {};

type Request = {
  kind: "choice";
  trips: number;
  utilities?: ChoiceUtilities;
  inputs?: Parameters<typeof calculateUtilities>[0];
  carAvailability?: number;
  bikeAvailability?: number;
  noCarShare?: number;
};

type Response = {
  kind: "choice";
  shares: { transit: number; car: number; walk: number; bike: number; rest: number };
  trips: { transit: number; car: number; walk: number; bike: number; rest: number };
};

self.onmessage = (event: MessageEvent<Request>) => {
  const { trips } = event.data;
  const values = event.data.inputs
    ? calculateUtilities(event.data.inputs)
    : (event.data.utilities ?? { transit: Number.NEGATIVE_INFINITY, car: Number.NEGATIVE_INFINITY, walk: Number.NEGATIVE_INFINITY, bike: Number.NEGATIVE_INFINITY });
  const probabilities = calculateProbabilities(values, {
    carAvailability: event.data.carAvailability,
    bikeAvailability: event.data.bikeAvailability,
    noCarShare: event.data.noCarShare,
  });
  const result: Response = {
    kind: "choice",
    shares: probabilities,
    trips: {
      transit: trips * probabilities.transit,
      car: trips * probabilities.car,
      walk: trips * probabilities.walk,
      bike: trips * probabilities.bike,
      rest: trips * probabilities.rest,
    },
  };
  self.postMessage(result);
};
