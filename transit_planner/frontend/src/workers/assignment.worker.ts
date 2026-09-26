import type {NetworkPayload} from "../types";
type Request={kind:"departures";network:NetworkPayload};
type Response={kind:"departures";dailyDepartures:number;fleetEstimate:number};
self.onmessage=(event:MessageEvent<Request>)=>{
 const {network}=event.data; let departures=0; let fleet=0;
 for(const service of network.services) for(const period of network.periods){const h=service.headway_by_period[period.id];if(h>0){const d=Math.ceil((period.end_minute-period.start_minute)/h);departures+=d;fleet=Math.max(fleet,Math.ceil(120/h));}}
 self.postMessage({kind:"departures",dailyDepartures:departures,fleetEstimate:fleet} satisfies Response);
};