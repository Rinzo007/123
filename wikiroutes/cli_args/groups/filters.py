"""Фильтры маршрутов: геометрия, граница города, маршруты из OSM."""
from __future__ import annotations

import argparse

from ...config import DEFAULT_UCDB_PATHS
from ...urban_boundary import DEFAULT_BUILT_S_FILE


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    """Ограничения по непрямолинейности, длине, радиусу и границе города."""
    # Фильтры
    parser.add_argument(
        "--curv",
        type=float,
        default=0.0,
        help="Максимальный коэффициент непрямолинейности маршрута; 0 отключает ограничение.",
    )
    parser.add_argument(
        "--minlen",
        type=float,
        default=None,
        help="Минимальная длина маршрута в километрах; 0 отключает ограничение.",
    )
    parser.add_argument(
        "--maxlen",
        type=float,
        default=None,
        help="Максимальная длина маршрута в километрах; 0 отключает ограничение.",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=None,
        help="Максимальное расстояние маршрута от заданного центра в километрах; 0 отключает ограничение.",
    )
    parser.add_argument(
        "--center-lat",
        type=float,
        default=None,
        help="Широта центра для фильтра --radius.",
    )
    parser.add_argument(
        "--center-lon",
        type=float,
        default=None,
        help="Долгота центра для фильтра --radius.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Оставить только активные маршруты.",
    )
    parser.add_argument(
        "--no-boundary",
        dest="boundary",
        action="store_false",
        default=True,
        help="Отключить автоматическое получение границы города из OSM и фильтр маршрутов по ней.",
    )
    parser.add_argument(
        "--boundary-buffer",
        type=float,
        default=500.0,
        help="Расширение границы города в метрах при фильтрации (допуск для маршрутов у края).",
    )
    parser.add_argument(
        "--boundary-country",
        dest="boundary_country",
        type=str,
        default="ru",
        help="ISO-код страны (по умолчанию 'ru') для разрешения омонимов при поиске границы "
        "города в OSM (напр. 'dzerzhynsk' → российский Дзержинск). При отсутствии результата "
        "по стране делается глобальный запрос (это спасает Крым, помеченный в OSM как Украина).",
    )
    parser.add_argument(
        "--boundary-extra",
        dest="boundary_extra",
        type=str,
        default="",
        help="Дополнительные relation-id границ OSM (через запятую) для склейки с границей "
        "города (объединение unary_union). Напр. '--boundary-extra 12345,67890' — муниципалитет "
        "и т.п. Загружаются из Overpass.",
    )
    parser.add_argument(
        "--ucdb",
        dest="ucdb",
        type=str,
        nargs="*",
        metavar="GPKG",
        help=(
            "Использовать границы городов из GHS-UCDB GeoPackage вместо OSM/Nominatim. "
            "Принимает один или несколько путей к gpkg (издания GHS_STAT_UCDB2015MT "
            "и/или GHS_UCDB_GLOBE_R2024A); в каждом файле город подбирается по названию "
            "(с защитой от омонимов по близости к маршрутам) и среди всех найденных "
            "записей выбирается наибольшая по площади. Требует --boundary. Без значения "
            "используются оба файла по умолчанию: {} (порядок можно задать переменной "
            "окружения WIKIROUTES_UCDB через запятую)."
        ).format(", ".join(DEFAULT_UCDB_PATHS)),
    )
    parser.add_argument(
        "--boundary-geojson",
        dest="boundary_geojson",
        action="store_true",
        help="Экспортировать использованную границу города в отдельный GeoJSON-файл.",
    )
    parser.add_argument(
        "--boundary-raster",
        dest="boundary_raster",
        type=str,
        nargs="?",
        const=DEFAULT_BUILT_S_FILE,
        default=DEFAULT_BUILT_S_FILE,
        help=(
            "Растр GHS-BUILT-S (застройка, EPSG:54009) для построения границы города, "
            f"когда его нет в UCDB: вокруг центра из OSM строится «городская территория» "
            f"(пороги US Census Bureau: ядро 10%%, раствор 5%%, мин. 25 км²). "
            f"По умолчанию: {DEFAULT_BUILT_S_FILE}."
        ),
    )
    parser.add_argument(
        "--max-route-number",
        type=int,
        default=0,
        help="Максимальный номер маршрута и правило отсева маршрутов без номера или со скобками; 0 отключает фильтр.",
    )

    # Маршруты из OSM
    parser.add_argument(
        "--osm-routes",
        action="store_true",
        help="Добавить маршруты общественного транспорта из OSM (Overpass) к общему "
        "списку. Отношения route становятся направлениями и группируются в маршруты "
        "по номеру (ref) и типу транспорта; запрос строится по bbox границы города "
        "(кэшируется в каталоге кэша).",
    )
