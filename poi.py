import json
import logging
import math
import time
from collections import Counter, defaultdict
from typing import Any

import requests

from .cache import JsonCache
from .geometry import point_to_segment_dist_km
from .metrics import PoiStats
from .models import RouteData

logger = logging.getLogger("wikiroutes.gis.poi")

OVERPASS_SERVERS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

POI_TAG_RULES: dict[str, list[dict[str, str]]] = {
    "Школа": [{"amenity": "school"}],
    "Детский сад": [{"amenity": "kindergarten"}],
}


def classify_poi_by_tags(tags: dict[str, Any]) -> str | None:
    for category, rules in POI_TAG_RULES.items():
        for rule in rules:
            if all(tags.get(k) == v for k, v in rule.items()):
                return category

    return None


def _point_to_segment_planar_km(
    plat: float,
    plon: float,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    cos_mid: float,
) -> float:
    """Плоская equirect-оценка расстояния от точки до отрезка (км).

    Быстрый фильтр (несколько умножений вместо сферической тригонометрии):
    применяется только для отсечения заведомо далёких пар с запасом;
    точное расстояние считает ``point_to_segment_dist_km``.
    """
    dx = (plon - lon1) * cos_mid
    dy = plat - lat1
    ex = (lon2 - lon1) * cos_mid
    ey = lat2 - lat1

    denom = ex * ex + ey * ey

    if denom <= 0.0:
        return math.hypot(dx, dy) * 111.0

    t = max(0.0, min(1.0, (dx * ex + dy * ey) / denom))

    return math.hypot(dx - t * ex, dy - t * ey) * 111.0


def _overpass_request(query: str, timeout: int = 200) -> dict[str, Any]:
    last_error: str | None = None

    for server_url in OVERPASS_SERVERS:
        server_name = server_url.split("/")[2]

        try:
            logger.info("POI: пробую сервер %s...", server_name)

            response = requests.post(
                server_url,
                data={"data": query},
                timeout=timeout,
                headers={"User-Agent": "WikiroutesExporter/1.0"},
            )

            if response.status_code == 429:
                logger.warning("POI: %s вернул 429", server_name)
                last_error = f"429 от {server_name}"
                time.sleep(2)
                continue

            if response.status_code == 504:
                logger.warning("POI: %s вернул 504", server_name)
                last_error = f"504 от {server_name}"
                time.sleep(2)
                continue

            response.raise_for_status()

            try:
                data = response.json()

                if isinstance(data, dict):
                    return data

                last_error = f"Неожиданный JSON от {server_name}"
                continue
            except json.JSONDecodeError as exc:
                logger.warning("POI: %s вернул невалидный JSON: %s", server_name, exc)
                last_error = f"JSONDecodeError от {server_name}"
                continue

        except requests.exceptions.Timeout:
            logger.warning("POI: %s не ответил (timeout)", server_name)
            last_error = f"Timeout от {server_name}"
            continue
        except requests.RequestException as exc:
            logger.warning("POI: ошибка %s: %s", server_name, exc)
            last_error = str(exc)
            continue

    raise RuntimeError(f"Все серверы недоступны. Последняя ошибка: {last_error}")


def load_poi_osm(
    bbox_wgs84: tuple[float, float, float, float],
    city: str,
    cache: JsonCache,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    min_lat, min_lon, max_lat, max_lon = bbox_wgs84

    cache_key = f"{city}_{round(min_lat, 3)}_{round(max_lat, 3)}_osm"
    cached = cache.get("poi", cache_key)

    if isinstance(cached, list):
        pois = cached
        logger.info("POI: читаю из кеша (%d точек)", len(pois))
    else:
        logger.info("POI: загрузка из OpenStreetMap (Overpass API)...")
        logger.info(
            "POI: bbox = (%.3f, %.3f) - (%.3f, %.3f)",
            min_lat,
            min_lon,
            max_lat,
            max_lon,
        )

        # Исправлено: node/way/relation, а не только way.
        query = f"""
[out:json][timeout:180][bbox:{min_lat},{min_lon},{max_lat},{max_lon}];
(
  node["amenity"="school"];
  way["amenity"="school"];
  relation["amenity"="school"];
  node["amenity"="kindergarten"];
  way["amenity"="kindergarten"];
  relation["amenity"="kindergarten"];
);
out center;
"""

        try:
            data = _overpass_request(query)
        except Exception:
            logger.exception("POI: не удалось получить данные ни с одного сервера")
            return [], {}

        elements = data.get("elements", [])
        logger.info("POI: всего элементов в bbox: %d", len(elements))

        pois = []
        skipped_no_name = 0
        skipped_military = 0
        skipped_no_match = 0

        for el in elements:
            tags = el.get("tags", {})
            el_type = el.get("type")

            name = str(tags.get("name", "")).strip()

            if not name:
                skipped_no_name += 1
                continue

            if tags.get("landuse") == "military":
                skipped_military += 1
                continue

            if tags.get("military") in ("airfield", "base", "barracks", "naval_base"):
                skipped_military += 1
                continue

            if tags.get("access") == "private" and tags.get("aeroway") == "aerodrome":
                skipped_military += 1
                continue

            poi_type = classify_poi_by_tags(tags)

            if not poi_type:
                skipped_no_match += 1
                continue

            if el_type == "node":
                lat, lon = el.get("lat"), el.get("lon")
            elif el_type in ("way", "relation"):
                center = el.get("center")

                if not center:
                    continue

                lat, lon = center.get("lat"), center.get("lon")
            else:
                continue

            if lat is None or lon is None:
                continue

            pois.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "poi_type": poi_type,
                    "name": name,
                    "osm_type": el_type,
                }
            )

        if not pois:
            logger.warning("POI: не найдено ни одной точки интереса в bbox")
            cache.put("poi", cache_key, [])
            return [], {}

        cache.put("poi", cache_key, pois)

        logger.info("POI: сохранено в кеш")
        logger.info("POI: отфильтровано без имени: %d", skipped_no_name)
        logger.info("POI: отфильтровано военных объектов: %d", skipped_military)
        logger.info("POI: отфильтровано без тега: %d", skipped_no_match)

    counts = Counter(p["poi_type"] for p in pois)
    values = {typ: max(1.0, 10000.0 / cnt) for typ, cnt in counts.items()}

    for p in pois:
        p["poi_value"] = values[p["poi_type"]]

    return pois, values


def filter_poi_near_routes(
    pois: list[dict[str, Any]],
    routes: list[RouteData],
    buffer_m: float,
) -> tuple[list[dict[str, Any]], int]:
    if not pois or not routes:
        return pois, 0

    buffer_km = buffer_m / 1000.0
    cell_lat = max(0.001, buffer_m / 111000.0)

    min_cos = 1.0

    for route in routes:
        if route.error or not route.directions:
            continue

        for direction in route.directions:
            coords = direction.coords

            for i in range(1, len(coords)):
                lat1, lon1 = coords[i - 1]
                lat2, lon2 = coords[i]

                mid_lat = (lat1 + lat2) / 2.0
                cos_val = abs(math.cos(math.radians(mid_lat)))

                if cos_val < min_cos:
                    min_cos = cos_val

    min_cos = max(0.1, min_cos)
    cell_lon = max(0.001, cell_lat / min_cos)

    route_segments: list[tuple[float, float, float, float, float]] = []
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)

    for route in routes:
        if route.error or not route.directions:
            continue

        for direction in route.directions:
            coords = direction.coords

            for i in range(1, len(coords)):
                lat1, lon1 = coords[i - 1]
                lat2, lon2 = coords[i]

                seg_idx = len(route_segments)
                cos_mid = math.cos(math.radians((lat1 + lat2) / 2.0))
                route_segments.append((lat1, lon1, lat2, lon2, cos_mid))

                la0 = math.floor((min(lat1, lat2) - cell_lat) / cell_lat)
                la1 = math.floor((max(lat1, lat2) + cell_lat) / cell_lat)
                lo0 = math.floor((min(lon1, lon2) - cell_lon) / cell_lon)
                lo1 = math.floor((max(lon1, lon2) + cell_lon) / cell_lon)

                for gla in range(la0, la1 + 1):
                    for glo in range(lo0, lo1 + 1):
                        grid[(gla, glo)].append(seg_idx)

    if not route_segments:
        return pois, 0

    filtered: list[dict[str, Any]] = []

    for p in pois:
        plat, plon = p["lat"], p["lon"]
        key = (math.floor(plat / cell_lat), math.floor(plon / cell_lon))

        near = False

        for idx in grid.get(key, ()):
            lat1, lon1, lat2, lon2, cos_mid = route_segments[idx]

            # Плоский фильтр с запасом отсекает далёкие пары без
            # сферической тригонометрии; точное расстояние — только
            # для потенциально близких.
            if (
                _point_to_segment_planar_km(plat, plon, lat1, lon1, lat2, lon2, cos_mid)
                <= buffer_km * 1.5
                and point_to_segment_dist_km(plat, plon, lat1, lon1, lat2, lon2)
                <= buffer_km
            ):
                near = True
                break

        if near:
            filtered.append(p)

    return filtered, len(pois) - len(filtered)


def compute_route_poi(
    route: RouteData,
    pois: list[dict[str, Any]],
    buffer_m: float,
    directions: list[Any] | None = None,
) -> PoiStats:
    if not pois:
        return PoiStats()

    dirs = directions if directions is not None else route.directions

    coords = [coord for direction in dirs for coord in direction.coords]

    if not coords:
        return PoiStats()

    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]

    lat_mid = sum(lats) / len(lats)

    deg_m = buffer_m / 111000.0
    deg_m_lon = deg_m / max(0.1, math.cos(math.radians(lat_mid)))

    min_lat = min(lats) - deg_m
    max_lat = max(lats) + deg_m
    min_lon = min(lons) - deg_m_lon
    max_lon = max(lons) + deg_m_lon

    candidates = [
        p
        for p in pois
        if min_lon <= p["lon"] <= max_lon and min_lat <= p["lat"] <= max_lat
    ]

    if not candidates:
        return PoiStats()

    cell_lat = max(0.001, deg_m)
    cell_lon = max(0.001, deg_m_lon)

    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    segments: list[tuple[float, float, float, float, float]] = []

    for direction in dirs:
        dcoords = direction.coords

        for i in range(1, len(dcoords)):
            lat1, lon1 = dcoords[i - 1]
            lat2, lon2 = dcoords[i]

            seg_idx = len(segments)
            cos_mid = math.cos(math.radians((lat1 + lat2) / 2.0))
            segments.append((lat1, lon1, lat2, lon2, cos_mid))

            la0 = math.floor((min(lat1, lat2) - cell_lat) / cell_lat)
            la1 = math.floor((max(lat1, lat2) + cell_lat) / cell_lat)
            lo0 = math.floor((min(lon1, lon2) - cell_lon) / cell_lon)
            lo1 = math.floor((max(lon1, lon2) + cell_lon) / cell_lon)

            for gla in range(la0, la1 + 1):
                for glo in range(lo0, lo1 + 1):
                    grid[(gla, glo)].append(seg_idx)

    if not segments:
        return PoiStats()

    buffer_km = buffer_m / 1000.0
    hits: list[dict[str, Any]] = []

    for p in candidates:
        plat, plon = p["lat"], p["lon"]
        cell_key = (math.floor(plat / cell_lat), math.floor(plon / cell_lon))

        cand_idxs = grid.get(cell_key, [])

        if not cand_idxs:
            continue

        near = False

        for idx in cand_idxs:
            lat1, lon1, lat2, lon2, cos_mid = segments[idx]

            if (
                _point_to_segment_planar_km(plat, plon, lat1, lon1, lat2, lon2, cos_mid)
                <= buffer_km * 1.5
                and point_to_segment_dist_km(plat, plon, lat1, lon1, lat2, lon2)
                <= buffer_km
            ):
                near = True
                break

        if near:
            hits.append(p)

    if not hits:
        return PoiStats()

    by_type: dict[str, int] = {}

    for hit in hits:
        poi_type = hit["poi_type"]
        by_type[poi_type] = by_type.get(poi_type, 0) + 1

    return PoiStats(
        total_value=sum(hit["poi_value"] for hit in hits),
        count=len(hits),
        by_type=by_type,
    )


def compute_poi(
    routes: list[RouteData],
    bbox_wgs84: tuple[float, float, float, float],
    city: str,
    buffer_m: float,
    cache: JsonCache,
) -> tuple[
    dict[int, PoiStats],
    dict[str, float],
    dict[tuple[int, int], PoiStats],
]:
    pois, _ = load_poi_osm(bbox_wgs84, city, cache)

    if not pois:
        return {}, {}, {}

    filtered_pois, removed = filter_poi_near_routes(pois, routes, buffer_m)

    logger.info(
        "POI: после фильтра по коридорам: %d из %d (отброшено вне буфера: %d)",
        len(filtered_pois),
        len(pois),
        removed,
    )

    if not filtered_pois:
        logger.warning("POI: после фильтра не осталось ни одной точки")
        return {}, {}, {}

    counts = Counter(p["poi_type"] for p in filtered_pois)
    values = {typ: max(1.0, 1000.0 / cnt) for typ, cnt in counts.items()}

    for p in filtered_pois:
        p["poi_value"] = values[p["poi_type"]]

    stats: dict[int, PoiStats] = {}
    dir_stats: dict[tuple[int, int], PoiStats] = {}

    for i, route in enumerate(routes, start=1):
        if route.error or not route.directions:
            continue

        item = compute_route_poi(route, filtered_pois, buffer_m)
        stats[route.route_id] = item

        for di, d in enumerate(route.directions):
            dir_stats[(route.route_id, di)] = compute_route_poi(
                route,
                filtered_pois,
                buffer_m,
                directions=[d],
            )

        logger.info(
            "POI [%d/%d] %s: %d точек, value=%.1f",
            i,
            len(routes),
            route.name,
            item.count,
            item.total_value,
        )

    return stats, values, dir_stats
