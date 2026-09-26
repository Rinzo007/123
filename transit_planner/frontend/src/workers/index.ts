import type {NetworkPayload} from "../types";

export type EvaluationSummary={lines:number;stops:number;dailyDepartures:number};

type WorkerKind="evaluation"|"assignment"|"matrix"|"demand-choice";

type WorkerMap={
 evaluation:typeof import("./evaluation.worker");
 assignment:typeof import("./assignment.worker");
 matrix:typeof import("./matrix.worker");
 "demand-choice":typeof import("./demand-choice.worker");
};

const workers:Partial<Record<WorkerKind,Worker>>={};

export function getComputationWorker(kind:WorkerKind):Worker{
 const existing=workers[kind]; if(existing) return existing;
 const urls:Record<WorkerKind,URL>={
  evaluation:new URL("./evaluation.worker.ts",import.meta.url),
  assignment:new URL("./assignment.worker.ts",import.meta.url),
  matrix:new URL("./matrix.worker.ts",import.meta.url),
  "demand-choice":new URL("./demand-choice.worker.ts",import.meta.url),
 };
 const worker=new Worker(urls[kind],{type:"module"}); workers[kind]=worker; return worker;
}

export function evaluateNetwork(network:NetworkPayload):Promise<EvaluationSummary>{
 return new Promise((resolve,reject)=>{
  const worker=getComputationWorker("evaluation");
  const onMessage=(event:MessageEvent<EvaluationSummary>)=>{cleanup();resolve(event.data)};
  const onError=(event:ErrorEvent)=>{cleanup();reject(event.error??new Error(event.message))};
  const cleanup=()=>{worker.removeEventListener("message",onMessage);worker.removeEventListener("error",onError)};
  worker.addEventListener("message",onMessage);worker.addEventListener("error",onError);
  worker.postMessage({kind:"summary",network});
 });
}

export function estimateDepartures(network:NetworkPayload):Promise<{dailyDepartures:number;fleetEstimate:number}>{
 return new Promise((resolve,reject)=>{
  const worker=getComputationWorker("assignment");
  const onMessage=(event:MessageEvent)=>{cleanup();resolve(event.data)};
  const onError=(event:ErrorEvent)=>{cleanup();reject(event.error??new Error(event.message))};
  const cleanup=()=>{worker.removeEventListener("message",onMessage);worker.removeEventListener("error",onError)};
  worker.addEventListener("message",onMessage);worker.addEventListener("error",onError);
  worker.postMessage({kind:"departures",network});
 });
}

export function disposeComputationWorkers(){
 for(const worker of Object.values(workers)) worker?.terminate();
 for(const key of Object.keys(workers) as WorkerKind[]) delete workers[key];
}
