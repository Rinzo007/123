import { REFERENCE } from "./reference-model";

export {};

type Zone = { id: string; centroid_x: number; centroid_y: number };
type Request = { kind: "matrix"; zones: Zone[]; speedKph?: number };
type Response = {
  kind: "matrix";
  ids: string[];
  distancesM: number[];
  timesMin: number[];
  times: number[];
  referenceSpeedKph: number;
};

self.onmessage = (event: MessageEvent<Request>) => {
  const { zones } = event.data;
  const speedKph = event.data.speedKph && event.data.speedKph > 0
    ? event.data.speedKph
    : REFERENCE.referenceSpeedKph;
  const n = zones.length;
  const ids = zones.map((zone) => zone.id);
  const distancesM = new Array<number>(n * n);
  const timesMin = new Array<number>(n * n);
  for (let i = 0; i < n; i += 1) {
    const a = zones[i];
    for (let j = 0; j < n; j += 1) {
      const b = zones[j];
      const distance = Math.hypot(a.centroid_x - b.centroid_x, a.centroid_y - b.centroid_y);
      const index = i * n + j;
      distancesM[index] = distance;
      timesMin[index] = distance / 1000 / speedKph * 60;
    }
  }
  self.postMessage({
    kind: "matrix",
    ids,
    distancesM,
    timesMin,
    times: timesMin,
    referenceSpeedKph: speedKph,
  } satisfies Response);
};
