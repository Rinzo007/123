"""Зоны из внешнего набора точек спроса.

Зачем: сетка TAZ даёт сотни зон на город, а эталонные наборы держат
десятки тысяч точек с населением и занятостью в каждой. На Киеве в городском
боксе 11 854 точки против 504 зон сетки - разница 23 раза, и на такой
детальности уже видно, где именно живут поездки.

Формат принимается в виде списка объектов с полями `id`, `location`
([lon, lat]), `residents`, `jobs`. Имена полей не выдумываются: у эталонного
набора `residents` - это население точки, а `jobs` - занятость, и сумма
каждого совпадает с заявленным итогом.

Существенно, что имена `residents`/`jobs` в разных документах одного и того же
бандла значат разное: в config.json величина 2 513 143 названа population, а на
странице реестра - Total Modeled Demand при Total Population 5 079 053. Поэтому
загрузчик берёт числа как есть, не переименовывая их, и итог записывает в
provenance - решение об интерпретации остаётся за вызывающим.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .city import DemandZone
from .geo import Point
from .projection import project_wgs84_point


@dataclass(frozen=True, slots=True)
class DemandPointSource:
    """Набор точек спроса: путь и сводка о том, что в нём лежит."""

    path: Path
    count: int
    inside_count: int
    total_residents: float
    total_jobs: float
    zero_residents: int
    zero_jobs: int

    def to_provenance(self) -> dict:
        return {
            "file": self.path.name,
            "pointsTotal": self.count,
            "pointsInsideStudyArea": self.inside_count,
            "residentsInside": round(self.total_residents, 1),
            "jobsInside": round(self.total_jobs, 1),
            "pointsWithoutResidents": self.zero_residents,
            "pointsWithoutJobs": self.zero_jobs,
        }


def load_demand_points(
    path: str | Path,
    *,
    origin_lon: float,
    origin_lat: float,
    inside: "callable | None" = None,
) -> tuple[tuple[DemandZone, ...], DemandPointSource]:
    """Читает точки спроса и переводит их в локальные метры.

    `inside` отсекает точки вне области исследования. Без него берутся все:
    набор может быть шире города, и молчаливое усечение выглядело бы как
    полный охват.
    """
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    entries = payload["points"] if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"no points in {source_path}")

    zones: list[DemandZone] = []
    total_residents = 0.0
    total_jobs = 0.0
    zero_residents = 0
    zero_jobs = 0
    kept = 0
    for entry in entries:
        location = entry.get("location")
        if not location or len(location) < 2:
            continue
        lon, lat = float(location[0]), float(location[1])
        if inside is not None and not inside(lon, lat):
            continue
        residents = float(entry.get("residents") or 0.0)
        jobs = float(entry.get("jobs") or 0.0)
        if residents <= 0:
            zero_residents += 1
        if jobs <= 0:
            zero_jobs += 1
        point = project_wgs84_point(
            Point(lon, lat), origin_lon=origin_lon, origin_lat=origin_lat
        )
        kept += 1
        total_residents += residents
        total_jobs += jobs
        zones.append(
            DemandZone(
                id=str(entry.get("id") or f"pt{kept}"),
                centroid_x=point.x,
                centroid_y=point.y,
                population=residents,
                jobs=jobs,
            )
        )

    if not zones:
        raise ValueError(f"no points inside the study area: {source_path}")

    return tuple(zones), DemandPointSource(
        path=source_path,
        count=len(entries),
        inside_count=kept,
        total_residents=total_residents,
        total_jobs=total_jobs,
        zero_residents=zero_residents,
        zero_jobs=zero_jobs,
    )
