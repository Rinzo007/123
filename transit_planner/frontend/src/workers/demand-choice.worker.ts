export {};
type Request={kind:"choice";trips:number;utilities:{transit:number;car:number;walk:number;bike:number}};
type Response={kind:"choice";shares:{transit:number;car:number;walk:number;bike:number};trips:{transit:number;car:number;walk:number;bike:number}};
self.onmessage=(event:MessageEvent<Request>)=>{
 const {trips,utilities}=event.data; const values=Object.values(utilities).map(v=>Math.exp(Math.max(-50,Math.min(50,v))));
 const sum=values.reduce((a,b)=>a+b,0)||1; const keys=["transit","car","walk","bike"] as const;
 const shares=Object.fromEntries(keys.map((k,i)=>[k,values[i]/sum])) as Response["shares"];
 self.postMessage({kind:"choice",shares,trips:{transit:trips*shares.transit,car:trips*shares.car,walk:trips*shares.walk,bike:trips*shares.bike}} satisfies Response);
};