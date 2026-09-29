"""Каскадная классификация зданий с сохранением пути решения.

Зачем каскад, а не проверка класса: на территории, где Overture покрыт sparsely,
у части зданий нет ни класса, ни подтипа (в проверенном берлинском боксе таких
636 из 3226, то есть 20%). Одиночная проверка молча относит их к одному
классу, и ошибка уходит в население. Каскад вместо этого даёт каждому зданию
класс, тот сигнал, который его определил, и все сигналы, которые смотрели и
не сработали.

Сигналы, которых нет, не выдумываются: их отсутствие записывается в
``unavailable`` и попадает в provenance пака. Иначе «семь уровней» выглядели
бы как семь уровней, а работали бы два.

Классификация влияет на население (жилая площадь) и на рабочие места, поэтому
она проверяется тестами на каждом уровне, а не только на общем случае.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .urban import BuildingFootprint, is_residential_building


class BuildingClass(str, Enum):
    """Класс здания с точки зрения спроса."""

    RESIDENTIAL = "residential"
    WORKPLACE = "workplace"
    INERT = "inert"
    UNKNOWN = "unknown"


# Уровни каскада в порядке приоритета. Число - это порядок, а не вес: решение
# принимает первый сработавший уровень, и он же записывается в decided_by.
TIER_OVERRIDE = 0
TIER_OSM_TAG = 1
TIER_CLASS = 2
TIER_SUBTYPE = 3
TIER_LANDUSE = 4
TIER_URBAN_RASTER = 5
TIER_MORPHOLOGY = 6

# Классы Overture, проверенные на реальных данных: 2026-09-23.1, Берлин.
# Это не спекуляция о схеме, а перечисление того, что реально приходит.
RESIDENTIAL_OVERTURE_CLASSES = frozenset({
    "apartments",
    "residential",
    "house",
    "dormitory",
    "bungalow",
    "hut",
    "detached",
})
WORKPLACE_OVERTURE_CLASSES = frozenset({
    "office",
    "commercial",
    "retail",
    "government",
    "hospital",
    "school",
    "kindergarten",
    "college",
    "university",
    "hotel",
    "industrial",
    "warehouse",
    "garages",
})
INERT_OVERTURE_CLASSES = frozenset({
    "roof",
    "toilets",
    "transportation",
    "bunker",
    "sports_hall",
    "kiosk",
    "pavilion",
    "guardhouse",
    "outbuilding",
    "religious",
    "synagogue",
})

# Рабочие места типизируются по назначению здания, а не одной суммой.
# Так поступает и эталонный киевский бандл: у него рабочая сторона размечена
# типами (UNI, HOS, AIR, MUS), и спецспрос рождается из них, а не добавляется
# отдельно. Ключи совпадают с purpose в модели спроса, поэтому тип сразу
# попадает в существующий слой, а не в обход него.
WORKPLACE_PURPOSE_BY_CLASS: dict[str, str] = {
    "college": "edu",
    "university": "edu",
    "school": "edu",
    "kindergarten": "edu",
    "hospital": "health",
    "clinic": "health",
    "retail": "shop",
    "shop": "shop",
    "supermarket": "shop",
    "department_store": "shop",
    "hotel": "night",
    "restaurant": "night",
    "cafe": "night",
    "airport": "air",
    "airport_terminal": "air",
    "bus_station": "air",
}
# Здания без указанного назначения остаются общей работой.
GENERIC_WORKPLACE_PURPOSE = "work"

# Этажность, при которой крупный контур скорее производственный, а крупный
# контур с малой этажностью - жилой. Границы взяты из морфологии, а не из
# предположения о городе: проверяются тестами на синтетических зданиях.
MORPHOLOGY_WORKPLACE_MIN_M2 = 1500.0
MORPHOLOGY_WORKPLACE_MIN_FLOORS = 4.0
MORPHOLOGY_RESIDENTIAL_MAX_M2 = 5000.0
MORPHOLOGY_RESIDENTIAL_MIN_M2 = 20.0


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    """Что дал один сигнал по конкретному зданию."""

    tier: int
    name: str
    value: str
    decision: BuildingClass | None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "name": self.name,
            "value": self.value,
            "decision": self.decision.value if self.decision else None,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BuildingClassification:
    """Итог классификации одного здания вместе со всем путём решения."""

    building_id: str
    building_class: BuildingClass
    decided_by: str
    confidence: float
    outcomes: tuple[SignalOutcome, ...] = ()
    floor_area_m2: float = 0.0
    workplace_purpose: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.building_id,
            "class": self.building_class.value,
            "decidedBy": self.decided_by,
            "confidence": round(self.confidence, 3),
            "floorAreaM2": round(self.floor_area_m2, 1),
            "workplacePurpose": self.workplace_purpose,
            "signals": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(slots=True)
class ExternalSignals:
    """Внешние источники сигналов.

    Каждый источник - callable, возвращающий класс или None. Отдельные
    зависимости не заводятся: без растра OBAT или выгрузки OSM соответствующее
    поле остаётся None, и это видно в отчёте, а не теряется молча.
    """

    override_class: dict[str, BuildingClass] = field(default_factory=dict)
    osm_tag_class: dict[str, BuildingClass] = field(default_factory=dict)
    landuse_class: dict[str, BuildingClass] = field(default_factory=dict)
    urban_raster_class: dict[str, BuildingClass] = field(default_factory=dict)

    def absent_sources(self) -> list[str]:
        missing = []
        if not self.osm_tag_class:
            missing.append("osm_tag")
        if not self.landuse_class:
            missing.append("osm_landuse")
        if not self.urban_raster_class:
            missing.append("urban_landuse_raster")
        return missing


def _floors(building: BuildingFootprint) -> float:
    from .urban import effective_floors

    return effective_floors(building)


def classify_building(
    building: BuildingFootprint,
    *,
    signals: ExternalSignals | None = None,
) -> BuildingClassification:
    """Классифицирует здание и возвращает путь, которым пришли к решению."""
    signals = signals or ExternalSignals()
    outcomes: list[SignalOutcome] = []
    floors = _floors(building)
    floor_area = building.area_m2 * floors

    def record(tier: int, name: str, value: object, decision: BuildingClass | None,
               reason: str = "") -> BuildingClass | None:
        outcomes.append(
            SignalOutcome(
                tier=tier,
                name=name,
                value="" if value is None else str(value),
                decision=decision,
                reason=reason,
            )
        )
        return decision

    # 0. Ручное переопределение оператора: промышленные кварталы и военные,
    #    которые по прочим сигналам читаются как жильё.
    forced = signals.override_class.get(building.id)
    if record(TIER_OVERRIDE, "operator_override", building.id, forced) is not None:
        return _finish(building, forced or BuildingClass.UNKNOWN, "operator_override",
                       1.0, outcomes, floor_area)

    # 1. Под-теги OSM: civic / school / hospital / religious / military.
    osm = signals.osm_tag_class.get(building.id)
    if record(TIER_OSM_TAG, "osm_subtag", building.id, osm) is not None:
        return _finish(building, osm or BuildingClass.UNKNOWN, "osm_subtag",
                       0.9, outcomes, floor_area)

    # 2. Класс Overture - самый прямой сигнал, когда он есть.
    raw_class = (building.building_class or "").strip().lower()
    if raw_class:
        decision = _class_from_overture(raw_class)
        if record(TIER_CLASS, "overture_class", raw_class, decision) is not None:
            confidence = 0.85 if decision is not BuildingClass.UNKNOWN else 0.2
            return _finish(building, decision, "overture_class", confidence,
                           outcomes, floor_area)

    # 3. Подтип Overture: класс заполнен не у всех зданий, подтип остаётся.
    subtype = (building.subtype or "").strip().lower()
    if subtype:
        decision = _class_from_overture(subtype)
        if record(TIER_SUBTYPE, "overture_subtype", subtype, decision) is not None:
            confidence = 0.7 if decision is not BuildingClass.UNKNOWN else 0.2
            return _finish(building, decision, "overture_subtype", confidence,
                           outcomes, floor_area)

    # 4. Контекст землепользования OSM: квартал в промзоне не жилой.
    landuse = signals.landuse_class.get(building.id)
    if record(TIER_LANDUSE, "osm_landuse", building.id, landuse) is not None:
        return _finish(building, landuse or BuildingClass.UNKNOWN, "osm_landuse",
                       0.8, outcomes, floor_area)

    # 5. Растр городского землепользования: GULU-подобный, сглаженный.
    raster = signals.urban_raster_class.get(building.id)
    if record(TIER_URBAN_RASTER, "urban_landuse_raster", building.id, raster) is not None:
        return _finish(building, raster or BuildingClass.UNKNOWN,
                       "urban_landuse_raster", 0.6, outcomes, floor_area)

    # 6. Морфология: единственный сигнал, который работает всегда, потому что
    #    площадь и этажность есть у каждого непустого контура.
    decision, reason = _class_from_morphology(building.area_m2, floors)
    record(TIER_MORPHOLOGY, "morphology",
           f"area={building.area_m2:.0f}m2 floors={floors:.1f}", decision, reason)
    confidence = 0.5 if decision is not BuildingClass.UNKNOWN else 0.1
    return _finish(building, decision, "morphology", confidence, outcomes, floor_area)


def _finish(
    building: BuildingFootprint,
    decision: BuildingClass | None,
    decided_by: str,
    confidence: float,
    outcomes: list[SignalOutcome],
    floor_area: float,
) -> BuildingClassification:
    return BuildingClassification(
        building_id=building.id,
        building_class=decision or BuildingClass.UNKNOWN,
        decided_by=decided_by,
        confidence=confidence,
        outcomes=tuple(outcomes),
        floor_area_m2=floor_area,
        workplace_purpose=workplace_purpose_for(building),
    )


def workplace_purpose_for(building: BuildingFootprint) -> str | None:
    """Назначение рабочего здания как ключ purpose, либо None для не-работы.

    Тип ставится только рабочим зданиям и только если назначение известно из
    класса Overture: угадывать по морфологии нельзя, иначе склад стал бы
    больницей.
    """
    if not is_residential_building(building) and (
        (building.building_class or "").strip().lower() not in WORKPLACE_OVERTURE_CLASSES
    ):
        return None
    raw = (building.building_class or "").strip().lower()
    if raw in WORKPLACE_PURPOSE_BY_CLASS:
        return WORKPLACE_PURPOSE_BY_CLASS[raw]
    return GENERIC_WORKPLACE_PURPOSE if raw in WORKPLACE_OVERTURE_CLASSES else None


def _class_from_overture(value: str) -> BuildingClass:
    if value in RESIDENTIAL_OVERTURE_CLASSES:
        return BuildingClass.RESIDENTIAL
    if value in WORKPLACE_OVERTURE_CLASSES:
        return BuildingClass.WORKPLACE
    if value in INERT_OVERTURE_CLASSES:
        return BuildingClass.INERT
    return BuildingClass.UNKNOWN


def _class_from_morphology(area_m2: float, floors: float) -> tuple[BuildingClass, str]:
    if area_m2 < MORPHOLOGY_RESIDENTIAL_MIN_M2:
        return BuildingClass.UNKNOWN, "слишком мал для жилого или рабочего"
    if (
        area_m2 >= MORPHOLOGY_WORKPLACE_MIN_M2
        and floors >= MORPHOLOGY_WORKPLACE_MIN_FLOORS
    ):
        return BuildingClass.WORKPLACE, "крупный многоэтажный контур"
    if area_m2 <= MORPHOLOGY_RESIDENTIAL_MAX_M2:
        return BuildingClass.RESIDENTIAL, "малоэтажный контур жилого масштаба"
    return BuildingClass.UNKNOWN, "крупный малоэтажный контур: оба класса возможны"


def classify_buildings(
    buildings: tuple[BuildingFootprint, ...],
    *,
    signals: ExternalSignals | None = None,
) -> tuple[BuildingClassification, ...]:
    return tuple(classify_building(building, signals=signals) for building in buildings)


def classification_summary(
    classifications: tuple[BuildingClassification, ...],
    *,
    signals: ExternalSignals | None = None,
) -> dict:
    """Сводка для provenance: сколько зданий решил каждый уровень."""
    by_tier: dict[str, int] = {}
    by_class: dict[str, int] = {}
    unknown_area = 0.0
    for item in classifications:
        by_tier[item.decided_by] = by_tier.get(item.decided_by, 0) + 1
        key = item.building_class.value
        by_class[key] = by_class.get(key, 0) + 1
        if item.building_class is BuildingClass.UNKNOWN:
            unknown_area += item.floor_area_m2
    return {
        "buildings": len(classifications),
        "byTier": dict(sorted(by_tier.items())),
        "byClass": dict(sorted(by_class.items())),
        "unknownFloorAreaM2": round(unknown_area, 1),
        "absentSignals": (signals or ExternalSignals()).absent_sources(),
    }
