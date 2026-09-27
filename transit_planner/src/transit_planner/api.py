from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .analytics import _service_analytics, _track_capacity_analytics
from .assignment import AssignmentConfig, assign_demand
from .calibration import ObservedRouteRidership, calibrate_route_ridership
from .city import DemandZone
from .city_demand import CityDemandConfig, build_city_temporal_demand
from .demand import DemandMatrix, ODPairDemand
from .demand_streets import build_demand_streets, demand_streets_to_geojson
from .economics import EconomicsConfig, aggregate_temporal_economics, calculate_temporal_economics
from .geo import Point
from .geojson import connectors_to_geojson, places_to_geojson, roads_to_geojson, stops_to_geojson, zones_to_geojson
from .infrastructure import TrackType
from .network import TransitMode
from .od import GravityParameters, gravity_od
from .overture import OvertureConnectorProvider, OverturePlacesProvider, OvertureSource, OvertureTransitProvider, OvertureTransportationProvider
from .overture_network import OvertureNetworkProvider
from .projection import project_local_point_wgs84
from .reference_demand import build_reference_demand_layers
from .reference_model import REFERENCE_MOBILITY, REFERENCE_PURPOSE_LAYERS, TrackRow
from .scenario import ScenarioDefinition, compare_scenarios, run_scenario
from .serialization import network_from_dict
from .temporal_assignment import assign_temporal_demand
from .timetable import generate_service_timetable
from .zones import generate_zones_from_population_raster

app=FastAPI(title="Transit Planner",version="0.1.0")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
RELEASE="2026-09-23.1"

def bounds(s,w,n,e):
    if not(-90<=s<n<=90 and -180<=w<e<=180): raise HTTPException(400,"Некорректная географическая область")
    if (n-s)*(e-w)>.04: raise HTTPException(400,"Слишком большая область")
    return s,w,n,e

def src(r): return OvertureSource(release=r.strip() if isinstance(r,str) and r.strip() else RELEASE)

def econ(p, period, segments=None):
    r = p.get("economics_config", {})
    return EconomicsConfig(
        period_id=str(r.get("period_id", period)),
        fare_per_transit_trip=float(r.get("fare_per_transit_trip", 0)),
        annual_days=int(r.get("annual_days", 365)),
    )

def ed(x): return asdict(x)
def scenario(p,d):
    pairs=tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all")),None if x.get("base_time_min") is None else float(x["base_time_min"])) for x in p.get("demand",[]))
    zones=tuple(DemandZone(str(x["id"]),float(x["centroid_x"]),float(x["centroid_y"]),population=float(x.get("population",0)),jobs=float(x.get("jobs",0)),no_car_share=float(x.get("no_car_share",REFERENCE_MOBILITY.no_car_share))) for x in p.get("zones",[]))
    return ScenarioDefinition(str(p.get("id",d)),str(p.get("name",p.get("id",d))),network_from_dict(p["network"]),DemandMatrix(pairs),AssignmentConfig(**p.get("config",{"period_id":"am"})),zones)

def result_dict(x): return {k:getattr(x,k) for k in ("daily_vehicle_km","daily_fleet_cost","daily_operating_cost","daily_fare_revenue","annual_fleet_cost","annual_operating_cost","annual_fare_revenue","operating_cost_per_transit_trip","revenue_per_transit_trip")}

@app.get("/health")
def health(): return {"status":"ok","data_source":"overture","overture_release":RELEASE}
@app.post("/api/v1/network/validate")
def validate(p): n=network_from_dict(p); e=n.validate(); return {"valid":not e,"errors":e}

def load(provider,method,s,w,n,e,r):
    try:return getattr(provider(source=src(r),bbox=bounds(s,w,n,e)),method)()
    except (OSError,RuntimeError,TimeoutError) as x:raise HTTPException(502,str(x)) from x
@app.get("/api/v1/data/overture/roads")
def roads(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):return roads_to_geojson(load(OvertureTransportationProvider,"load_roads",south,west,north,east,release))
@app.get("/api/v1/data/overture/connectors")
def connectors(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):return connectors_to_geojson(load(OvertureConnectorProvider,"load_connectors",south,west,north,east,release))
@app.get("/api/v1/data/overture/stops")
def stops(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):return stops_to_geojson(load(OvertureTransitProvider,"load_stops",south,west,north,east,release))
@app.get("/api/v1/data/overture/places")
def places(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):return places_to_geojson(load(OverturePlacesProvider,"load_places",south,west,north,east,release))
@app.get("/api/v1/data/overture/network")
def network_data(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):
    b=bounds(south,west,north,east); s=src(release)
    try:
        with ThreadPoolExecutor(4) as p:
            fs=[p.submit(f) for f in (lambda:OvertureTransportationProvider(source=s,bbox=b).load_roads(),lambda:OvertureConnectorProvider(source=s,bbox=b).load_connectors(),lambda:OvertureTransitProvider(source=s,bbox=b).load_stops(),lambda:OverturePlacesProvider(source=s,bbox=b).load_places())]; r,c,t,pl=[f.result() for f in fs]
    except (OSError,RuntimeError,TimeoutError) as x:raise HTTPException(502,str(x)) from x
    return {"roads":roads_to_geojson(r),"connectors":connectors_to_geojson(c),"stops":stops_to_geojson(t),"places":places_to_geojson(pl),"release":s.release,"counts":{"roads":len(r),"connectors":len(c),"stops":len(t),"places":len(pl)}}
@app.post("/api/v1/data/overture/route")
def route(p:dict):
    try:
        b=bounds(float(p["south"]),float(p["west"]),float(p["north"]),float(p["east"])); pts=tuple(Point(float(x["lon"]),float(x["lat"])) for x in p["points"])
        if not 2<=len(pts)<=100:raise ValueError("Для маршрута нужны от 2 до 100 точек")
        n=OvertureNetworkProvider(source=src(p.get("release")),bbox=b,snap_max_distance_m=float(p.get("snap_distance_m",150))).load(include_connectors=False,include_stops=False,include_places=False); r=n.route_points(pts)
    except (KeyError,TypeError,ValueError) as x:raise HTTPException(400,str(x)) from x
    except (OSError,RuntimeError,TimeoutError) as x:raise HTTPException(502,str(x)) from x
    g=[project_local_point_wgs84(q,origin_lon=n.origin_lon,origin_lat=n.origin_lat) for q in r.geometry]; return {"type":"Feature","geometry":{"type":"LineString","coordinates":[[q.x,q.y] for q in g]},"properties":{"edge_ids":list(r.edge_ids),"length_m":r.length_m,"travel_time_min":r.travel_time_min,"snap_distances_m":list(r.snap_distances_m)}}
@app.get("/api/v1/demand/population-zones")
def population_zones(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...)):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster:raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    lon,lat=(west+east)/2,(south+north)/2
    try:z=generate_zones_from_population_raster(raster,bbox=bounds(south,west,north,east),origin_lon=lon,origin_lat=lat)
    except (OSError,RuntimeError,ValueError) as x:raise HTTPException(502,str(x)) from x
    return zones_to_geojson(z,origin_lon=lon,origin_lat=lat)
@app.get("/api/v1/demand/reference")
def reference_demand(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),origin_lon:float|None=Query(None),origin_lat:float|None=Query(None),release:str|None=Query(None)):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster:raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    b=bounds(south,west,north,east); lon=(west+east)/2 if origin_lon is None else origin_lon; lat=(south+north)/2 if origin_lat is None else origin_lat
    try:
        z=generate_zones_from_population_raster(raster,bbox=b,origin_lon=lon,origin_lat=lat); pl=OverturePlacesProvider(source=src(release),bbox=b).load_places(); cfg=CityDemandConfig(); od=gravity_od(z,parameters=GravityParameters(speed_kph=cfg.reference_speed_kph,decay=cfg.decay),trip_rate=cfg.trip_rate); layers=build_reference_demand_layers(z,pl,origin_lon=lon,origin_lat=lat); idx={x.id:i for i,x in enumerate(z)}; profiles={x.key:x for x in REFERENCE_PURPOSE_LAYERS}
        pts=[]
        for x in z:q=project_local_point_wgs84(Point(x.centroid_x,x.centroid_y),origin_lon=lon,origin_lat=lat);pts.append([q.x,q.y,max(0,x.population),max(x.jobs,x.population,sum(x.attractions.values()))])
        rows=[[idx[x.origin_zone_id],idx[x.destination_zone_id],x.trips_per_day,max(120,(x.base_time_min or 0)*60)] for x in od.pairs if x.origin_zone_id in idx and x.destination_zone_id in idx]
        ordered=tuple(z)
        baseline_t=None
        if len(ordered)<=2000:
            baseline_t=[[0.0]*len(ordered) for _ in ordered]
            for i,a in enumerate(ordered):
                for j,bz in enumerate(ordered):
                    if i==j:continue
                    dx=a.centroid_x-bz.centroid_x; dy=a.centroid_y-bz.centroid_y
                    baseline_t[i][j]=max(120.0,(dx*dx+dy*dy)**0.5/(cfg.reference_speed_kph/3.6))
        return {"city":"dynamic","source":"WorldPop + Overture + city demand model","pts":pts,"od":rows,"baselineT":baseline_t,"layers":[{"purpose":x.purpose,"label":x.label,"od":[[idx[o],idx[d],t,max(120,base)] for o,d,t,base in x.od_pairs if o in idx and d in idx],"out":list(profiles[x.purpose].outbound_shares),"ret":list(profiles[x.purpose].return_shares)} for x in layers.layers],"meta":{"zones":len(z),"commuter_od_pairs":len(rows),"purpose_layers":len(layers.layers),"purpose_od_pairs":sum(len(x.od_pairs) for x in layers.layers),"baselineT_included":baseline_t is not None}}
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,TimeoutError) as x:raise HTTPException(502,str(x)) from x
@app.post("/api/v1/demand/streets")
def demand_streets(p:dict):
    pairs=tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all"))) for x in p.get("demand",[])); z={str(x["id"]):DemandZone(str(x["id"]),float(x["centroid_x"]),float(x["centroid_y"])) for x in p.get("zones",[])}; return demand_streets_to_geojson(build_demand_streets(pairs,z,min_trips=float(p.get("min_trips",0))),z,origin_lon=float(p["origin_lon"]),origin_lat=float(p["origin_lat"]))
@app.post("/api/v1/assignment")
def assignment(p:dict):
    n=network_from_dict(p["network"]); d=DemandMatrix(tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all"))) for x in p.get("demand",[]))); z={str(x["id"]):DemandZone(str(x["id"]),float(x["centroid_x"]),float(x["centroid_y"]),population=float(x.get("population",0)),jobs=float(x.get("jobs",0))) for x in p.get("zones",[])}; cfg=AssignmentConfig(**p["config"]); r=assign_demand(n,d,zones=z,config=cfg); return {"metrics":asdict(r.metrics),"iterations":r.iterations,"max_load_ratio":r.max_load_ratio,"unserved_transit_demand":r.unserved_transit_demand,"loss_reasons":[asdict(x) for x in r.loss_reasons],"route_flows":[asdict(x) for x in r.route_flows],"section_loads":[asdict(x) for x in r.section_loads],"stop_flows":[asdict(x) for x in r.stop_flows],"track_capacity":[asdict(x) for x in _track_capacity_analytics(n)]}
@app.post("/api/v1/economics")
def economics(p:dict):
    return calculate_economics_payload(p)

def calculate_economics_payload(p:dict):
    s=scenario(p,"economics"); r=run_scenario(s,economics_config=econ(p,s.assignment_config.period_id)); return {"scenario_id":r.scenario_id,"name":r.name,"economics":result_dict(r.economics)}

@app.post("/api/v1/scenario/compare")
def scenario_compare(p:dict):
    return compare_scenario_payload(p)

def compare_scenario_payload(p:dict):
    a=scenario(p["base"],"base"); b=scenario(p["alternative"],"alternative"); ar=run_scenario(a,economics_config=econ(p["base"],a.assignment_config.period_id) if "economics_config" in p["base"] else None); br=run_scenario(b,economics_config=econ(p["alternative"],b.assignment_config.period_id) if "economics_config" in p["alternative"] else None); c=compare_scenarios(ar,br)
    return {"base":{"scenario_id":ar.scenario_id,"name":ar.name,"metrics":{"transit_share":ar.assignment.metrics.transit_share}},"alternative":{"scenario_id":br.scenario_id,"name":br.name,"metrics":{"transit_share":br.assignment.metrics.transit_share}},"comparison":{"base_scenario_id":c.base_scenario_id,"alternative_scenario_id":c.alternative_scenario_id,"metrics":[{"metric":x.metric,"base":x.base,"alternative":x.alternative,"delta":x.delta,"relative_delta":x.relative_delta} for x in c.metrics],"sections":[{"route_id":x.route_id,"from_stop_id":x.from_stop_id,"to_stop_id":x.to_stop_id,"base_passengers":x.base_passengers,"alternative_passengers":x.alternative_passengers,"delta":x.delta} for x in c.sections],"services":[{"service_id":x.service_id,"route_id":x.route_id,"period_id":x.period_id,"base_riders":x.base_riders,"alternative_riders":x.alternative_riders,"riders_delta":x.riders_delta,"base_peak_load_factor":x.base_peak_load_factor,"alternative_peak_load_factor":x.alternative_peak_load_factor,"peak_load_factor_delta":x.peak_load_factor_delta,"base_fleet":x.base_fleet,"alternative_fleet":x.alternative_fleet,"fleet_delta":x.fleet_delta,"base_effective_headway_min":x.base_effective_headway_min,"alternative_effective_headway_min":x.alternative_effective_headway_min,"effective_headway_delta":x.effective_headway_delta,"base_minimum_headway_min":x.base_minimum_headway_min,"alternative_minimum_headway_min":x.alternative_minimum_headway_min,"minimum_headway_delta":x.minimum_headway_delta} for x in c.services]}}
@app.post("/api/v1/calibration/route-ridership")
def calibration(p:dict):
    r=calibrate_route_ridership(tuple(ObservedRouteRidership(str(x["route_id"]),float(x["observed_boardings_per_day"])) for x in p.get("observed",[])),{str(k):float(v) for k,v in p.get("simulated",{}).items()}); return {"mae":r.mae,"rmse":r.rmse,"mape":r.mape,"routes":[asdict(x) for x in r.routes]}
@app.post("/api/v1/timetable")
def timetable(p:dict):
    t=generate_service_timetable(str(p.get("service_id","service")),{str(k):(int(v["start_minute"]),int(v["end_minute"])) for k,v in p["periods"].items()},{str(k):float(v) for k,v in p["headway_by_period"].items()},offset_minute=int(p.get("offset_minute",0))); return {"service_id":t.service_id,"periods":[asdict(x) for x in t.periods]}
@app.post("/api/v1/assignment/city")
def city_assignment(p:dict):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster:raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    try:
        n=network_from_dict(p["network"]); b=bounds(float(p["south"]),float(p["west"]),float(p["north"]),float(p["east"])); lon=float(p.get("origin_lon",(b[1]+b[3])/2)); lat=float(p.get("origin_lat",(b[0]+b[2])/2)); z=generate_zones_from_population_raster(raster,bbox=b,origin_lon=lon,origin_lat=lat); pl=OverturePlacesProvider(source=src(p.get("release")),bbox=b).load_places(); raw=p.get("demand_config",{}); dc=CityDemandConfig(trip_rate=float(raw.get("trip_rate",.12)),decay=float(raw.get("decay",.08)),reference_speed_kph=float(raw.get("reference_speed_kph",30))); td=build_city_temporal_demand(z,pl,origin_lon=lon,origin_lat=lat,config=dc); ac=AssignmentConfig(**p["config"]); tr=assign_temporal_demand(n,td,zones={x.id:x for x in z},config=ac); ec=econ(p,ac.period_id); te=calculate_temporal_economics(n,tr,config=ec); total=aggregate_temporal_economics(n,tr,config=ec)
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,TimeoutError) as x:raise HTTPException(502,str(x)) from x
    r=tr.aggregate(); return {"data":{"zones":len(z),"places":len(pl),"od_pairs":len(td.pairs),"total_demand_trips":tr.total_demand_trips},"assignment":{"metrics":asdict(r.metrics),"max_load_ratio":r.max_load_ratio,"unserved_transit_demand":r.unserved_transit_demand},"economics":result_dict(total),"periods":[{"period_id":x.period_id,"demand_trips":x.demand_trips,"transit_trips":x.result.metrics.transit_trips,"services":[asdict(_service_analytics(n,sid,x.period_id,assignment=x.result)) for sid in n.services if x.period_id in n.services[sid].headway_by_period],"economics":result_dict(te[i])} for i,x in enumerate(tr.periods)]}


@app.post("/api/v1/analytics")
def network_analytics(p: dict):
    from .analytics import analyze_network
    n = network_from_dict(p["network"])
    d = DemandMatrix(tuple(
        ODPairDemand(
            str(x["origin_zone_id"]), str(x["destination_zone_id"]),
            float(x["trips_per_day"]), str(x.get("purpose", "all"))
        ) for x in p.get("demand", [])
    ))
    zones = {
        str(x["id"]): DemandZone(
            str(x["id"]), float(x["centroid_x"]), float(x["centroid_y"]),
            population=float(x.get("population", 0)),
            jobs=float(x.get("jobs", 0)),
        ) for x in p.get("zones", [])
    }
    result = assign_demand(n, d, zones=zones, config=AssignmentConfig(**p["config"]))
    analytics = analyze_network(n, result, zones=tuple(zones.values()))
    return asdict(analytics)


@app.post("/api/v1/network/validate/physical")
def validate_network(p: dict):
    n = network_from_dict(p["network"])
    errors = list(n.validate())
    for section in n.track_sections.values():
        if section.max_slope_percent is not None and section.slope_percent > section.max_slope_percent:
            errors.append(
                f"Track {section.id} slope {section.slope_percent:.2f}% exceeds "
                f"{section.max_slope_percent:.2f}%"
            )
        if section.curve_radius_m is not None and section.curve_radius_m < 1:
            errors.append(f"Track {section.id} has invalid curve radius")
    return {"valid": not errors, "errors": errors}
