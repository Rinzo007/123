#!/usr/bin/env node
"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),os=require("node:os");
const {Worker:NodeWorker}=require("node:worker_threads");

const MATRIX_WORKER_SOURCE=`
const {parentPort}=require("node:worker_threads");
let graph=null;
function solve(msg){
  const {job,start,end,stops,offsets,targets,costs,targetOffsets,targetNodes}=msg;
  const nodes=stops*2;
  const targetMark=new Uint32Array(nodes);
  const times=new Float64Array((end-start)*stops);
  times.fill(Infinity);
  const previous=new Int32Array((end-start)*nodes);
  previous.fill(-1);
  const dist=new Float64Array(nodes);
  const used=new Uint8Array(nodes);
  let heapNode=new Int32Array(Math.max(1024,nodes));
  let heapDist=new Float64Array(heapNode.length);
  for(let src=start;src<end;src++){
    const targetFrom=targetOffsets?targetOffsets[src]:0;
    const targetTo=targetOffsets?targetOffsets[src+1]:0;
    const targetCount=targetTo-targetFrom;
    if(targetOffsets && targetCount===0)continue;
    for(let k=targetFrom;k<targetTo;k++)targetMark[targetNodes[k]]=src+1;
    let remaining=targetCount;
    dist.fill(Infinity);used.fill(0);dist[src]=0;
    let size=1;
    heapNode[0]=src;heapDist[0]=0;
    function push(node,d){
      if(size>=heapNode.length){
        const nn=new Int32Array(heapNode.length*2),dd=new Float64Array(heapDist.length*2);
        nn.set(heapNode);dd.set(heapDist);heapNode=nn;heapDist=dd;
      }
      let k=size++;
      heapNode[k]=node;heapDist[k]=d;
      while(k>0){
        const p=(k-1)>>1;
        if(heapDist[p]<=heapDist[k])break;
        const tn=heapNode[p],td=heapDist[p];
        heapNode[p]=heapNode[k];heapDist[p]=heapDist[k];
        heapNode[k]=tn;heapDist[k]=td;k=p;
      }
    }
    function pop(){
      const node=heapNode[0],d=heapDist[0],last=--size;
      if(last>0){
        heapNode[0]=heapNode[last];heapDist[0]=heapDist[last];
        let k=0;
        for(;;){
          const l=2*k+1,r=l+1;let m=k;
          if(l<size&&heapDist[l]<heapDist[m])m=l;
          if(r<size&&heapDist[r]<heapDist[m])m=r;
          if(m===k)break;
          const tn=heapNode[k],td=heapDist[k];
          heapNode[k]=heapNode[m];heapDist[k]=heapDist[m];
          heapNode[m]=tn;heapDist[m]=td;k=m;
        }
      }
      return [node,d];
    }
    while(size){
      const [node,cost]=pop();
      if(used[node])continue;
      used[node]=1;
      if(node>=stops){
        times[(src-start)*stops+(node-stops)]=cost;
        if(targetMark[node]===src+1){
          remaining--;
          if(remaining===0)break;
        }
      }
      for(let k=offsets[node],finish=offsets[node+1];k<finish;k++){
        const to=targets[k];
        if(used[to])continue;
        const next=cost+costs[k];
        if(next<dist[to]){
          dist[to]=next;
          previous[(src-start)*nodes+to]=node;
          push(to,next);
        }
      }
    }
  }
  parentPort.postMessage({type:"solved",job,start,times,previous});
}
parentPort.on("message",msg=>{
 if(msg.type==="init"){
   graph={
     offsets:msg.offsets instanceof SharedArrayBuffer?new Int32Array(msg.offsets):msg.offsets,
     targets:msg.targets instanceof SharedArrayBuffer?new Int32Array(msg.targets):msg.targets,
     costs:msg.costs instanceof SharedArrayBuffer?new Float64Array(msg.costs):msg.costs,
     targetOffsets:msg.targetOffsets instanceof SharedArrayBuffer?new Int32Array(msg.targetOffsets):msg.targetOffsets,
     targetNodes:msg.targetNodes instanceof SharedArrayBuffer?new Int32Array(msg.targetNodes):msg.targetNodes,
   };
 }else if(msg.type==="solve")solve({...msg,...graph});
});
`


function compressMatrixGraph(offsets0,targets0,costs0,targetCSR){
 const I=targetCSR.stops;
 if(I<1200)return{offsets:offsets0,targets:targets0,costs:costs0};
 const lineOfStop=targetCSR.lineOfStop;
 const important=new Uint8Array(I);
 const sourceImportant=new Uint8Array(I);
 for(let k=0;k<targetCSR.nodes.length;k++)important[targetCSR.nodes[k]-I]=1;
 for(let src=0;src<I;src++){
   if(targetCSR.offsets[src+1]>targetCSR.offsets[src])sourceImportant[src]=1;
 }
 for(let stop=0;stop<I;stop++){
   const node=I+stop;
   for(let k=offsets0[node];k<offsets0[node+1];k++){
     if(targets0[k]<I){important[stop]=1;sourceImportant[stop]=1;break;}
   }
 }
 const outOffsets=new Int32Array(I*2+1);
 const targetParts=[],costParts=[];let total=0;
 for(let node=0;node<I*2;node++){
   outOffsets[node]=total;
   const from=offsets0[node],to=offsets0[node+1];
   if(node<I){
     if(!sourceImportant[node])continue;
     const srcLine=lineOfStop[node];
     for(let k=from;k<to;k++){
       const target=targets0[k];
       if(target<I){
         targetParts.push(target);costParts.push(costs0[k]);total++;
         continue;
       }
       const dst=target-I;
       if(lineOfStop[dst]!==srcLine||!important[dst])continue;
       targetParts.push(target);costParts.push(costs0[k]);total++;
     }
   }else{
     for(let k=from;k<to;k++){
       const target=targets0[k];
       if(target<I){targetParts.push(target);costParts.push(costs0[k]);total++;}
     }
   }
 }
 outOffsets[I*2]=total;
 const targets=new Int32Array(total),costs=new Float64Array(total);
 for(let i=0;i<total;i++){targets[i]=targetParts[i];costs[i]=costParts[i];}
 return{offsets:outOffsets,targets,costs};
}
function matrixGraphKey(offsets,targets,costs){
  const sample=(arr)=>{
    let h=2166136261>>>0,step=Math.max(1,Math.floor(arr.length/32));
    for(let i=0,n=0;i<arr.length&&n<32;i+=step,n++){
      const v=typeof arr[i]==="number"?arr[i]:0;
      h^=Number.isInteger(v)?(v>>>0):Math.floor(v*1000)>>>0;
      h=Math.imul(h,16777619);
    }
    return h>>>0;
  };
  return offsets.length+":"+targets.length+":"+costs.length+":"+sample(offsets)+":"+sample(targets)+":"+sample(costs);
}
const MODE_ACCESS_M={bus:500,tram:600,metro:800,rail:1500};
function buildMatrixTargetCSR(q){
 const lines=q.lines||[],pts=q.city?.pts||[];
 const lineRefs=[],lineOfStop=[];
 let I=0;
 for(let li=0;li<lines.length;li++){
   const line=lines[li],stops=line.stops||[],rr=[];
   if(stops.length<2){lineRefs.push(rr);continue;}
   for(let si=0;si<stops.length;si++){
     if(line.openStops&&line.openStops[si]===false){rr.push(-1);continue;}
     rr.push(I);lineOfStop[I]=li;I++;
   }
   lineRefs.push(rr);
 }
 const zoneCache=new Map();
 const stopGrid=new Map();
 const GRID_LON=0.01,GRID_LAT=0.0062,GRID_RADIUS=3;
 for(let li=0;li<lines.length;li++){
   const line=lines[li],stops=line.stops||[],rr=lineRefs[li];
   if(stops.length<2)continue;
   for(let si=0;si<stops.length;si++){
     const g=rr[si];if(g<0)continue;
     const p=stops[si],gx=Math.floor(Number(p[0])/GRID_LON),gy=Math.floor(Number(p[1])/GRID_LAT);
     const key=gx+":"+gy;
     let bucket=stopGrid.get(key);if(!bucket)stopGrid.set(key,bucket=[]);
     bucket.push([li,si,g]);
   }
 }
 function candidates(zi){
   if(zoneCache.has(zi))return zoneCache.get(zi);
   const point=pts[zi],out=[];
   if(!point){zoneCache.set(zi,out);return out;}
   const gx=Math.floor(Number(point[0])/GRID_LON),gy=Math.floor(Number(point[1])/GRID_LAT);
   const bestByLine=new Map();
   for(let dx=-GRID_RADIUS;dx<=GRID_RADIUS;dx++)for(let dy=-GRID_RADIUS;dy<=GRID_RADIUS;dy++){
     const bucket=stopGrid.get((gx+dx)+":"+(gy+dy));
     if(!bucket)continue;
     for(const [li,si,g] of bucket){
       const line=lines[li];
       const access=MODE_ACCESS_M[String(line.mode||"bus").toLowerCase()]??1500;
       const d=hav(point,line.stops[si]);
       if(d>access+1e-9)continue;
       const prev=bestByLine.get(li);
       if(!prev||d<prev.d||(d===prev.d&&g<prev.g))bestByLine.set(li,{g,d});
     }
   }
   for(const [li,best] of bestByLine)out.push(best.g);
   out.sort((a,b)=>a-b);
   zoneCache.set(zi,out);return out;
 }
 const rows=[];
 for(const row of (q.city?.od||[]))rows.push(row);
 for(const layer of (q.layers||[]))for(const row of layer.od||[])rows.push(row);
 const targetSets=Array.from({length:I},()=>null);
 for(const row of rows){
   const srcs=candidates(Number(row[0])),dsts=candidates(Number(row[1]));
   if(!srcs.length||!dsts.length)continue;
   for(const src of srcs){
     let set=targetSets[src];if(!set)targetSets[src]=set=new Set();
     for(const dst of dsts)set.add(I+dst);
   }
 }
 const offsets=new Int32Array(I+1);
 let total=0;
 for(let i=0;i<I;i++){offsets[i]=total;if(targetSets[i])total+=targetSets[i].size;}
 offsets[I]=total;
 const nodes=new Int32Array(total);let at=0;
 for(let i=0;i<I;i++)if(targetSets[i])for(const node of targetSets[i])nodes[at++]=node;
 return{offsets,nodes,stops:I,lineOfStop:Int32Array.from(lineOfStop)};
}

const MATRIX_WORKERS=new Set();
class TaktNodeWorker{
  constructor(){
    this.worker=new NodeWorker(MATRIX_WORKER_SOURCE,{eval:true});
    MATRIX_WORKERS.add(this);
    this.onmessage=null;this.onerror=null;this.graphSource=null;
    this.worker.on("message",data=>{if(this.onmessage)this.onmessage({data});});
    this.worker.on("error",err=>{if(this.onerror)this.onerror(err);});
  }
  postMessage(msg){
    if(msg.type==="solve" && msg.offsets){
      if(this.graphSource!==msg.offsets){
        this.graphSource=msg.offsets;
        const targetCSR=buildMatrixTargetCSR(MATRIX_CURRENT_CITY||{});
        const compressed=compressMatrixGraph(msg.offsets,msg.targets,msg.costs,targetCSR);
        const targetOffsets=new SharedArrayBuffer(targetCSR.offsets.byteLength);
        const targetNodes=new SharedArrayBuffer(targetCSR.nodes.byteLength);
        new Int32Array(targetOffsets).set(targetCSR.offsets);
        new Int32Array(targetNodes).set(targetCSR.nodes);
        const offsetsOut=new SharedArrayBuffer(compressed.offsets.byteLength);
        const targetsOut=new SharedArrayBuffer(compressed.targets.byteLength);
        const costsOut=new SharedArrayBuffer(compressed.costs.byteLength);
        new Int32Array(offsetsOut).set(compressed.offsets);
        new Int32Array(targetsOut).set(compressed.targets);
        new Float64Array(costsOut).set(compressed.costs);
        this.worker.postMessage({type:"init",offsets:offsetsOut,targets:targetsOut,costs:costsOut,targetOffsets,targetNodes});
      }
      this.worker.postMessage({type:"solve",job:msg.job,start:msg.start,end:msg.end,stops:msg.stops});
      return;
    }
    this.worker.postMessage(msg);
  }
  terminate(){
    MATRIX_WORKERS.delete(this);
    return this.worker.terminate();
  }
}
async function terminateMatrixWorkers(){
  const workers=Array.from(MATRIX_WORKERS);
  MATRIX_WORKERS.clear();
  await Promise.all(workers.map(w=>w.worker.terminate()));
}
let MATRIX_CURRENT_CITY=null;
const ROOT=path.resolve(__dirname,".."),DEFAULT_MANIFEST=path.join(ROOT,"tests/fixtures/takt_release_city_cases.json");
const resolveFrom= (base,p) => {
  if(typeof p!=="string" || !p) throw Error("manifest path must be a non-empty string");
  return path.isAbsolute(p) ? path.resolve(p) : path.resolve(base,p);
};
const load=p=>JSON.parse(fs.readFileSync(p,"utf8"));
const decodeF32=s=>{const b=Buffer.from(s,"base64");const v=new Float32Array(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));return Array.from(v)};
const hav=(a,b)=>{const r=6371e3,z=Math.PI/180,dl=(b[1]-a[1])*z,dn=(b[0]-a[0])*z,la=a[1]*z,lb=b[1]*z,q=Math.sin(dl/2)**2+Math.cos(la)*Math.cos(lb)*Math.sin(dn/2)**2;return 2*r*Math.asin(Math.sqrt(q))};
function getBs(){
 const bundlePath=process.env.TAKT_BUNDLE_PATH?path.resolve(process.env.TAKT_BUNDLE_PATH):path.join(ROOT,"scripts/bd956ff0a1875604740f.js");
 const b=fs.readFileSync(bundlePath,"utf8"),m="})();",i=b.lastIndexOf(m);if(i<0)throw Error("Takt bundle terminator not found");
 const s={console,performance,setTimeout,clearTimeout,setInterval,clearInterval,TextEncoder,TextDecoder,URL,URLSearchParams,
 Uint8Array,Uint16Array,Uint32Array,Int32Array,Float32Array,Float64Array,DataView,ArrayBuffer,SharedArrayBuffer,BigInt64Array,BigUint64Array,Math,Date,JSON,
 Map,Set,WeakMap,WeakSet,Promise,Error,TypeError,RangeError,Symbol,Reflect,Object,Array,Number,String,Boolean,RegExp,parseInt,parseFloat,isFinite,isNaN,Worker:TaktNodeWorker,navigator:{hardwareConcurrency:6},
 atob:globalThis.atob,btoa:globalThis.btoa};s.globalThis=s;s.location={hostname:"localhost",href:"http://localhost/"};s.self={location:s.location,addEventListener(){},postMessage(){}};
 vm.runInNewContext(b.slice(0,i)+"\nglobalThis.__TAKT_Bs=Bs;\n"+b.slice(i),s,{filename:bundlePath,displayErrors:true});
 if(typeof s.__TAKT_Bs!=="function")throw Error("Takt Bs() not exported"); return s.__TAKT_Bs;
}
function coarseCell(point){
 const lon=Number(point[0]),lat=Number(point[1]);
 return Math.floor(lon/0.01)+":"+Math.floor(lat/0.0062);
}
function build(c,manifestDir){
 const d=load(resolveFrom(manifestDir,c.demand)),m=load(resolveFrom(manifestDir,c.model)),
   b=load(resolveFrom(manifestDir,c.baseline)),p=load(resolveFrom(manifestDir,c.purposes));
 const s=c.golden_scenario||{};
 const maxOd=Number(s.max_od_pairs||Infinity),maxPurpose=Number(s.max_purpose_od_pairs||Infinity),maxLines=Number(s.max_lines||Infinity);
 const originalOd=d.od||[];
 const rankedOd=originalOd.map((row,index)=>({row,index})).filter(x=>Number(x.row[2])>0)
   .sort((a,z)=>Number(z.row[2])-Number(a.row[2])||a.index-z.index);
 const selectedOdItems=rankedOd.slice(0,maxOd);
 const selectedOd=selectedOdItems.map(x=>x.row);
 const endpointCells=new Set();
 for(const item of selectedOdItems){
   const a=d.pts?.[Number(item.row[0])],z=d.pts?.[Number(item.row[1])];
   if(a)endpointCells.add(coarseCell(a));
   if(z)endpointCells.add(coarseCell(z));
 }
 const rankedLines=(b.lines||[]).map((line,index)=>{
   const cells=new Set((line.stops||[]).map(coarseCell));
   let coverage=0;for(const cell of cells)if(endpointCells.has(cell))coverage++;
   return{line,index,coverage,id:String(line.id??"")};
 }).sort((a,z)=>z.coverage-a.coverage||a.id.localeCompare(z.id)||a.index-z.index);
 const keepLines=new Set(rankedLines.slice(0,maxLines).map(x=>x.index));
 const lines=(b.lines||[]).map(x=>({...x,headways:Array.from(x.headways||[10,10,10,10,10],Number)}))
   .filter((_,i)=>keepLines.has(i));
 const base=(p.commuteBaseT||[]).map(decodeF32).map(arr=>selectedOdItems.map(item=>arr[item.index]));
 const layers=(p.layers||[]).map(x=>{
   const q=decodeF32(x.od),rows=[];for(let i=0;i<x.n;i++)rows.push([q[i*4],q[i*4+1],q[i*4+2],q[i*4+3]]);
   const keep=rows.map((row,index)=>({row,index})).filter(x=>Number(x.row[2])>0)
     .sort((a,z)=>Number(z.row[2])-Number(a.row[2])||a.index-z.index).slice(0,maxPurpose);
   const rawBase=(x.baseT||[]).map(decodeF32);
   return{od:keep.map(x=>x.row),out:x.out,ret:x.ret,baseT:rawBase.map(arr=>keep.map(item=>arr[item.index])),n:keep.length};
 });
 const geoms=lines.map(x=>{
   const st=x.stops||[];
   const cum=x.segLen?.length?[0,...x.segLen.reduce((a,v)=>[...a,a[a.length-1]+Number(v)],[])]:[0,...st.slice(1).reduce((a,v,i)=>[...a,a[a.length-1]+hav(st[i],v)],[])];
   return{stops:st,cum,cumT:x.cumT?.map(Number)||null,segLen:x.segLen?.map(Number)||cum.slice(1).map((v,i)=>v-cum[i]),segCostMul:x.segCostMul||null};
 });
 return{
   city:{pts:d.pts,od:selectedOd,model:m},
   lines,geoms,base,layers,
   scenario:{maxOd,maxPurpose,maxLines,originalOdPairs:originalOd.length,selectedOdPairs:selectedOd.length,
     originalPurposeOdPairs:(p.layers||[]).reduce((n,x)=>n+Number(x.n||0),0),originalLines:(b.lines||[]).length,selectedLines:lines.length}
 };
}
function clean(x){
  return {
    lines: x.lines || [],
    modeSplit: x.modeSplit || {transit:0,car:0,walk:0,rest:0},
    satisfaction: x.satisfaction || null,
    ridersPerDay: Number(x.ridersPerDay || 0),
    transferTrips: Number(x.transferTrips || 0),
    interchanges: x.interchanges || [],
    trackCapacity: x.trackCapacity || [],
    coveredCommuters: Number(x.coveredCommuters || 0),
    totalCommuters: Number(x.totalCommuters || 0),
    capitalCostM: Number(x.capitalCostM || 0),
    revenueDay: Number(x.revenueDay || 0),
    opexDay: Number(x.opexDay || 0),
    servedByPoint: x.servedByPoint || [],
    journeyOrigins: x.journeyOrigins || null,
    missedByPoint: x.missedByPoint || [],
    noRouteByPoint: x.noRouteByPoint || [],
    coveredPoint: x.coveredPoint || [],
    equilibrium: x.equilibrium || null,
  };
}
async function runOne(Bs,man,c,manifestDir){
 const q=build(c,manifestDir); MATRIX_CURRENT_CITY=q;
 try{
 const r=await Bs(q.city,q.lines,q.geoms,q.base,q.layers,false,undefined,undefined,{base:.6,perKm:.12});
 const full=clean(r),modes=r.modeSplit||{};
 const total=Number(r.ridersPerDay||0)+0; // scalar fields are already canonical rounded in the JS engine
 return {reference:{engine:"Takt web bundle",bundle:man.bundle.source,city:c.name,version:c.version,inputs:c.git_blob_sha},
 scenario:{demandLayer:"city demand.json",purposeLayers:q.layers.length,odPairs:q.city.od.length,zones:q.city.pts.length,lines:q.lines.length,...q.scenario},
 parity:{ridersPerDay:Number(r.ridersPerDay||0),capitalCostM:Number(r.capitalCostM||0),revenueDay:Number(r.revenueDay||0),opexDay:Number(r.opexDay||0),
   modeSplit:{transit:Number(modes.transit||0),car:Number(modes.car||0),walk:Number(modes.walk||0),rest:Number(modes.rest||0)},
   transferTrips:Number(r.transferTrips||0),coveredCommuters:Number(r.coveredCommuters||0),totalCommuters:Number(r.totalCommuters||0),
   satisfactionScore:Number(r.satisfaction?.score||0),satisfactionTotalTrips:Number(r.satisfaction?.totalTrips||0),equilibrium:r.equilibrium||null,
   lineSummary:(r.lines||[]).map(x=>({id:x.id,mode:x.mode,ridersPerDay:Number(x.ridersPerDay||0),fleet:Number(x.fleet||0),revenueDay:Number(x.revenueDay||0),opexDay:Number(x.opexDay||0),peakLoadFactor:Number(x.peakLoadFactor||0)}))},
 result:full}
 }finally{
   await terminateMatrixWorkers();
   MATRIX_CURRENT_CITY=null;
 }
}
async function main(){
 const manifestArg=process.argv.find(x=>x.startsWith("--manifest="));
 const manifestPath=manifestArg?path.resolve(manifestArg.slice("--manifest=".length)):DEFAULT_MANIFEST;
 const manifestDir=path.dirname(manifestPath);
 const man=load(manifestPath);
 const arg=process.argv.find(x=>x.startsWith("--city="));
 const outputArg=process.argv.find(x=>x.startsWith("--output="));
 const cases=arg?[man.city_cases.find(c=>c.name===arg.slice(7))]:man.city_cases;
 if(cases.some(x=>!x))throw Error("unknown city");
 if(outputArg && cases.length!==1)throw Error("--output requires exactly one city");
 const Bs=getBs();
 for(const c of cases){
   const out=outputArg
     ? path.resolve(outputArg.slice("--output=".length))
     : path.join(ROOT,"tests/fixtures/cities",c.name,"takt_browser_snapshot.json");
   fs.mkdirSync(path.dirname(out),{recursive:true});
   const snap=await runOne(Bs,man,c,manifestDir);
   fs.writeFileSync(out,JSON.stringify(snap,null,2)+"\n");
   console.log(out);
 }
}
main().catch(e=>{console.error(e&&e.stack?e.stack:e);process.exitCode=1});
