import type {NetworkPayload} from "../types";
import { solveDemand, solveMatrix, ReferenceEvaluationClient, type ReferenceDemandBatch, type ReferenceMatrixInput } from "./reference-runtime";

export type EvaluationSummary={lines:number;stops:number;dailyDepartures:number};
export type ClientPreviewResult={
  evaluation: EvaluationSummary;
  operations: {dailyDepartures:number; fleetEstimate:number};
};
export type ReferenceDemandBatchResult=Awaited<ReturnType<typeof solveDemand>>;
export type ReferenceMatrixResult=Awaited<ReturnType<typeof solveMatrix>>;

type WorkerKind="evaluation"|"assignment";
const workers:Partial<Record<WorkerKind,Worker>>={};

function getWorker(kind:WorkerKind):Worker{
  const existing=workers[kind]; if(existing) return existing;
  const urls:Record<WorkerKind,URL>={
    evaluation:new URL("./evaluation.worker.ts",import.meta.url),
    assignment:new URL("./assignment.worker.ts",import.meta.url),
  };
  const worker=new Worker(urls[kind],{type:"module"});
  workers[kind]=worker;
  return worker;
}

function request<T>(kind:WorkerKind,payload:unknown):Promise<T>{
  return new Promise((resolve,reject)=>{
    const worker=getWorker(kind);
    const onMessage=(event:MessageEvent<T>)=>{cleanup();resolve(event.data)};
    const onError=(event:ErrorEvent)=>{cleanup();reject(event.error??new Error(event.message))};
    const cleanup=()=>{worker.removeEventListener("message",onMessage);worker.removeEventListener("error",onError)};
    worker.addEventListener("message",onMessage);
    worker.addEventListener("error",onError);
    worker.postMessage(payload);
  });
}

export function evaluateNetwork(network:NetworkPayload){return request<EvaluationSummary>("evaluation",{kind:"summary",network});}
export function estimateDepartures(network:NetworkPayload){return request<{kind:"departures";dailyDepartures:number;fleetEstimate:number}>("assignment",{kind:"departures",network});}

export function solveDemandStrategy(batch:ReferenceDemandBatch):Promise<ReferenceDemandBatchResult>{
  return solveDemand(batch);
}

export function solveRoadMatrix(input:ReferenceMatrixInput):Promise<ReferenceMatrixResult>{
  return solveMatrix(input);
}

export function createEvaluationClient(workerCount?:number):ReferenceEvaluationClient{
  return new ReferenceEvaluationClient(workerCount);
}

export function disposeComputationWorkers(){
  for(const worker of Object.values(workers)) worker?.terminate();
  for(const key of Object.keys(workers) as WorkerKind[]) delete workers[key];
}

export async function runClientPreview(network:NetworkPayload):Promise<ClientPreviewResult>{
  const [evaluation, operations] = await Promise.all([
    evaluateNetwork(network),
    estimateDepartures(network),
  ]);
  return {evaluation, operations};
}
