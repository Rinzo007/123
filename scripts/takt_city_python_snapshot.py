#!/usr/bin/env python3
"""Generate the canonical P6 Python snapshot for a pinned city case."""
from __future__ import annotations
import argparse,base64,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scipy import sparse
from passenger_flow import ModeChoiceConfig,TAKT_PERIODS,prepare_passenger_flow,run_passenger_flow
from passenger_flow.algorithm.kpis import _atomic_infrastructure_sections
from passenger_flow.base.models import vehicle_spec_for_route_type
from od.model import Zones
ROOT=Path(__file__).resolve().parents[1];MANIFEST=ROOT/"tests/fixtures/takt_release_city_cases.json"
def load(p): return json.loads(p.read_text(encoding="utf-8"))
def f32(s): return np.frombuffer(base64.b64decode(s),dtype="<f4").copy()
def coarse_cell(point: tuple[float, float]) -> tuple[int, int]:
 return (int(np.floor(float(point[0]) / 0.01)), int(np.floor(float(point[1]) / 0.0062)))

def _bounded_case(c, d, b, p):
 s=c.get("golden_scenario") or {}
 max_od=int(s.get("max_od_pairs",10**18)); max_purpose=int(s.get("max_purpose_od_pairs",10**18)); max_lines=int(s.get("max_lines",10**18))
 raw=np.asarray(d["od"],dtype=np.float64)
 order=np.argsort(-raw[:,2],kind="stable")
 positive=[int(i) for i in order if raw[int(i),2]>0.0][:max_od]
 endpoint_cells=set()
 for i in positive:
  endpoint_cells.add(coarse_cell(tuple(d["pts"][int(raw[i,0])])))
  endpoint_cells.add(coarse_cell(tuple(d["pts"][int(raw[i,1])])))
 ranked_lines=[]
 for i,L in enumerate(b.get("lines",[])):
  cells={coarse_cell((float(pt[0]),float(pt[1]))) for pt in (L.get("stops") or [])}
  ranked_lines.append((len(cells & endpoint_cells),str(L.get("id","")),i,L))
 ranked_lines.sort(key=lambda x:(-x[0],x[1],x[2]))
 keep={x[2] for x in ranked_lines[:max_lines]}
 lines_src=[L for i,L in enumerate(b.get("lines",[])) if i in keep]
 layers=[]
 original_purpose_rows=0
 for layer in p.get("layers",[]):
  q=f32(layer["od"])
  nrow=int(layer.get("n",len(q)//4))
  q=q.reshape((-1,4))[:nrow]
  original_purpose_rows += nrow
  idx=np.argsort(-q[:,2],kind="stable")[:max_purpose]
  base_layer=[f32(x) for x in (layer.get("baseT") or [])]
  layers.append({"od":q[idx],"out":layer.get("out",[]),"ret":layer.get("ret",[]),
                 "base_time_s":np.stack([x[idx] for x in base_layer]) if len(base_layer)==5 else None})
 return positive,raw,lines_src,layers,max_od,max_purpose,max_lines,original_purpose_rows

def build(c):
 d=load(ROOT/c["demand"]);b=load(ROOT/c["baseline"]);p=load(ROOT/c["purposes"]);m=load(ROOT/c["model"])
 positive,raw,bounded_lines,layers,max_od,max_purpose,max_lines,original_purpose_rows=_bounded_case(c,d,b,p)
 pts=np.asarray(d["pts"],dtype=np.float64)
 sel_raw=raw[positive]
 n=len(pts);rows=sel_raw[:,0].astype(np.int64);cols=sel_raw[:,1].astype(np.int64);vals=sel_raw[:,2].astype(np.float64)
 od=sparse.csr_matrix((vals,(rows,cols)),shape=(n,n))
 base_car=sel_raw[:,3].astype(np.float64) if sel_raw.shape[1]>=4 else None
 z=Zones(ids=np.arange(1,n+1,dtype=np.int64),polygons=tuple([None]*n),xy=pts[:,:2],bounds=(float(pts[:,0].min()),float(pts[:,1].min()),float(pts[:,0].max()),float(pts[:,1].max())))
 routes=[];src=[]
 for i,L in enumerate(bounded_lines):
  ss=tuple(SimpleNamespace(id=10_000_000+i*100_000+k,name=f"{L.get('name',i)}:{k}",latitude=float(s[1]),longitude=float(s[0])) for k,s in enumerate(L.get("stops",[])))
  dr=SimpleNamespace(name=str(L.get("name",i)),stops=ss,cumT=tuple(map(float,L.get("cumT",[]))) or None,segLen=tuple(map(float,L.get("segLen",[]))) or None)
  routes.append(SimpleNamespace(ok=True,route_id=i,name=str(L.get("name",i)),route_type=str(L.get("mode","bus")),directions=(dr,),
    bothWays=bool(L.get("bothWays",False)),headways=tuple(map(float,L.get("headways",[10]*5))),row=L.get("row"),
    rows=L.get("rows"),trackId=L.get("trackId"),capacity=L.get("capacity"),closed=bool(L.get("closed",False)),
    openStops=L.get("openStops"),builtSegs=L.get("builtSegs"),closedSegs=L.get("closedSegs"),gaps=L.get("gaps"),onTrack=L.get("onTrack")))
  src.append(L)
 bt=[f32(x) for x in p.get("commuteBaseT",[])]
 base=np.stack([x[positive] for x in bt]) if len(bt)==5 and all(x.size==len(raw) for x in bt) else None
 scenario={"maxOd":max_od,"maxPurpose":max_purpose,"maxLines":max_lines,"originalOdPairs":len(raw),
           "selectedOdPairs":len(positive),"originalPurposeOdPairs":original_purpose_rows,
           "originalLines":len(b.get("lines",[])),"selectedLines":len(routes)}
 return routes,raw,od,base,base_car,layers,z,src,m,pts,scenario

def mk(m):
 mob=m.get("mobility",{});car=m.get("car",{});rest=m.get("rest",{})
 return ModeChoiceConfig(car_no_car_share=float(mob.get("noCar",.35)),two_wheel_share=float(mob.get("twoWheelShare",.3)),
   two_wheel_speed_mps=float(mob.get("twoWheelSpeed",4.2)),two_wheel_reach_m=float(mob.get("twoWheelReachM",7000)),
   two_wheel_per_km_eur=float(mob.get("twoWheelPerKm",.03)),car_cost_per_km_eur=float(car.get("costPerKm",.25)),
   car_parking_eur=float(car.get("parkEur",1.5)),car_parking_min=float(car.get("parkingS",240))/60,
   vot_per_eur_s=float(m.get("votSPerEur",360)),rest_base_speed_kmh=float(rest.get("baseSpeed",3.6)),
   rest_cont_speed_kmh=float(rest.get("contSpeed",5)),rest_access_s=float(rest.get("accessS",420)),
   rest_wait_s=float(rest.get("waitS",240)),rest_circuity=float(rest.get("circuity",1.3)))
def track_capacity(seqs):
 sections,_=_atomic_infrastructure_sections(seqs);out=[]
 for key,ids in sorted(sections.items(),key=lambda kv:kv[0]):
  mode,a,b=key
  if mode=="bus" or len(ids)<2:continue
  try: ax,ay=map(int,a.split(","));bx,by=map(int,b.split(","))
  except ValueError:continue
  out.append({"coords":[[ax/1e5,ay/1e5],[bx/1e5,by/1e5]],"lines":[int(seqs[i]["route_id"]) for i in sorted(ids)],
    "tph":sum(sum(60/float(h) for h in (seqs[i].get("headways") or ()) if float(h)>0) for i in sorted(ids)),
    "limit":float(vehicle_spec_for_route_type(mode).track_tph)})
 return out
def snap(man,c):
 routes,raw,od,base,base_car,layers,z,src,model,pts,scenario=build(c);prep=prepare_passenger_flow(routes,z)
 result=run_passenger_flow(routes,od,z,population=pts[:,2],base_time_s=base,car_base_time_s=base_car,od_sparse=od,
   periods=TAKT_PERIODS,headway_min=10.0,mode_choice=mk(model),transfer_penalty_calc="takt",
   stop_search_radius_m=1500,wait_calc="takt",include_reliability=True,msa_max_iterations=6,msa_gap=.01,
   prepared=prep,demand_layers=layers,capex_factor=float(model.get('capex',{}).get('costFactor',1.0)),
   profile_timings=True)
 d=result.takt_diagnostics or {}
 perf=d.get("performance") or {}
 if perf:
  assignment_other=max(0.0,float(perf.get("assignment_s",0.0))-float(perf.get("access_s",0.0))-float(perf.get("journeys_s",0.0)))
  print(
   "[P6 profile] "
   f"access={float(perf.get('access_s',0.0)):.3f}s "
   f"journeys={float(perf.get('journeys_s',0.0)):.3f}s "
   f"assignment={float(perf.get('assignment_s',0.0)):.3f}s "
   f"assignment_other={assignment_other:.3f}s "
   f"msa={float(perf.get('msa_s',0.0)):.3f}s "
   f"crowd_state={float(perf.get('crowd_state_s',0.0)):.3f}s "
   f"journey_calls={int(perf.get('journey_calls',0.0))} "
   f"journey_cache_hits={int(perf.get('journey_cache_hits',0.0))} "
   f"access_cache_misses={int(perf.get('access_cache_misses',0.0))} "
   f"transfer_suffix_cache={len(getattr(prep.transfer_index,'downstream_cache',{}))}"
  )
 total=result.raw_assigned_trips+result.raw_car_trips+result.raw_walk_trips+result.raw_two_wheel_trips+result.raw_rest_trips
 lines=[]
 for lr in result.line_results:
  s=src[int(lr.route_id)] if 0<=int(lr.route_id)<len(src) else {}
  lines.append({"id":s.get("id",lr.route_id),"mode":lr.mode,"ridersPerDay":lr.trips,"lengthKm":lr.cycle_km/2,
    "capitalCostM":lr.capital_cost_eur/1e6,"fleet":lr.fleet,"revenueDay":lr.revenue_day,"opexDay":lr.opex_day,
    "peakLoadFactor":lr.crowding,"minHeadway":lr.min_headway,"passengerKm":lr.passenger_km,"crowdedPassengerKm":lr.crowded_passenger_km,
    "excessPassengerKm":lr.excess_passenger_km,"severePassengerKm":lr.severe_passenger_km,"extremePassengerKm":lr.extreme_passenger_km})
 line_summary=[{"id":x.get("id"),"mode":x.get("mode"),"ridersPerDay":float(x.get("ridersPerDay",0)),"fleet":float(x.get("fleet",0)),
   "revenueDay":float(x.get("revenueDay",0)),"opexDay":float(x.get("opexDay",0)),"peakLoadFactor":float(x.get("peakLoadFactor",0))} for x in lines]
 parity={"ridersPerDay":round(result.raw_assigned_trips),"capitalCostM":result.capital_cost_eur/1e6,"revenueDay":round(result.raw_revenue_day),"opexDay":round(result.raw_opex_day),
   "modeSplit":{"transit":result.raw_assigned_trips/max(total,1e-12),"car":result.raw_car_trips/max(total,1e-12),"walk":(result.raw_walk_trips+result.raw_two_wheel_trips)/max(total,1e-12),"rest":result.raw_rest_trips/max(total,1e-12)},
   "transferTrips":float(sum(x["trips"] for x in d.get("interchanges",[]))),"coveredCommuters":float(d.get("coveredCommuters",0)),
   "totalCommuters":float(d.get("totalCommuters",0)),"satisfactionScore":float(d.get("satisfaction",{}).get("score",0)),"satisfactionTotalTrips":int(d.get("satisfaction",{}).get("totalTrips",0)),
   "equilibrium":d.get("equilibrium",{}),"lineSummary":line_summary}
 full={"ridersPerDay":parity["ridersPerDay"],"transferTrips":parity["transferTrips"],"coveredCommuters":parity["coveredCommuters"],
  "totalCommuters":parity["totalCommuters"],"capitalCostM":parity["capitalCostM"],"revenueDay":parity["revenueDay"],"opexDay":parity["opexDay"],
  "modeSplit":parity["modeSplit"],"satisfaction":d.get("satisfaction",{}),"interchanges":d.get("interchanges",[]),"trackCapacity":track_capacity(list(prep.route_sequences)),
  "coveredPoint":d.get("coveredPoint",[]),"servedByPoint":d.get("servedByPoint",[]),"missedByPoint":d.get("missedByPoint",[]),
  "noRouteByPoint":d.get("noRouteByPoint",[]),"journeyOrigins":d.get("journeyOrigins",{}),"equilibrium":parity["equilibrium"],"lines":lines,
  "periods":[{"key":p.key,"label":p.label,"totalTrips":p.total_trips,"assignedTrips":p.assigned_trips,"carTrips":p.car_trips,"walkTrips":p.walk_trips,"twoWheelTrips":p.two_wheel_trips,"restTrips":p.rest_trips} for p in result.period_flows],
  "stops":[{"name":s.name,"lat":s.lat,"lon":s.lon,"boardings":s.boardings,"alightings":s.alightings,"totalFlow":s.total_flow,"routes":list(s.routes)} for s in result.stop_flows]}
 return {"reference":{"engine":"passenger_flow Python","bundle":man["bundle"]["source"],"city":c["name"],"version":c["version"],"inputs":c["git_blob_sha"]},
   "scenario":{"demandLayer":"primary OD","purposeLayers":len(layers),"odPairs":int(scenario["selectedOdPairs"]),"zones":len(z),"lines":len(routes),**scenario},"parity":parity,"result":full}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--city");ap.add_argument("--output-dir",type=Path,default=ROOT/"tests/fixtures/cities");a=ap.parse_args()
 man=load(MANIFEST);cases=man["city_cases"];cases=[next(x for x in cases if x["name"]==a.city)] if a.city else cases
 for c in cases:
  out=a.output_dir/c["name"]/"takt_python_snapshot.json";out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(snap(man,c),ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(out)
if __name__=="__main__":main()
