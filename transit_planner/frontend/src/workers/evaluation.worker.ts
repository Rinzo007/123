import type { NetworkPayload } from "../types";

type Request = {
  kind: "summary";
  network: NetworkPayload;
};

type Response = {
  lines: number;
  stops: number;
  dailyDepartures: number;
};

self.onmessage = (event: MessageEvent<Request>) => {
  const { network } = event.data;
  let dailyDepartures = 0;
  for (const service of network.services) {
    for (const period of network.periods) {
      const headway = service.headway_by_period[period.id];
      if (!(headway > 0)) continue;
      dailyDepartures += Math.ceil((period.end_minute - period.start_minute) / headway);
    }
  }
  const response: Response = {
    lines: network.routes.length,
    stops: network.stops.length,
    dailyDepartures,
  };
  self.postMessage(response);
};
