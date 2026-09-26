export {};
type Zone={id:string;centroid_x:number;centroid_y:number};
type Request={kind:"matrix";zones:Zone[]};
type Response={kind:"matrix";ids:string[];times:number[]};
self.onmessage=(event:MessageEvent<Request>)=>{
 const {zones}=event.data; const n=zones.length; const ids=zones.map(z=>z.id); const times=new Array<number>(n*n);
 for(let i=0;i<n;i++){const a=zones[i];for(let j=0;j<n;j++){const b=zones[j];const dx=a.centroid_x-b.centroid_x;const dy=a.centroid_y-b.centroid_y;times[i*n+j]=Math.hypot(dx,dy)/4.2;}}
 self.postMessage({kind:"matrix",ids,times} satisfies Response);
};