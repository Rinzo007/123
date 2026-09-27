from transit_planner.serialization import network_from_dict


def test_complete_frontend_network_payload_is_strictly_loadable():
    payload = {
        "stops": [
            {"id": "a", "name": "A", "location": {"x": 0, "y": 0}, "is_station": True},
            {"id": "b", "name": "B", "location": {"x": 1000, "y": 0}, "is_station": True},
        ],
        "routes": [{
            "id": "r", "name": "R", "mode": "metro", "stop_ids": ["a", "b"],
            "geometry": {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
            "track_section_ids": ["t1"], "both_ways": True, "closed": False,
        }],
        "vehicle_types": [{
            "id": "metro", "name": "Metro", "mode": "metro",
            "capacity": 750, "operating_cost_per_km": 1,
        }],
        "periods": [{"id": "am", "start_minute": 360, "end_minute": 540}],
        "services": [{
            "id": "svc", "route_id": "r", "vehicle_type_id": "metro",
            "headway_by_period": {"am": 10},
        }],
        "track_sections": [{
            "id": "t1", "length_km": 1, "track_type": "tunnel",
            "capacity_departures_per_hour": 30, "station_ids": ["a", "b"],
            "speed_limit_kph": 90,
        }],
        "stations": [{
            "id": "s1", "name": "A", "stop_id": "a",
            "platform_ids": ["p1"], "group_id": None, "interchange": False,
            "platform_length_m": 120,
        }, {
            "id": "s2", "name": "B", "stop_id": "b",
            "platform_ids": ["p2"], "group_id": None, "interchange": False,
            "platform_length_m": 120,
        }],
        "platforms": [
            {"id": "p1", "station_id": "s1", "length_m": 120, "track_ids": [], "layout": "side", "number": 1},
            {"id": "p2", "station_id": "s2", "length_m": 120, "track_ids": [], "layout": "side", "number": 1},
        ],
        "station_groups": [],
        "rolling_stock": [{
            "id": "stock", "name": "Metro", "vehicle_type_id": "metro",
            "car_capacity": 100, "car_length_m": 20, "train_width_m": 3,
            "max_cars": 8, "max_speed_kph": 90, "acceleration_mps2": 1,
            "deceleration_mps2": 1.2, "lateral_acceleration_mps2": 1,
            "minimum_curve_radius_m": 180, "dwell_seconds": 20,
            "car_cost": 1, "train_operating_cost_per_hour": 1,
            "car_operating_cost_per_hour": 1, "track_maintenance_cost_per_km_year": 1,
            "station_maintenance_cost_per_year": 1, "tph_limit": 30,
        }],
        "fare_groups": [{
            "id": "default", "name": "Default", "fare_system": "flat",
            "flat_fare": 2, "route_fares": {}, "transfer_policy": "none",
            "transfer_window_min": 0, "boarding_charge": 0, "per_km_rate": 0,
            "fare_cap": None, "zones": [], "zone_base_fare": 0,
            "zone_per_zone_fare": 0,
        }],
    }
    network = network_from_dict(payload)
    assert network.stations["s1"].stop_id == "a"
    assert network.rolling_stock["stock"].capacity == 800
    assert network.fare_groups["default"].price() == 2
