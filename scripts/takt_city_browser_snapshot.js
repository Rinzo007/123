#!/usr/bin/env node
"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),os=require("node:os");
const {Worker:NodeWorker}=require("node:worker_threads");

const MATRIX_WORKER_SOURCE=`
const {parentPort}=require("node:worker_threads");
let graph=null;
function solve(msg){
  const {job,start,end,stops,offsets,targets,costs}=msg;
  const nodes=stops*2;
  const times=new Float64Array((end-start)*stops);
  times.fill(Infinity);
  const previous=new Int32Array((end-start)*nodes);
  previous.fill(-1);
  const dist=new Float64Array(nodes);
  const used=new Uint8Array(nodes);
  let heapNode=new Int32Array(Math.max(1024,nodes));
  let heapDist=new Float64Array(heapNode.length);
  for(let src=start;src<end;src++){
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
      if(node>=stops)times[(src-start)*stops+(node-stops)]=cost;
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
parentPort.on("message",msg=>{if(msg.type==="init")graph=msg;else if(msg.type==="solve")solve({...msg,...graph});});
`


class TaktNodeWorker{
  constructor(){
    this.worker=new NodeWorker(MATRIX_WORKER_SOURCE,{eval:true});
    this.onmessage=null;this.onerror=null;this.graphKey=null;
    this.worker.on("message",data=>{if(this.onmessage)this.onmessage({data});});
    this.worker.on("error",err=>{if(this.onerror)this.onerror(err);});
  }
  postMessage(msg){
    if(msg.type==="solve" && msg.offsets){
      if(this.graphKey!==msg.offsets){
        this.graphKey=msg.offsets;
        const offsets=new SharedArrayBuffer(msg.offsets.byteLength);
        const targets=new SharedArrayBuffer(msg.targets.byteLength);
        const costs=new SharedArrayBuffer(msg.costs.byteLength);
        new Int32Array(offsets).set(msg.offsets);
        new Int32Array(targets).set(msg.targets);
        new Float64Array(costs).set(msg.costs);
        this.worker.postMessage({type:"init",offsets,targets,costs});
      }
      this.worker.postMessage({type:"solve",job:msg.job,start:msg.start,end:msg.end,stops:msg.stops});
      return;
    }
    this.worker.postMessage(msg);
  }
  terminate(){return this.worker.terminate();}
}
const ROOT=path.resolve(__dirname,".."),MANIFEST=path.join(ROOT,"tests/fixtures/takt_release_city_cases.json");
const load=p=>JSON.parse(fs.readFileSync(p,"utf8"));
const decodeF32=s=>{const b=Buffer.from(s,"base64");const v=new Float32Array(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));return Array.from(v)};
const hav=(a,b)=>{const r=6371e3,z=Math.PI/180,dl=(b[1]-a[1])*z,dn=(b[0]-a[0])*z,la=a[1]*z,lb=b[1]*z,q=Math.sin(dl/2)**2+Math.cos(la)*Math.cos(lb)*Math.sin(dn/2)**2;return 2*r*Math.asin(Math.sqrt(q))};
function getBs(){
 const bundlePath=process.env.TAKT_BUNDLE_PATH?path.resolve(process.env.TAKT_BUNDLE_PATH):path.join(ROOT,"scripts/bd956ff0a1875604740f.js");
 const b=fs.readFileSync(bundlePath,"utf8"),m="})();",i=b.lastIndexOf(m);if(i<0)throw Error("Takt bundle terminator not found");
 const s={console,performance,setTimeout,clearTimeout,setInterval,clearInterval,TextEncoder,TextDecoder,URL,URLSearchParams,
 Uint8Array,Uint16Array,Uint32Array,Int32Array,Float32Array,Float64Array,DataView,ArrayBuffer,SharedArrayBuffer,BigInt64Array,BigUint64Array,Math,Date,JSON,
 Map,Set,WeakMap,WeakSet,Promise,Error,TypeError,RangeError,Symbol,Reflect,Object,Array,Number,String,Boolean,RegExp,parseInt,parseFloat,isFinite,isNaN,Worker:TaktNodeWorker,navigator:{hardwareConcurrency:Math.max(2,os.cpus().length)},
 atob:globalThis.atob,btoa:globalThis.btoa};s.globalThis=s;s.location={hostname:"localhost",href:"http://localhost/"};s.self={location:s.location,addEventListener(){},postMessage(){}};
 vm.runInNewContext(b.slice(0,i)+"\nglobalThis.__TAKT_Bs=Bs;\n"+b.slice(i),s,{filename:bundlePath,displayErrors:true});
 if(typeof s.__TAKT_Bs!=="function")throw Error("Takt Bs() not exported"); return s.__TAKT_Bs;
}
function build(c){
 const d=load(path.join(ROOT,c.demand)),m=load(path.join(ROOT,c.model)),b=load(path.join(ROOT,c.baseline)),p=load(path.join(ROOT,c.purposes));
 const lines=(b.lines||[]).map(x=>({...x,headways:Array.from(x.headways||[10,10,10,10,10],Number)}));
 const geoms=lines.map(x=>{const st=x.stops||[];const cum=x.segLen?.length?[0,...x.segLen.reduce((a,v)=>[...a,a[a.length-1]+Number(v)],[])]:[0,...st.slice(1).reduce((a,v,i)=>[...a,a[a.length-1]+hav(st[i],v)],[])];return{stops:st,cum,cumT:x.cumT?.map(Number)||null,segLen:x.segLen?.map(Number)||cum.slice(1).map((v,i)=>v-cum[i]),segCostMul:x.segCostMul||null}});
 const base=(p.commuteBaseT||[]).map(decodeF32);
 const layers=(p.layers||[]).map(x=>{const q=decodeF32(x.od);const rows=[];for(let i=0;i<x.n;i++)rows.push([q[i*4],q[i*4+1],q[i*4+2],q[i*4+3]]);return{od:rows,out:x.out,ret:x.ret,baseT:(x.baseT||[]).map(decodeF32),n:x.n}});
 return{city:{pts:d.pts,od:d.od,model:m},lines,geoms,base:base.length===5?base:undefined,layers};
}
function clean(x){const out=JSON.parse(JSON.stringify(x));delete out.computeMs;delete out.profile;delete out.diag;return out}
async function runOne(Bs,man,c){
 const q=build(c),r=await Bs(q.city,q.lines,q.geoms,q.base,q.layers,false,undefined,undefined,{base:.6,perKm:.12});
 const full=clean(r),modes=r.modeSplit||{};
 const total=Number(r.ridersPerDay||0)+0; // scalar fields are already canonical rounded in the JS engine
 return {reference:{engine:"Takt web bundle",bundle:man.bundle.source,city:c.name,version:c.version,inputs:c.git_blob_sha},
 scenario:{demandLayer:"city demand.json",purposeLayers:q.layers.length,odPairs:q.city.od.length,zones:q.city.pts.length,lines:q.lines.length},
 parity:{ridersPerDay:Number(r.ridersPerDay||0),capitalCostM:Number(r.capitalCostM||0),revenueDay:Number(r.revenueDay||0),opexDay:Number(r.opexDay||0),
   modeSplit:{transit:Number(modes.transit||0),car:Number(modes.car||0),walk:Number(modes.walk||0),rest:Number(modes.rest||0)},
   transferTrips:Number(r.transferTrips||0),coveredCommuters:Number(r.coveredCommuters||0),totalCommuters:Number(r.totalCommuters||0),
   satisfaction:r.satisfaction||null,equilibrium:r.equilibrium||null,
   lineSummary:(r.lines||[]).map(x=>({id:x.id,mode:x.mode,ridersPerDay:Number(x.ridersPerDay||0),fleet:Number(x.fleet||0),revenueDay:Number(x.revenueDay||0),opexDay:Number(x.opexDay||0),peakLoadFactor:Number(x.peakLoadFactor||0)}))},
 result:full}
}
async function main(){
 const man=load(MANIFEST),arg=process.argv.find(x=>x.startsWith("--city="));
 const cases=arg?[man.city_cases.find(c=>c.name===arg.slice(7))]:man.city_cases;if(cases.some(x=>!x))throw Error("unknown city");
 const Bs=getBs();for(const c of cases){const out=path.join(ROOT,"tests/fixtures/cities",c.name,"takt_browser_snapshot.json");fs.mkdirSync(path.dirname(out),{recursive:true});const snap=await runOne(Bs,man,c);fs.writeFileSync(out,JSON.stringify(snap,null,2)+"\n");console.log(out)}}
main().catch(e=>{console.error(e&&e.stack?e.stack:e);process.exitCode=1});
