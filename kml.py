"""Экспорт маршрутной сети, остановок и аналитики в KML 2.2."""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .compat import stop_name
from .constants import BASE_URL
from .support import fix_stop_coord, fmt_curv_str, type_label

logger = logging.getLogger("wikiroutes.kml")

KML_LINE_COLOR = {
    "trolleybus": "ff0000ff",
    "tram": "ff0088ff",
    "water": "ffff0000",
    "bus": "ff00aa00",
    "minibus": "ff00d7ff",
    "metro": "ff800080",
    "train": "ff003264",
    "funicular": "ffcc66ff",
    "cable": "ffffff00",
}
KML_STOP_COLOR = {
    "trolleybus": "ff0000cc",
    "tram": "ff0066cc",
    "water": "ffcc0000",
    "bus": "ff008800",
    "minibus": "ff00aacc",
    "metro": "cc800080",
    "train": "cc003264",
    "funicular": "cccc66ff",
    "cable": "ccffff00",
}
KML_STOP_ICON = {
    "trolleybus": "https://maps.google.com/mapfiles/kml/shapes/bus.png",
    "tram": "https://maps.google.com/mapfiles/kml/shapes/rail.png",
    "water": "https://maps.google.com/mapfiles/kml/shapes/ferry.png",
    "bus": "https://maps.google.com/mapfiles/kml/shapes/bus.png",
    "minibus": "https://maps.google.com/mapfiles/kml/shapes/bus.png",
    "metro": "https://maps.google.com/mapfiles/kml/shapes/rail.png",
    "train": "https://maps.google.com/mapfiles/kml/shapes/rail.png",
    "funicular": "https://maps.google.com/mapfiles/kml/shapes/rail.png",
    "cable": "https://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
}
KML_FOLDER_LABELS = {
    "trolleybus": "🚎 Троллейбусы",
    "tram": "🚊 Трамваи",
    "water": "⛴ Водный транспорт",
    "bus": "🚌 Автобусы",
    "minibus": "🚐 Маршрутные такси",
    "metro": "🚇 Метро",
    "train": "🚆 Поезда",
    "funicular": "🚞 Фуникулёры",
    "cable": "🚠 Канатные дороги",
}


def _valid_coord(lat: Any, lon: Any) -> tuple[float, float] | None:
    """Возвращает валидную пару WGS84 или ``None``."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except TypeError, ValueError:
        return None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    return lat_f, lon_f


def build_kml(
    routes: Sequence[Any],
    city_title: str,
    path: str | Path,
    include_stops: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    ghs_stats: Mapping[Any, Any] | None = None,
    ghs_buffer: float = 0.0,
    ghs_dir_stats: Mapping[Any, Any] | None = None,
    poi_stats: Mapping[Any, Any] | None = None,
    poi_buffer: float = 0.0,
    poi_dir_stats: Mapping[Any, Any] | None = None,
    generated: Sequence[Mapping[str, Any]] | None = None,
    built_s_stats: Mapping[Any, Any] | None = None,
    built_s_meta: Mapping[str, Any] | None = None,
    built_s_dir_stats: Mapping[Any, Any] | None = None,
    overture_stats: Mapping[Any, Any] | None = None,
    overture_meta: Mapping[str, Any] | None = None,
    overture_dir_stats: Mapping[Any, Any] | None = None,
) -> str | Path | None:
    """Создаёт KML 2.2 и сохраняет его по указанному пути.

    Метрики GHS, BUILT-S, POI и Overture при наличии записываются
    на уровень отдельного направления маршрута.
    """
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = f"{city_title} — маршрутная сеть"
    ET.SubElement(doc, "description").text = (
        f"Источник: {BASE_URL}\n"
        f"Город: {city_title}\n"
        f"Маршрутов: {len(routes)}\n"
        f"Дата: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}"
    )

    def line_style(sid: str, color: str, width: str) -> None:
        st = ET.SubElement(doc, "Style", id=sid)
        ls = ET.SubElement(st, "LineStyle")
        ET.SubElement(ls, "color").text = color
        ET.SubElement(ls, "width").text = width

    def icon_style(sid: str, color: str, scale: str, href: str) -> None:
        st = ET.SubElement(doc, "Style", id=sid)
        ic = ET.SubElement(st, "IconStyle")
        ET.SubElement(ic, "color").text = color
        ET.SubElement(ic, "scale").text = scale
        ET.SubElement(ET.SubElement(ic, "Icon"), "href").text = href

    def get_stat(
        dir_stats: Mapping[Any, Any] | None,
        route_stats: Mapping[Any, Any] | None,
        route_id: int,
        di: int,
    ) -> Any:
        if dir_stats is not None and (route_id, di) in dir_stats:
            return dir_stats[(route_id, di)]
        if route_stats is not None and route_id in route_stats:
            return route_stats[route_id]
        return None

    present_types = {
        getattr(r.route_type, "value", r.route_type)
        for r in routes
        if not r.error and r.directions
    }
    for rt in present_types:
        if rt in KML_LINE_COLOR:
            line_style(rt, KML_LINE_COLOR[rt], "3")
    for rt in present_types:
        if rt in KML_STOP_COLOR:
            icon_style(
                f"stop_{rt}",
                KML_STOP_COLOR[rt],
                "0.6",
                KML_STOP_ICON.get(
                    rt,
                    "https://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
                ),
            )

    folders = {}
    for rt in sorted(present_types):
        folder = ET.SubElement(doc, "Folder")
        ET.SubElement(folder, "name").text = KML_FOLDER_LABELS.get(rt, type_label(rt))
        folders[rt] = folder

    for rd in sorted(
        routes, key=lambda r: (getattr(r.route_type, "value", r.route_type), r.name)
    ):
        if rd.error or not rd.directions:
            continue
        rt_key = getattr(rd.route_type, "value", rd.route_type)
        if rt_key not in folders:
            continue

        for di, d in enumerate(rd.directions):
            if not d.coords:
                continue
            valid_coords = [
                point
                for point in (_valid_coord(*coord) for coord in d.coords)
                if point is not None
            ]
            if len(valid_coords) < 2:
                continue
            pm = ET.SubElement(folders[rt_key], "Placemark")
            name_suffix = " (Идея)" if rd.is_idea else ""
            ET.SubElement(
                pm, "name"
            ).text = f"{rd.name}{name_suffix} ({'туда' if di == 0 else 'обратно'})"

            lines = [
                f"Маршрут: {rd.name}",
                f"Тип: {type_label(rd.route_type)}",
                f"Источник: {'Идея' if rd.is_idea else 'Реальный маршрут'}",
                f"Направление: {d.name or '—'}",
                f"Длина: {d.km:.1f} км",
                f"Коэфф. непрямолинейности: {fmt_curv_str(d.curvilinearity)}",
                f"Остановок: {len(d.stops)}",
                f"Ссылка: {rd.url}",
            ]
            if rd.is_idea:
                lines.append(f"Автор: {rd.company}")
                rating_str = rd.transport_class
                if rating_str and rating_str != "()":
                    lines.append(f"Рейтинг: {rating_str}")

            st = get_stat(ghs_dir_stats, ghs_stats, rd.route_id, di)
            if st:
                lines.append(
                    f"Объём зданий (буфер {ghs_buffer:.0f} м): "
                    f"{st.volume_m3 / 1e3:.1f} тыс. м³"
                )
            st = get_stat(built_s_dir_stats, built_s_stats, rd.route_id, di)
            if st:
                buf = built_s_meta.get("buffer_m", 0) if built_s_meta else 0
                lines.append(
                    f"Поверхность зданий (буфер {buf:.0f} м): "
                    f"{st.surface_m2 / 1e3:.1f} тыс. м²"
                )
            st = get_stat(overture_dir_stats, overture_stats, rd.route_id, di)
            if st and getattr(st, "total_area_m2", 0) > 0:
                buf = overture_meta.get("buffer_m", 0) if overture_meta else 0
                lines.append(
                    f"Площадь объектов Overture (буфер {buf:.0f} м): "
                    f"{st.total_area_m2 / 1e3:.1f} тыс. м²"
                )
            st = get_stat(poi_dir_stats, poi_stats, rd.route_id, di)
            if st:
                lines.append(
                    f"POI в буфере {poi_buffer:.0f} м: "
                    f"{st.count} точек, value={st.total_value:.1f}"
                )

            ET.SubElement(pm, "description").text = "\n".join(lines)
            ET.SubElement(pm, "styleUrl").text = f"#{rt_key}"
            ls = ET.SubElement(pm, "LineString")
            ET.SubElement(ls, "tessellate").text = "1"
            ET.SubElement(ls, "coordinates").text = "\n".join(
                f"{lon:.7f},{lat:.7f},0" for lat, lon in valid_coords
            )

            if include_stops:
                for si, s in enumerate(d.stops):
                    c = fix_stop_coord(s, si, bbox)
                    if not c or _valid_coord(c[0], c[1]) is None:
                        continue
                    sp = ET.SubElement(folders[rt_key], "Placemark")
                    ET.SubElement(sp, "name").text = stop_name(s) or "?"
                    ET.SubElement(sp, "styleUrl").text = f"#stop_{rt_key}"
                    ET.SubElement(
                        ET.SubElement(sp, "Point"), "coordinates"
                    ).text = f"{c[1]:.7f},{c[0]:.7f},0"

    if generated:
        st = ET.SubElement(doc, "Style", id="gen")
        ls = ET.SubElement(st, "LineStyle")
        ET.SubElement(ls, "color").text = "fff020a0"
        ET.SubElement(ls, "width").text = "3"
        fg = ET.SubElement(doc, "Folder")
        ET.SubElement(fg, "name").text = "🧪 Сгенерированные маршруты (объём застройки)"
        for g in generated:
            pm = ET.SubElement(fg, "Placemark")
            ET.SubElement(pm, "name").text = f"Г{g['n']}: {g['from']} → {g['to']}"
            ET.SubElement(pm, "description").text = "\n".join(
                [
                    "Метод: Якимов М.Р., объём застройки вместо пассажиропотока",
                    f"Длина: {g['length_km']:.1f} км",
                    f"Остановок: {g['stops']}",
                    f"Прямолинейность: {fmt_curv_str(g['curvilinearity'])}",
                    f"Объём коридора: {g['volume'] / 1e3:.1f} тыс. м³",
                    f"Статус: {g['status']}",
                    f"Трасса: {g['chain']}",
                ]
            )
            ET.SubElement(pm, "styleUrl").text = "#gen"
            lsg = ET.SubElement(pm, "LineString")
            ET.SubElement(lsg, "tessellate").text = "1"
            ET.SubElement(lsg, "coordinates").text = "\n".join(
                f"{lon:.7f},{lat:.7f},0" for lat, lon in g["pts"]
            )

    if hasattr(ET, "indent"):
        ET.indent(kml, space="  ")

    try:
        with Path(path).open("w", encoding="utf-8") as out_f:
            out_f.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + ET.tostring(kml, encoding="unicode")
            )
    except OSError as e:
        logger.warning("KML: %s", e)
        return None
    return path
