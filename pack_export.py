"""Экспорт «Takt city pack» для оркестратора пассажиропотока.

Собирает из OD-артефактов (``<city>_od_pairs.parquet``,
``<city>_od_zones.parquet`` / ``.geojson``, ``<city>_od_purposes.parquet``)
и сети маршрутов запуска набор JSON-файлов, который читает оркестратор
``passengerflow`` (demand.json, districts.json, baseline.json, model.json,
purposes.bin.json, demand-streets.json, manifest.json).

Координаты в формате Takt везде передаются как ``[lon, lat]``; во внутренних
моделях WikiRoutes используется ``(lat, lon)`` — конвертация выполняется здесь.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .config import DEFAULT_DATA_DIR, DEFAULT_ORCHESTRATOR_DIR, CliConfig
from .geometry import haversine_km
from .models import RouteData
from .pipeline.runtime import PipelineResult

# Роли файлов внутри pack (ключи manifest). Совпадают с ролями оркестратора.
PACK_ROLES = (
    "demand.json",
    "districts.json",
    "baseline.json",
    "model.json",
    "purposes.bin.json",
    "demand-streets.json",
)

# Пиковый интервал (мин) и средняя скорость (км/ч) по типам маршрутов.
# Используются для синтеза baseline, когда реальное расписание недоступно.
MODE_PEAK_MIN = {
    "bus": 10,
    "trolleybus": 10,
    "electrobus": 12,
    "tram": 8,
    "metro": 5,
    "train": 15,
    "water": 20,
    "funicular": 15,
    "cable": 20,
    "monorail": 6,
    "idea": 20,
}
MODE_SPEED_KMH = {
    "bus": 25,
    "trolleybus": 20,
    "electrobus": 25,
    "tram": 25,
    "metro": 35,
    "train": 40,
    "water": 20,
    "funicular": 10,
    "cable": 10,
    "monorail": 30,
    "idea": 20,
}

# Скорость «по прямой» для оценки времени поездки в demand (км/ч).
_DEMAND_SPEED_KMH = 30.0
_MIN_TIME_S = 120.0
_DWELL_S = 25.0


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Расстояние между точками в км; аргументы в порядке (lon, lat)."""
    return haversine_km(float(lat1), float(lon1), float(lat2), float(lon2))


def _ring_area(ring: list) -> float:
    """Площадь кольца (ребро/геодезика аппроксимирована декартово) для выбора
    наибольшей части MultiPolygon."""
    area = 0.0
    prev = ring[-1]
    for cur in ring:
        lon1, lat1 = prev
        lon2, lat2 = cur
        area += (lon2 - lon1) * (lat2 + lat1)
        prev = cur
    return abs(area) / 2.0


@dataclass(frozen=True)
class _Zone:
    """Зона OD: идентификатор, центроид, кольцо полигона и балансы."""
    zone_id: int
    lon: float
    lat: float
    production: float
    attraction: float
    ring: tuple[tuple[float, float], ...]

    @property
    def centroid(self) -> tuple[float, float]:
        return self.lon, self.lat


def _read_rows(path: Path, what: str) -> list[dict]:
    """Читает parquet-таблицу в список словарей (pyarrow)."""
    if not path.is_file():
        raise FileNotFoundError(f"Не найден {what}: {path}")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Для чтения OD-артефактов нужен pyarrow: pip install pyarrow"
        ) from exc
    return pq.read_table(path).to_pylist()


def _sum_od_balances(
    pairs: list[tuple[int, int, float]],
) -> dict[int, tuple[float, float]]:
    """Балансы зон из матрицы пар: production = исходящие, attraction = входящие."""
    production: dict[int, float] = {}
    attraction: dict[int, float] = {}
    for orig, dest, trips in pairs:
        production[orig] = production.get(orig, 0.0) + trips
        attraction[dest] = attraction.get(dest, 0.0) + trips
    zone_ids = set(production) | set(attraction)
    return {zone_id: (production.get(zone_id, 0.0), attraction.get(zone_id, 0.0)) for zone_id in zone_ids}


def load_zones(data_dir: Path, city: str) -> list[_Zone]:
    """Загружает зоны из od_zones.geojson.

    Балансы (production/attraction) берутся из ``od_pairs.parquet`` — сумм
    исходящих и входящих поездок на зону, чтобы согласовать с матрицей OD;
    при отсутствии пар — из ``od_zones.parquet``.
    """
    data_dir = Path(data_dir)
    geojson_path = data_dir / f"{city}_od_zones.geojson"
    if not geojson_path.is_file():
        raise FileNotFoundError(
            f"Не найден фалай зон: {geojson_path}. "
            "Пакет строится из OD-артефактов каталога данных."
        )

    with geojson_path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)

    parquet_path = data_dir / f"{city}_od_zones.parquet"
    balances: dict[int, tuple[float, float]] = {}
    if parquet_path.is_file():
        for row in _read_rows(parquet_path, str(parquet_path)):
            try:
                zone_id = int(row["zone_id"])
            except (KeyError, TypeError, ValueError):
                continue
            production = float(row.get("production") or 0.0)
            attraction = float(row.get("attraction") or 0.0)
            balances[zone_id] = (production, attraction)

    zones: list[_Zone] = []
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        raw_id = props.get("zone_id", len(zones) + 1)
        try:
            zone_id = int(raw_id)
        except (TypeError, ValueError):
            zone_id = len(zones) + 1
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        geom_type = geometry.get("type")
        if geom_type == "Polygon" and coords:
            rings = coords[0]
        elif geom_type == "MultiPolygon" and coords:
            if not all(polygon and polygon[0] for polygon in coords):
                raise ValueError(f"Зона {zone_id}: пустой MultiPolygon в geojson зон")
            rings = max(
                (polygon[0] for polygon in coords),
                key=lambda ring: _ring_area(ring),
            )
        else:
            raise ValueError(
                f"Зона {zone_id}: ожидался Polygon в geojson зон, получен {geom_type}"
            )
        ring: list[tuple[float, float]] = []
        for lon, lat in rings:
            ring.append((float(lon), float(lat)))
        mean_lon = sum(point[0] for point in ring) / len(ring)
        mean_lat = sum(point[1] for point in ring) / len(ring)
        production, attraction = balances.get(zone_id, (0.0, 0.0))
        zones.append(
            _Zone(
                zone_id=zone_id,
                lon=mean_lon,
                lat=mean_lat,
                production=float(production) if math.isfinite(production) else 0.0,
                attraction=float(attraction) if math.isfinite(attraction) else 0.0,
                ring=tuple(ring),
            )
        )

    zones.sort(key=lambda zone: zone.zone_id)

    pairs_path = data_dir / f"{city}_od_pairs.parquet"
    if pairs_path.is_file():
        od_balances: dict[int, tuple[float, float]] = {}
        try:
            od_balances = _sum_od_balances(load_pairs(data_dir, city))
        except (FileNotFoundError, ValueError, OSError, RuntimeError, KeyError):
            od_balances = {}
        if od_balances:
            zones = [
                replace(
                    zone,
                    production=od_balances.get(zone.zone_id, (0.0, 0.0))[0],
                    attraction=od_balances.get(zone.zone_id, (0.0, 0.0))[1],
                )
                for zone in zones
            ]
    return zones


def load_pairs(data_dir: Path, city: str) -> list[tuple[int, int, float]]:
    """Загружает пары OD: (orig_zone, dest_zone, trips)."""
    data_dir = Path(data_dir)
    path = data_dir / f"{city}_od_pairs.parquet"
    if not path.is_file():
        raise FileNotFoundError(
            f"Не найден {path}. Пакет строится из OD-артефактов каталога данных."
        )
    pairs: list[tuple[int, int, float]] = []
    for row in _read_rows(path, str(path)):
        try:
            orig = int(row["orig_zone"])
            dest = int(row["dest_zone"])
            trips = float(row.get("trips") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(trips) and trips > 0:
            pairs.append((orig, dest, trips))
    return pairs


def load_purposes(data_dir: Path, city: str) -> list[dict]:
    """Загружает цели поездок; при отсутствии артефакта возвращает пустой список."""
    data_dir = Path(data_dir)
    path = data_dir / f"{city}_od_purposes.parquet"
    if not path.is_file():
        return []
    purposes: list[dict] = []
    for row in _read_rows(path, str(path)):
        purpose = str(row.get("purpose") or "").strip()
        if not purpose:
            continue
        purposes.append(
            {
                "purpose": purpose,
                "trips": float(row.get("trips") or 0.0),
                "share": float(row.get("share") or 0.0),
            }
        )
    return purposes


def build_demand(
    city: str,
    city_title: str,
    zones: list[_Zone],
    pairs: list[tuple[int, int, float]],
) -> dict:
    """Собирает demand.json: pts в порядке зон, OD-индексы с оценкой времени."""
    index = {zone.zone_id: i for i, zone in enumerate(zones)}
    pts = [
        [
            round(zone.lon, 6),
            round(zone.lat, 6),
            round(zone.production, 2),
            round(zone.attraction, 2),
        ]
        for zone in zones
    ]
    od: list[list] = []
    for orig, dest, trips in pairs:
        i = index.get(orig)
        j = index.get(dest)
        if i is None or j is None:
            continue
        dist_km = _haversine_km(
            zones[i].lon, zones[i].lat, zones[j].lon, zones[j].lat
        )
        time_s = max(_MIN_TIME_S, round(3600.0 * dist_km / _DEMAND_SPEED_KMH))
        od.append([i, j, round(trips, 3), time_s])
    source = (
        f"{city} OD artefacts ({len(pts)} zones, {len(od)} pairs); "
        f"travel time estimated at {_DEMAND_SPEED_KMH:.0f} km/h crow-flies"
    )
    return {
        "city": city_title or city,
        "source": source,
        "pts": pts,
        "od": od,
    }


def build_districts(zones: list[_Zone]) -> dict:
    """Собирает districts.json: по точке-месту на зону (x=lon, y=lat)."""
    places = [
        {
            "n": f"z{zone.zone_id}",
            "r": 6,
            "x": round(zone.lon, 6),
            "y": round(zone.lat, 6),
            "s": 1,
            "q": "",
            "arms": "",
        }
        for zone in zones
    ]
    return {"places": places}


def _direction_run(
    city_slug: str,
    route: RouteData,
    direction_idx: int,
    stops_lonlat: list[list[float]],
    mode: str,
) -> dict:
    """Строит одну строку (направление) в baseline.json."""
    peak = MODE_PEAK_MIN.get(mode, 10)
    speed = MODE_SPEED_KMH.get(mode, 25)
    headways = [max(60, peak * 6), peak, peak, peak, peak]
    trips = max(1, round(1440 / peak))
    cum_t: list[float] = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(stops_lonlat, stops_lonlat[1:]):
        segment_s = 3600.0 * _haversine_km(lon1, lat1, lon2, lat2) / speed + _DWELL_S
        cum_t.append(cum_t[-1] + segment_s)
    return {
        "id": f"{city_slug}-{mode}-{route.route_id}-{direction_idx + 1}",
        "name": route.name or str(route.route_id),
        "mode": mode,
        "headways": headways,
        "stops": stops_lonlat,
        "cumT": [round(value) for value in cum_t],
        "trips": trips,
    }


def build_baseline(city_slug: str, routes: list[RouteData]) -> dict:
    """Собирает baseline.json из направлений маршрутов с координатными остановками."""
    lines: list[dict] = []
    for route in routes:
        mode = route.route_type.value if route.route_type else "bus"
        for direction_idx, direction in enumerate(route.directions):
            stops_lonlat: list[list[float]] = []
            for stop in direction.stops:
                coordinate = stop.coordinate()
                if coordinate is None:
                    continue
                latitude, longitude = coordinate
                stops_lonlat.append([round(longitude, 6), round(latitude, 6)])
            if len(stops_lonlat) < 2:
                continue
            lines.append(_direction_run(city_slug, route, direction_idx, stops_lonlat, mode))
    generated = (
        f"wikiroutes {city_slug} pipeline; headways/trips estimated from mode defaults"
    )
    return {
        "generated": generated,
        "lines": lines,
    }


def build_model(city: str, route_count: int) -> dict:
    """Собирает model.json: поверхностные параметры сценария для оркестратора."""
    return {
        "city": city,
        "mobility": {
            "noCar": 0.15,
            "twoWheelShare": 0.02,
            "twoWheelSpeed": 22.0,
            "twoWheelReachM": 15000.0,
            "twoWheelPerKm": 0.03,
        },
        "rest": {
            "baseSpeed": 4.8,
            "contSpeed": 5.0,
            "accessS": 240.0,
            "waitS": 120.0,
            "circuity": 1.35,
            "note": "defaults",
        },
        "restMeasured": {},
        "car": {"costPerKm": 0.17, "parkEur": 3.0, "parkingS": 120.0},
        "votSPerEur": 300,
        "signs": {},
        "credits": {
            "generatedBy": "wikiroutes pack export",
            "routes": route_count,
        },
        "sources": {},
    }


def build_purposes(zones: list[_Zone], purposes: list[dict]) -> dict:
    """Собирает purposes.bin.json: разбивка ценностей по зонам (float32 LE, base64).

    Каждая цель получает вектор по зонам: ``trips(цель) * production(зона) / sum(production)``.
    """
    productions = [max(0.0, zone.production) for zone in zones]
    production_sum = sum(productions) or 1.0
    layers: list[dict] = []
    commute_base: list[float] = []
    for purpose in purposes:
        trips = max(0.0, purpose["trips"])
        values = [trips * production / production_sum for production in productions]
        packed = struct.pack("<%df" % len(values), *values)
        encoded = base64.b64encode(packed).decode("ascii")
        layers.append(
            {
                "t": purpose["purpose"],
                "n": sum(1 for value in values if value > 0.0),
                "od": encoded,
                "out": encoded,
                "ret": encoded,
                "baseT": 900,
            }
        )
        commute_base.append(900)
    return {
        "v": 2,
        "layers": layers,
        "commuteBaseT": commute_base,
    }


def build_demand_streets(zones: list[_Zone]) -> dict:
    """Собирает demand-streets.json: уникальные рёбра колец зон с суммарным спросом.

    Каждый общий сегмент границы двух зон становится одним сегментом
    ``MultiLineString``; ``d`` — сумма ``(production + attraction) / 1000``
    для инцидентных зон (в тысячах).
    """
    edge_zones: dict[tuple[tuple[float, float], tuple[float, float]], list[int]] = {}
    for zone_index, zone in enumerate(zones):
        for a, b in zip(zone.ring, zone.ring[1:]):
            key_a = (round(a[0], 6), round(a[1], 6))
            key_b = (round(b[0], 6), round(b[1], 6))
            if key_a == key_b:
                continue
            key = tuple(sorted((key_a, key_b)))
            edge_zones.setdefault(key, []).append(zone_index)

    features: list[dict] = []
    for fid, (key, zone_indexes) in enumerate(sorted(edge_zones.items())):
        demand = sum(
            (zones[zone_index].production + zones[zone_index].attraction) / 1000.0
            for zone_index in zone_indexes
        )
        features.append(
            {
                "id": fid,
                "type": "Feature",
                "properties": {"fid": fid, "d": round(demand, 3)},
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[list(key[0]), list(key[1])]],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_pack_files(
    city: str,
    city_slug: str,
    city_title: str,
    routes: list[RouteData],
    data_dir: Path,
    version: int = 1,
) -> dict[str, bytes]:
    """Собирает все файлы pack (роль -> байты контента)."""
    data_dir = Path(data_dir)
    zones = load_zones(data_dir, city)
    pairs = load_pairs(data_dir, city)
    purposes = load_purposes(data_dir, city)

    files: dict[str, bytes] = {
        "demand.json": build_demand(city, city_title, zones, pairs),
        "districts.json": build_districts(zones),
        "baseline.json": build_baseline(city_slug, routes),
        "model.json": build_model(city_title or city, len(routes)),
        "demand-streets.json": build_demand_streets(zones),
    }
    if purposes:
        files["purposes.bin.json"] = build_purposes(zones, purposes)

    payload: dict[str, bytes] = {}
    for role in PACK_ROLES:
        if role in files:
            payload[role] = _dump_json(files[role])

    contents = b"".join(payload[role] for role in PACK_ROLES if role in payload)
    manifest = {
        "city": str(city_slug),
        "version": max(1, int(version)),
        "files": {role: len(payload[role]) for role in payload},
        "sha256": hashlib.sha256(contents).hexdigest(),
        "total": sum(len(payload[role]) for role in payload),
    }
    payload["manifest.json"] = _dump_json(manifest)
    return payload


def _dump_json(value: object) -> bytes:
    """Сериализует в компактный UTF-8 JSON."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def export_takt_pack(config: CliConfig, result: PipelineResult) -> Path | None:
    """Экспортирует Takt city pack в ``config.pack_out`` (или текущий каталог).

    Возвращает путь к manifest или ``None`` при ошибке сборки.
    """
    if not config.pack:
        return None
    pack_out = Path(config.pack_out or ".")
    pack_data = Path(config.pack_data or DEFAULT_DATA_DIR)
    pack_out.mkdir(parents=True, exist_ok=True)

    city_slug = result.city_slug or config.city_input
    city = city_slug
    city_title = result.city_title or city_slug
    try:
        payload = build_pack_files(
            city=city,
            city_slug=city_slug,
            city_title=city_title,
            routes=list(result.ok_routes),
            data_dir=pack_data,
            version=config.pack_version,
        )
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        print(f"  ⚠️  Takt pack пропущен: {exc}")
        return None

    for role, content in payload.items():
        (pack_out / role).write_bytes(content)

    manifest_path = pack_out / "manifest.json"
    manifest = json.loads(payload["manifest.json"].decode("utf-8"))
    print(
        f"  ✅ Takt pack: {manifest_path} "
        f"(version={manifest['version']}, total={manifest['total']:,} B)"
    )

    return manifest_path


def run_takt_model(pack_dir: Path, output_path: Path | None = None) -> dict | None:
    """Запускает takt-model simulation на собранном pack.

    Читает pack-файлы из *pack_dir*, запускает моделирование пассажиропотока
    и возвращает сводный результат. При *output_path* записывает JSON-отчёт.

    Возвращает ``None``, если takt-model недоступен или возникла ошибка.
    """
    try:
        from takt import (
            build_network_from_baseline,
            simulate,
            detect_city,
        )
    except ImportError:
        print(f"  ⚠️  takt-model не установлен; simulation пропущена")
        return None

    try:
        pack_dir = Path(pack_dir)
        city = detect_city(pack_dir) or pack_dir.name
        print(f"  🚆 takt-model: city={city}, pack={pack_dir}")

        network = build_network_from_baseline(city, root=pack_dir)
        result = simulate(network, city=city, root=pack_dir, sample_size=2000)

        report = {
            "city": city,
            "riders_per_weekday": result.riders_per_weekday,
            "satisfaction_pct": result.satisfaction_pct,
            "walk_reach_pct": result.walk_reach_pct,
            "change_lines_per_weekday": result.change_lines_per_weekday,
            "connected_districts": result.connected_districts,
            "connected_towns": result.connected_towns,
            "line_count": len(result.lines),
            "riders": {
                l.id: round(l.riders_per_weekday, 0) for l in result.lines
            },
            "lines": [
                {
                    "id": l.id,
                    "name": l.name,
                    "mode": l.mode,
                    "length_km": round(l.length_km, 2),
                    "riders_per_weekday": round(l.riders_per_weekday, 0),
                    "peak_load_factor": round(l.peak_load_factor, 2),
                    "fleet": l.fleet,
                }
                for l in result.lines
            ],
        }

        if output_path is not None:
            import json as _json
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                _json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  ✅ takt-model отчёт: {output_path}")

        return report
    except Exception as exc:
        print(f"  ⚠️  takt-model ошибка: {exc}")
        return None


__all__ = [
    "build_baseline",
    "build_demand",
    "build_demand_streets",
    "build_districts",
    "build_model",
    "build_pack_files",
    "build_purposes",
    "export_takt_pack",
    "load_pairs",
    "load_purposes",
    "load_zones",
    "run_takt_model",
]