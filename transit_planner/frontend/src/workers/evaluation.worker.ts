export {};
// The browser worker is a thin compatibility adapter. The original reference
// implementation is kept byte-for-byte under public/assets and is the runtime
// source for the reference worker protocol.
type Request =
  | { kind: "summary"; network: { routes: unknown[]; stops: unknown[]; services: Array<{headway_by_period: Record<string, number>}>; periods: Array<{id:string;start_minute:number;end_minute:number}> } };

self.onmessage = (event: MessageEvent<Request>) => {
  if (event.data.kind !== "summary") return;
  const network = event.data.network;
  let dailyDepartures = 0;
  for (const service of network.services) {
    for (const period of network.periods) {
      const headway = service.headway_by_period[period.id];
      if (headway > 0) dailyDepartures += Math.ceil((period.end_minute - period.start_minute) / headway);
    }
  }
  self.postMessage({lines: network.routes.length, stops: network.stops.length, dailyDepartures});
};
