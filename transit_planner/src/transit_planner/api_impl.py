from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
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
from .overture import OvertureConnectorProvider, OverturePlacesProvider, OvertureSource, OvertureTransitProvider, OvertureTransportationProvider, OvertureUrbanProvider
from .overture_network import OvertureNetworkProvider
from .projection import project_local_point_wgs84
from .reference_demand import build_reference_demand_layers
from .reference_model import REFERENCE_MOBILITY, REFERENCE_PURPOSE_LAYERS, TrackRow
from .scenario import ScenarioDefinition, compare_scenarios, run_scenario
from .serialization import network_from_dict
from .temporal_assignment import assign_temporal_demand
from .timetable import generate_service_timetable
from .urban import UrbanContext
from .zones import generate_zones_from_population_raster

app=FastAPI(title="Transit Planner",version="0.1.0")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
DEFAULT_RELEASE="2026-09-23.1"

def bbox(s,w,n,e):
    if not(-90<=s<n<=90 and -180<=w<e<=180): raise HTTPException(400,"Некорректная географическая область")
    if (n-s)*(e-w)>.04: raise HTTPException(400,"Слишком большая область")
    return s,w,n,e

def source(release): return OvertureSource(release=(release.strip() if isinstance(release,str) and release.strip() else DEFAULT_RELEASE))

def econ(payload,period,segments=None):
    r=payload.get("economics_config",{})
    if not isinstance(r,dict): raise TypeError("economics_config must be an object")
    seg=segments if segments is not None else r.get("reference_segment_cost_multipliers")
    rows=r.get("reference_row_cost_multipliers"); modes=r.get("infrastructure_cost_per_km"); tracks=r.get("infrastructure_cost_per_track_km")
    return EconomicsConfig(period_id=str(r.get("period_id",period)),fare_per_transit_trip=float(r.get("fare_per_transit_trip",0)),annual_days=int(r.get("annual_days",365)),infrastructure_cost_per_km=None if modes is None else {TransitMode(str(k)):float(v) for k,v in modes.items()},infrastructure_cost_per_track_km=None if tracks is None else {TrackType(str(k)):float(v) for k,v in tracks.items()},station_cost=float(r.get("station_cost",0)),reference_cost_multiplier=float(r.get("reference_cost_multiplier",1)),reference_row_cost_multipliers=None if rows is None else {TrackRow(str(k)):float(v) for k,v in rows.items()},reference_segment_cost_multipliers=None if seg is None else {str(k):tuple(float(v) for v in x) for k,x in seg.items()})

def econ_dict(x): return {k:getattr(x,k) for k in ("daily_vehicle_km","daily_fleet_cost","daily_operating_cost","daily_fare_revenue","annual_fleet_cost","annual_operating_cost","annual_fare_revenue","capital_cost","operating_cost_per_transit_trip","revenue_per_transit_trip")}

def scenario(p,default):
    pairs=tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all")),None if x.get("base_time_min") is None else float(x["base_time_min"])) for x in p.get("demand",[]))
    zones=tuple(DemandZone(id=str(x["id"]),centroid_x=float(x["centroid_x"]),centroid_y=float(x["centroid_y"]),population=float(x.get("population",0)),jobs=float(x.get("jobs",0)),no_car_share=float(x.get("no_car_share",REFERENCE_MOBILITY.no_car_share))) for x in p.get("zones",[]))
    return ScenarioDefinition(id=str(p.get("id",default)),name=str(p.get("name",p.get("id",default))),network=network_from_dict(p["network"]),demand=DemandMatrix(pairs),assignment_config=AssignmentConfig(**p.get("config",{"period_id":"am"})),zones=zones)

def urban(network,bounds,lon,lat,release):
    buildings,water=OvertureUrbanProvider(source=source(release),bbox=bounds).load(); ctx=UrbanContext(buildings,water); result={}; count=0
    for route in network.routes.values():
        vals=[]
        for i,_ in enumerate(route.segment_pairs()):
            geometry=network.route_segment_geometry_points(route,i)
            if len(geometry)<2: raise ValueError(f"Route {route.id} segment {i} has no geometry")
            pts=tuple(project_local_point_wgs84(p,origin_lon=lon,origin_lat=lat) for p in geometry)
            m,_=ctx.construction_multiplier(route.mode.value,network.route_segment_row(route,i).value,pts,cost_per_km=network.route_segment_cost_per_km(route,i)); vals.append(float(m)); count+=1
        result[route.id]=tuple(vals)
    return result,{"release":source(release).release,"buildings":len(buildings),"water":len(water),"segments":count}

@app.get("/health")
def health(): return {"status":"ok","data_source":"overture","overture_release":DEFAULT_RELEASE}
@app.post("/api/v1/network/validate")
def validate_network(p):
    n=network_from_dict(p); e=n.validate(); return {"valid":not e,"errors":e}

def geo(provider,method,s,w,n,e,release):
    try: return getattr(provider(source=source(release),bbox=bbox(s,w,n,e)),method)()
    except (OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,f"Overture недоступен: {x}") from x
@app.get("/api/v1/data/overture/roads")
def roads(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)): return roads_to_geojson(geo(OvertureTransportationProvider,"load_roads",south,west,north,east,release))
@app.get("/api/v1/data/overture/connectors")
def connectors(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)): return connectors_to_geojson(geo(OvertureConnectorProvider,"load_connectors",south,west,north,east,release))
@app.get("/api/v1/data/overture/stops")
def stops(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)): return stops_to_geojson(geo(OvertureTransitProvider,"load_stops",south,west,north,east,release))
@app.get("/api/v1/data/overture/places")
def places(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)): return places_to_geojson(geo(OverturePlacesProvider,"load_places",south,west,north,east,release))
@app.get("/api/v1/data/overture/network")
def overture_network(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),release:str|None=Query(None)):
    b=bbox(south,west,north,east); src=source(release)
    try:
        with ThreadPoolExecutor(4) as p:
            fs=[p.submit(f) for f in (lambda:OvertureTransportationProvider(source=src,bbox=b).load_roads(),lambda:OvertureConnectorProvider(source=src,bbox=b).load_connectors(),lambda:OvertureTransitProvider(source=src,bbox=b).load_stops(),lambda:OverturePlacesProvider(source=src,bbox=b).load_places())]; r,c,t,pl=[f.result() for f in fs]
    except (OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,f"Overture недоступен: {x}") from x
    return {"roads":roads_to_geojson(r),"connectors":connectors_to_geojson(c),"stops":stops_to_geojson(t),"places":places_to_geojson(pl),"release":src.release,"counts":{"roads":len(r),"connectors":len(c),"stops":len(t),"places":len(pl)}}
@app.post("/api/v1/data/overture/route")
def route(p:dict):
    try:
        b=bbox(float(p["south"]),float(p["west"]),float(p["north"]),float(p["east"])); pts=tuple(Point(float(x["lon"]),float(x["lat"])) for x in p["points"])
        if not 2<=len(pts)<=100: raise ValueError("Для маршрута нужны от 2 до 100 точек")
        n=OvertureNetworkProvider(source=source(p.get("release")),bbox=b,snap_max_distance_m=float(p.get("snap_distance_m",150))).load(include_connectors=False,include_stops=False,include_places=False); r=n.route_points(pts)
    except (KeyError,TypeError,ValueError) as x: raise HTTPException(400,str(x)) from x
    except (OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,f"Overture недоступен: {x}") from x
    g=[project_local_point_wgs84(q,origin_lon=n.origin_lon,origin_lat=n.origin_lat) for q in r.geometry]
    return {"type":"Feature","geometry":{"type":"LineString","coordinates":[[q.x,q.y] for q in g]},"properties":{"edge_ids":list(r.edge_ids),"length_m":r.length_m,"travel_time_min":r.travel_time_min,"snap_distances_m":list(r.snap_distances_m)}}
@app.post("/api/v1/data/overture/urban-multipliers")
def urban_api(p:dict):
    try: m,meta=urban(network_from_dict(p["network"]),bbox(float(p["south"]),float(p["west"]),float(p["north"]),float(p["east"])),float(p["origin_lon"]),float(p["origin_lat"]),p.get("release"))
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,str(x)) from x
    return {"release":meta["release"],"routes":{k:{"segment_multipliers":list(v)} for k,v in m.items()},"counts":meta}

@app.get("/api/v1/demand/population-zones")
def population_zones(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...)):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster: raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    lon,lat=(west+east)/2,(south+north)/2
    try: z=generate_zones_from_population_raster(raster,bbox=bbox(south,west,north,east),origin_lon=lon,origin_lat=lat)
    except (OSError,RuntimeError,ValueError) as x: raise HTTPException(502,str(x)) from x
    return zones_to_geojson(z,origin_lon=lon,origin_lat=lat)

@app.get("/api/v1/demand/reference")
def reference_demand(south:float=Query(...),west:float=Query(...),north:float=Query(...),east:float=Query(...),origin_lon:float|None=Query(None),origin_lat:float|None=Query(None),release:str|None=Query(None)):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster: raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    b=bbox(south,west,north,east); lon=(west+east)/2 if origin_lon is None else origin_lon; lat=(south+north)/2 if origin_lat is None else origin_lat
    try:
        z=generate_zones_from_population_raster(raster,bbox=b,origin_lon=lon,origin_lat=lat); pl=OverturePlacesProvider(source=source(release),bbox=b).load_places(); cfg=CityDemandConfig(); od=gravity_od(z,parameters=GravityParameters(speed_kph=cfg.reference_speed_kph,decay=cfg.decay),trip_rate=cfg.trip_rate); layers=build_reference_demand_layers(z,pl,origin_lon=lon,origin_lat=lat); idx={x.id:i for i,x in enumerate(z)}
        pts=[]
        for x in z:
            q=project_local_point_wgs84(Point(x.centroid_x,x.centroid_y),origin_lon=lon,origin_lat=lat); pts.append([q.x,q.y,max(0,x.population),max(x.jobs,x.population,sum(x.attractions.values()))])
        rows=[[idx[x.origin_zone_id],idx[x.destination_zone_id],x.trips_per_day,max(120,(x.base_time_min or 0)*60)] for x in od.pairs if x.origin_zone_id in idx and x.destination_zone_id in idx]
        return {"city":"dynamic","source":"WorldPop + Overture + city demand model","pts":pts,"od":rows,"baselineT":None,"layers":[{"purpose":x.purpose,"label":x.label,"od":[[idx[o],idx[d],t,max(120,base)] for o,d,t,base in x.od_pairs if o in idx and d in idx],"out":list(map(float,{p.key:p for p in REFERENCE_PURPOSE_LAYERS}[x.purpose].outbound_shares)),"ret":list(map(float,{p.key:p for p in REFERENCE_PURPOSE_LAYERS}[x.purpose].return_shares))} for x in layers.layers],"meta":{"zones":len(z),"commuter_od_pairs":len(rows),"purpose_layers":len(layers.layers),"purpose_od_pairs":sum(len(x.od_pairs) for x in layers.layers),"baselineT_included":False}}
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,str(x)) from x

@app.post("/api/v1/demand/streets")
def demand_streets(p:dict):
    pairs=tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all"))) for x in p.get("demand",[])); z={str(x["id"]):DemandZone(str(x["id"]),float(x["centroid_x"]),float(x["centroid_y"])) for x in p.get("zones",[])}; return demand_streets_to_geojson(build_demand_streets(pairs,z,min_trips=float(p.get("min_trips",0))),z,origin_lon=float(p["origin_lon"]),origin_lat=float(p["origin_lat"]))

@app.post("/api/v1/assignment")
def assignment(p:dict):
    n=network_from_dict(p["network"]); d=DemandMatrix(tuple(ODPairDemand(str(x["origin_zone_id"]),str(x["destination_zone_id"]),float(x["trips_per_day"]),str(x.get("purpose","all"))) for x in p.get("demand",[]))); z={str(x["id"]):DemandZone(str(x["id"]),float(x["centroid_x"]),float(x["centroid_y"]),population=float(x.get("population",0)),jobs=float(x.get("jobs",0))) for x in p.get("zones",[])}; cfg=AssignmentConfig(**p["config"]); r=assign_demand(n,d,zones=z,config=cfg); return {"metrics":r.metrics.__dict__ if hasattr(r.metrics,"__dict__") else {k:getattr(r.metrics,k) for k in ("total_trips","transit_trips","car_trips","walk_trips","bike_trips","rest_trips","transit_share","average_transit_time_min","average_transfers")},"iterations":r.iterations,"max_load_ratio":r.max_load_ratio,"unserved_transit_demand":r.unserved_transit_demand,"loss_reasons":[{"reason":x.reason,"trips":x.trips} for x in r.loss_reasons],"route_flows":[x.__dict__ for x in r.route_flows],"section_loads":[x.__dict__ for x in r.section_loads],"stop_flows":[x.__dict__ for x in r.stop_flows],"track_capacity":[x.__dict__ for x in _track_capacity_analytics(n)]}

@app.post("/api/v1/economics")
def economics_api(p:dict):
    s=scenario(p,"economics"); r=run_scenario(s,economics_config=econ(p,s.assignment_config.period_id)); return {"scenario_id":r.scenario_id,"name":r.name,"economics":econ_dict(r.economics)}

@app.post("/api/v1/scenario/compare")
def compare_api(p:dict):
    a=scenario(p["base"],"base"); b=scenario(p["alternative"],"alternative"); ar=run_scenario(a,economics_config=econ(p["base"],a.assignment_config.period_id) if "economics_config" in p["base"] else None); br=run_scenario(b,economics_config=econ(p["alternative"],b.assignment_config.period_id) if "economics_config" in p["alternative"] else None); c=compare_scenarios(ar,br)
    return {"base":{"scenario_id":ar.scenario_id,"name":ar.name,"metrics":{"transit_share":ar.assignment.metrics.transit_share}},"alternative":{"scenario_id":br.scenario_id,"name":br.name,"metrics":{"transit_share":br.assignment.metrics.transit_share}},"comparison":{"base_scenario_id":c.base_scenario_id,"alternative_scenario_id":c.alternative_scenario_id,"metrics":[x.__dict__ for x in c.metrics],"sections":[x.__dict__ for x in c.sections],"services":[x.__dict__ for x in c.services]}}

@app.post("/api/v1/calibration/route-ridership")
def calibration(p:dict):
    r=calibrate_route_ridership(tuple(ObservedRouteRidership(str(x["route_id"]),float(x["observed_boardings_per_day"])) for x in p.get("observed",[])),{str(k):float(v) for k,v in p.get("simulated",{}).items()}); return {"mae":r.mae,"rmse":r.rmse,"mape":r.mape,"routes":[x.__dict__ for x in r.routes]}

@app.post("/api/v1/timetable")
def timetable(p:dict):
    t=generate_service_timetable(str(p.get("service_id","service")),{str(k):(int(v["start_minute"]),int(v["end_minute"])) for k,v in p["periods"].items()},{str(k):float(v) for k,v in p["headway_by_period"].items()},offset_minute=int(p.get("offset_minute",0))); return {"service_id":t.service_id,"periods":[{"period_id":x.period_id,"departures_minute":list(x.departures_minute)} for x in t.periods]}

@app.post("/api/v1/assignment/city")
def city_assignment(p:dict):
    raster=os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster: raise HTTPException(503,"TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    try:
        n=network_from_dict(p["network"]); b=bbox(float(p["south"]),float(p["west"]),float(p["north"]),float(p["east"])); lon=float(p.get("origin_lon",(b[1]+b[3])/2)); lat=float(p.get("origin_lat",(b[0]+b[2])/2)); z=generate_zones_from_population_raster(raster,bbox=b,origin_lon=lon,origin_lat=lat); pl=OverturePlacesProvider(source=source(p.get("release")),bbox=b).load_places(); raw=p.get("demand_config",{}); dc=CityDemandConfig(trip_rate=float(raw.get("trip_rate",.12)),decay=float(raw.get("decay",.08)),reference_speed_kph=float(raw.get("reference_speed_kph",30))); td=build_city_temporal_demand(z,pl,origin_lon=lon,origin_lat=lat,config=dc); ac=AssignmentConfig(**p["config"]); tr=assign_temporal_demand(n,td,zones={x.id:x for x in z},config=ac); sm,um=urban(n,b,lon,lat,p.get("release")); ec=econ(p,ac.period_id,sm); te=calculate_temporal_economics(n,tr,config=ec); total=aggregate_temporal_economics(n,tr,config=ec)
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,TimeoutError) as x: raise HTTPException(502,str(x)) from x
    r=tr.aggregate(); return {"data":{"zones":len(z),"places":len(pl),"od_pairs":len(td.pairs),"total_demand_trips":tr.total_demand_trips},"urban_context":um,"assignment":{"metrics":{k:getattr(r.metrics,k) for k in ("total_trips","transit_trips","car_trips","walk_trips","bike_trips","rest_trips","transit_share","average_transit_time_min","average_transfers")},"max_load_ratio":r.max_load_ratio,"unserved_transit_demand":r.unserved_transit_demand},"economics":econ_dict(total),"periods":[{"period_id":x.period_id,"demand_trips":x.demand_trips,"transit_trips":x.result.metrics.transit_trips,"economics":econ_dict(te[i]),"services":[_service_analytics(n,sid,x.period_id,assignment=x.result).__dict__ for sid in n.services if x.period_id in n.services[sid].headway_by_period]} for i,x in enumerate(tr.periods)]}
