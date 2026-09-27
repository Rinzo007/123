"""Подтверждённые публичные точки, которые Vulture не может вывести статически.

Файл добавляется в список анализируемых путей Vulture. Ссылки ниже имитируют
использование публичных полей/методов, доступных внешнему коду или библиотекам.
"""

import overture

from transit_planner.analytics import AccessibilityResult, NetworkAnalytics, StopAnalytics
from transit_planner.city import City
from transit_planner.demand import TemporalDemandMatrix
from transit_planner.economics import EconomicsResult
from transit_planner.infrastructure import TrackSection
from transit_planner.providers import CityDataset
from transit_planner.city_pack import CityPackFile, build_and_write_overture_city_pack
from transit_planner.road import RoadEdge
from transit_planner.routing import TransitRouter
from transit_planner.scenario import MetricDelta, ScenarioComparison

overture.__getattr__

StopAnalytics.total_activity
AccessibilityResult.jobs_share
NetworkAnalytics.overloaded_sections

City.country
City.metadata

TemporalDemandMatrix.by_purpose

EconomicsResult.annual_fare_revenue
EconomicsResult.operating_cost_per_transit_trip
EconomicsResult.revenue_per_transit_trip

TrackSection.station_ids
CityDataset.metadata

RoadEdge.to_connector_id

TransitRouter.from_overture_network

MetricDelta.relative_delta
ScenarioComparison.base_scenario_id
ScenarioComparison.alternative_scenario_id

CityPackFile
build_and_write_overture_city_pack
