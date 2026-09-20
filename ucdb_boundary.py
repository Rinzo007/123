"""Границы городов из GHS-UCDB GeoPackage.

GHS ``UCDB`` (Urban Centre Database) содержит урбанизированные центры всего
мира с полигональными границами и названиями. Этот модуль читает одно или
несколько изданий UCDB (``GHS_STAT_UCDB2015MT_GLOBE_R2019A_V1_2.gpkg``,
``GHS_UCDB_GLOBE_R2024A.gpkg``), подбирает город по названию и возвращает
границу в WGS84. Среди всех найденных в любом файле записей всегда выбирается
наибольшая по площади (омонимы разрешаются по близости центроида к маршрутам).

Геометрия в файлах хранится в ``EPSG:4326`` (координаты ``(lon, lat)``),
что согласуется с остальными границами в проекте.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .common import union_all

logger = logging.getLogger("wikiroutes.ucdb")

# Файл лога пропусков городов (по образцу osm_boundary_errors.log).
_UCDB_ERR_LOG = "ucdb_boundary_errors.log"

# Официальные англоязычные названия в UCDB для slug'ов проекта
# (транслитерация с рус./укр.). Ключ — нормализованный slug; значение —
# имена, под которыми город хранится в GHS-UCDB (проверено по файлам).
# Города, которых в UCDB нет вообще, в таблицу не попадают.
_UCDB_SLUG_ALIASES: dict[str, tuple[str, ...]] = {
    "altmetievsk": ("Almetyevsk",),
    "antracit": (),
    "arhangelsk": ("Arkhangelsk",),
    "artemovsk": ("Artemivsk",),
    "astrahan": ("Astrakhan",),
    "belaya-cerkov": ("Bila Tserkva",),
    "belgorod-dnestrovsky": ("Bilhorod-Dnistrovskyi",),
    "beltsy": ("Balti",),
    "bendery": ("Bender",),
    "berdyansk": ("Berdiansk",),
    "blagoveschensk": ("Blagoveshchensk",),
    "bobrujsk": ("Babruysk",),
    "borisov": ("Barysaw",),
    "cherkassy": ("Cherkasy",),
    "chernigov": ("Chernihiv",),
    "chernovtsy": ("Chernivtsi",),
    "dnepropetrovsk": ("Dnipro",),
    "druzhkovka": ("Druzhkivka",),
    "dzerzhynsk": ("Dzerzhinsk",),
    "ekaterinburg": ("Yekaterinburg",),
    "elets": ("Yelets",),
    "enakievo": ("Yenakiieve",),
    "erevan": ("Yerevan",),
    "evpatoriya": ("Yevpatoriya",),
    "gomel": ("Homel",),
    "gorlovka": ("Horlivka",),
    "grodno": ("Hrodna",),
    "groznyj": ("Grozny",),
    "gyandzha": ("Ganja",),
    "gymree": ("Gyumri",),
    "habarovsk": ("Khabarovsk",),
    "herson": ("Kherson",),
    "hmelnitskiy": ("Khmelnytskyi",),
    "hudzant": ("Khujand",),
    "ivano-frankovsk": ("Ivano-Frankivsk",),
    "joshkar-ola": ("Yoshkar-Ola",),
    "kadievka": ("Kadiivka",),
    "kamenets-podolskiy": ("Kamianets-Podilskyi",),
    "kamensk-uralskiy": ("Kamensk-Uralsky",),
    "kamychin": ("Kamyshin",),
    "kharkov": ("Kharkiv",),
    "kiev": ("Kyiv",),
    "kirovograd": ("Kropyvnytskyi",),
    "kishinew": ("Chisinau",),
    "klaipeda": ("Klaipeda",),
    "komsomolsk-na-amure": ("Komsomolsk-on-Amur",),
    "konstantinovka": ("Kostiantynivka",),
    "kremenchug": ("Kremenchuk",),
    "krivoy-rog": ("Kryvyi Rih",),
    "liepaya": ("Liepaja",),
    "lisichansk": ("Lysychansk",),
    "lugansk": ("Luhansk",),
    "lvov": ("Lviv",),
    "mahachkala": ("Makhachkala",),
    "maikop": ("Maykop",),
    "makeevka": ("Makiivka",),
    "mogilev": ("Mahilyow",),
    "msk": ("Moscow",),
    "n-novgorod": ("Nizhny Novgorod",),
    "naberejnie-chelni": ("Naberezhnye Chelny",),
    "nikolaev": ("Mykolaiv",),
    "odessa": ("Odesa",),
    "oleksandriya": ("Oleksandriia",),
    "orel": ("Oryol",),
    "panevezhis": ("Panevezys",),
    "pavlograd": ("Pavlohrad",),
    "petropavlovsk": ("Petropavlovsk-Kamchatsky",),
    "rostov": ("Rostov-on-Don",),
    "rostov-na-donu": ("Rostov-on-Don",),
    "rovno": ("Rivne",),
    "rubcovsk": ("Rubtsovsk",),
    "semipalatinsk": ("Semey",),
    "sergiev-posad": ("Sergiyev Posad",),
    "serpuhov": ("Serpukhov",),
    "severodonetsk": ("Sievierodonetsk",),
    "shahty": ("Shakhty",),
    "shaulyai": ("Siauliai",),
    "slavyansk": ("Sloviansk",),
    "spb": ("Saint Petersburg",),
    "sumgait": ("Sumgayit",),
    "tallin": ("Tallinn",),
    "ternopol": ("Ternopil",),
    "ulianovsk": ("Ulyanovsk",),
    "usolye-sibirskoe": ("Usolye-Sibirskoye",),
    "uzhgorod": ("Uzhhorod",),
    "velikie-luki": ("Velikiye Luki",),
    "velikiy-novgorod": ("Veliky Novgorod",),
    "vilnus": ("Vilnius",),
    "vinnica": ("Vinnytsia",),
    "volzhsky": ("Volzhskiy",),
    "yuzhno-sahalinsk": ("Yuzhno-Sakhalinsk",),
    "zaporozhye": ("Zaporizhzhia",),
    "zhitomir": ("Zhytomyr",),
    "zhukovski": ("Zhukovsky",),
}


def _ensure_ucdb_err_log() -> None:
    """Один раз прикрепляет WARNING-файловый логгер к ``wikiroutes.ucdb``.

    В файл пишутся пропуски городов («Город не найден в GHS-UCDB»);
    ошибки чтения gpkg тоже попадают на WARNING-уровне.
    """
    if getattr(logger, "_ucdb_err_file_attached", False):
        return
    try:
        handler = logging.FileHandler(_UCDB_ERR_LOG, encoding="utf-8")
    except OSError:
        return
    handler.setLevel(logging.WARNING)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger._ucdb_err_file_attached = True  # type: ignore[attr-defined]


def _norm(value: Any) -> str:
    """Нормализует название для сравнения (без диакритики и пунктуации)."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-zа-яё0-9]+", " ", text.lower()).strip()


def _candidate_names(primary: str) -> list[str]:
    """Варианты поиска: сам slug и его транслитерация/латинская форма."""
    names = [primary]
    translit = {
        "ж": "zh", "ч": "ch", "ш": "sh", "щ": "shch",
        "ц": "ts", "х": "kh", "ъ": "", "ь": "", "ю": "yu", "я": "ya",
        "й": "y", "ё": "e", "э": "e",
    }
    lowered = primary.lower().replace("-", " ")
    latin = "".join(translit.get(ch, ch) for ch in lowered)
    if latin and _norm(latin) != _norm(primary):
        names.append(latin)
    return names


# Колонки с названием города в разных изданиях UCDB (по убыванию приоритета).
_UCDB_NAME_COLUMNS: tuple[str, ...] = (
    "UC_NM_MN",       # GHS_STAT_UCDB2015MT_GLOBE_R2019A_V1_2
    "GC_UCN_MAI_2025"  # GHS_UCDB_GLOBE_R2024A
)


def _read_ucdb_frame(gpkg_path: str | Path, layer: str | None) -> Any:
    """Читает первый слой GeoPackage UCDB; None при ошибке чтения."""
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover
        logger.warning("geopandas недоступен для чтения UCDB: %s", exc)
        return None
    try:
        if layer is None:
            layers = gpd.list_layers(str(gpkg_path))
            read_layer = layers["name"].iloc[0] if len(layers) else None
        else:
            read_layer = layer
        return gpd.read_file(str(gpkg_path), layer=read_layer)
    except Exception as exc:  # noqa: BLE001 — стойкость к повреждённому gpkg
        logger.warning("Не удалось прочитать UCDB %s: %s", gpkg_path, exc)
        return None


def _resolve_name_column(gpkg_path: str | Path, frame: Any) -> str | None:
    """Колонка названия города в frame (искали по известным именам)."""
    col = next(
        (col for col in _UCDB_NAME_COLUMNS if col in frame.columns), None
    )
    if col is None:
        logger.warning(
            "В UCDB %s не найдена колонка с названием города "
            "(искали %s)",
            gpkg_path,
            ", ".join(_UCDB_NAME_COLUMNS),
        )
    return col


def _iter_geom_results(
    frame: Any,
    name_column: str,
    wanted: set[str],
) -> list[tuple[Any, Any]]:
    """Строки (`название`, геометрия) для совпадений по нормализованным именам."""
    matched = frame[
        frame[name_column].astype(str).map(lambda v: _norm(v) in wanted)
    ]
    if matched.crs is not None and matched.crs != "EPSG:4326":
        matched = matched.to_crs(4326)
    results: list[tuple[Any, Any]] = []
    for idx in range(len(matched)):
        raw_name = matched.iloc[idx][name_column]
        geom = matched.iloc[idx].geometry
        if geom is None or geom.is_empty:
            continue
        results.append((raw_name, geom))
    return results


def _read_layer_hits(
    gpkg_path: str | Path,
    names: Sequence[str],
    *,
    layer: str | None = None,
    name_column: str | None = None,
) -> list[tuple[Any, Any]]:
    """Возвращает `(название, геометрия в WGS84)` для всех совпадений имен.

    Слой и колонка названий определяются автоматически по файлу (в разных
    изданиях UCDB они называются по-разному), если не заданы явно.
    """
    if not names:
        return []

    frame = _read_ucdb_frame(gpkg_path, layer)
    if frame is None:
        return []

    if name_column is None:
        name_column = _resolve_name_column(gpkg_path, frame)
        if name_column is None:
            return []

    wanted = {_norm(n) for n in names if _norm(n)}
    return _iter_geom_results(frame, name_column, wanted)


def _repr_point(geom: Any) -> tuple[float, float]:
    """Центроид полигона в WGS84 как ``(lon, lat)``."""
    centroid = geom.centroid
    try:
        return float(centroid.x), float(centroid.y)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _route_centroid(coords: Iterable[Sequence[float]]) -> tuple[float, float] | None:
    """Средняя точка всех координат маршрутов ``(lat, lon)`` -> ``(lon, lat)``."""
    lons: list[float] = []
    lats: list[float] = []
    for c in coords:
        if len(c) < 2:
            continue
        lats.append(float(c[0]))
        lons.append(float(c[1]))
    if not lons:
        return None
    return sum(lons) / len(lons), sum(lats) / len(lats)


def _geodesic_area_km2(geom: Any) -> float:
    """Площадь полигона в км² по геодезическому контуру (WGS84)."""
    try:
        from pyproj import Geod

        return abs(Geod(ellps="WGS84").geometry_area_perimeter(geom)[0]) / 1e6
    except Exception:  # noqa: BLE001 — fallback площади при сбое геодезики
        try:
            from pyproj import Transformer
            from shapely.ops import transform

            x = geom.centroid.x
            zone = int((x + 180) / 6) + 1
            proj = Transformer.from_crs(
                "EPSG:4326", f"EPSG:{32600 + zone}", always_xy=True
            )
            return abs(transform(proj.transform, geom).area) / 1e6
        except Exception:  # noqa: BLE001 — иначе площадь в квадратных градусах
            return abs(geom.area)


def _pick_candidate(
    hits: list[tuple[Any, Any]],
    route_centroid: tuple[float, float] | None,
) -> tuple[Any, Any] | None:
    """Выбирает лучшего кандидата.

    Всегда предпочитается самый крупный по площади (среди всех доступных
    записей/лет); при равной площади — ближайший к маршрутам (защита от
    омонимов, когда у города несколько одноимённых центров).
    """
    if not hits:
        return None

    # Всегда выбираем наибольшую площадь (среди всех доступных записей/лет).
    scored = [(_geodesic_area_km2(geom), raw, geom) for raw, geom in hits]
    max_area = max(area for area, _, _ in scored)
    best = [(raw, geom) for area, raw, geom in scored if area >= max_area]

    if len(best) == 1:
        return best[0]
    if route_centroid is None:
        logger.info(
            "UCDB: несколько записей с одинаковой максимальной площадью, "
            "координаты маршрутов недоступны — берём первую."
        )
        return best[0]

    lon0, lat0 = route_centroid
    # Квадрат евклидова расстояния в градусах — точность не нужна для выбора.
    return min(
        best,
        key=lambda hit: (
            (lon0 - _repr_point(hit[1])[0]) ** 2
            + (lat0 - _repr_point(hit[1])[1]) ** 2
        ),
    )


def _extend_unique(base: list[str], extra: Iterable[Any]) -> None:
    """Дополняет список имён новыми, отсутствующими среди уже добавленных."""
    known = {_norm(n) for n in base}
    for value in extra:
        normalized = _norm(str(value))
        if not normalized:
            continue
        if normalized in known:
            continue
        base.append(str(value))
        known.add(normalized)


def load_ucdb_boundary(
    gpkg_path: str | Path | Sequence[str | Path],
    *,
    primary_name: str,
    alternate_names: Sequence[str] = (),
    route_coords: Iterable[Sequence[float]] = (),
    layer: str | None = None,
    name_column: str | None = None,
) -> Any | None:
    """Ищет границу города в UCDB и возвращает полигон в WGS84 или None.

    ``gpkg_path`` — один путь или несколько путей к GeoPackage (издания
    GLOBAL-R2024A и STAT-2015MT). Во всех файлах находятся одноимённые записи,
    и всегда выбирается запись с наибольшей площадью; при равной площади среди
    одноимённых центров предпочитается ближайший к маршрутам (``route_coords``).

    ``primary_name`` — основной вариант названия (обычно slug города).
    ``alternate_names`` — дополнительные имена/алиасы.
    """
    names = _candidate_names(primary_name)
    _extend_unique(names, alternate_names)

    # Официальные англоязычные названия из UCDB для транслитерированных
    # slug'ов (kiev → Kyiv, msk → Moscow, odessa → Odesa и т.п.).
    aliases = _UCDB_SLUG_ALIASES.get(_norm(primary_name), ())
    _extend_unique(names, aliases)

    paths = _as_path_list(gpkg_path)

    hits: list[tuple[Any, Any]] = []
    for path in paths:
        hits.extend(
            _read_layer_hits(path, names, layer=layer, name_column=name_column)
        )

    if not hits:
        _ensure_ucdb_err_log()
        logger.warning(
            "Город «%s» не найден в GHS-UCDB (%s)",
            primary_name,
            ", ".join(str(p) for p in paths),
        )
        return None

    centroid = _route_centroid(route_coords)
    picked = _pick_candidate(hits, centroid)
    if picked is None:
        return None

    name, geom = picked
    logger.info("UCDB: граница города «%s» из UCDB", name)
    # Часть центров — multipolygon из отдельных грейдов; объединяем в единый
    # внешний контур, как в osm_boundary.
    return union_all([geom])


def _as_path_list(
    gpkg_path: str | Path | Sequence[str | Path],
) -> list[str | Path]:
    """Нормализует аргумент ``gpkg_path`` в список путей."""
    if isinstance(gpkg_path, (str, Path)):
        return [gpkg_path]
    return list(gpkg_path)