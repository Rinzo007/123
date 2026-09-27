from __future__ import annotations

from dataclasses import dataclass

from .analytics import NetworkAnalytics, analyze_network
from .assignment import AssignmentConfig, AssignmentResult, assign_demand
from .city import DemandZone
from .demand import DemandMatrix
from .economics import EconomicsConfig, EconomicsResult, calculate_economics
from .network import Network


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    id: str
    name: str
    network: Network
    demand: DemandMatrix
    assignment_config: AssignmentConfig
    zones: tuple[DemandZone, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    scenario_id: str
    name: str
    assignment: AssignmentResult
    analytics: NetworkAnalytics
    economics: EconomicsResult | None = None


@dataclass(frozen=True, slots=True)
class MetricDelta:
    metric: str
    base: float
    alternative: float

    @property
    def delta(self) -> float:
        return self.alternative - self.base

    @property
    def relative_delta(self) -> float:
        return 0.0 if self.base == 0 else self.delta / self.base


@dataclass(frozen=True, slots=True)
class ServiceDelta:
    service_id: str
    route_id: str
    period_id: str
    base_riders: float
    alternative_riders: float
    base_peak_load_factor: float
    alternative_peak_load_factor: float
    base_fleet: float
    alternative_fleet: float
    base_effective_headway_min: float
    alternative_effective_headway_min: float
    base_minimum_headway_min: float
    alternative_minimum_headway_min: float

    @property
    def riders_delta(self) -> float:
        return self.alternative_riders - self.base_riders

    @property
    def peak_load_factor_delta(self) -> float:
        return self.alternative_peak_load_factor - self.base_peak_load_factor

    @property
    def fleet_delta(self) -> float:
        return self.alternative_fleet - self.base_fleet

    @property
    def effective_headway_delta(self) -> float:
        return self.alternative_effective_headway_min - self.base_effective_headway_min

    @property
    def minimum_headway_delta(self) -> float:
        return self.alternative_minimum_headway_min - self.base_minimum_headway_min


@dataclass(frozen=True, slots=True)
class SectionDelta:
    route_id: str
    from_stop_id: str
    to_stop_id: str
    base_passengers: float
    alternative_passengers: float

    @property
    def delta(self) -> float:
        return self.alternative_passengers - self.base_passengers


@dataclass(frozen=True, slots=True)
class ScenarioComparison:
    base_scenario_id: str
    alternative_scenario_id: str
    metrics: tuple[MetricDelta, ...]
    sections: tuple[SectionDelta, ...]
    services: tuple[ServiceDelta, ...] = ()


def run_scenario(
    definition: ScenarioDefinition,
    *,
    economics_config: EconomicsConfig | None = None,
) -> ScenarioRun:
    assignment = assign_demand(
        definition.network,
        definition.demand,
        zones={zone.id: zone for zone in definition.zones},
        config=definition.assignment_config,
    )
    analytics = analyze_network(
        definition.network,
        assignment,
        zones=definition.zones,
    )
    economics = None
    if economics_config is not None:
        economics = calculate_economics(
            definition.network,
            assignment,
            config=economics_config,
        )


    return ScenarioRun(
        scenario_id=definition.id,
        name=definition.name,
        assignment=assignment,
        analytics=analytics,
        economics=economics,
    )


def compare_scenarios(
    base: ScenarioRun,
    alternative: ScenarioRun,
) -> ScenarioComparison:
    base_metrics = base.assignment.metrics
    alternative_metrics = alternative.assignment.metrics
    pairs = (
        ("total_trips", base_metrics.total_trips, alternative_metrics.total_trips),
        ("transit_trips", base_metrics.transit_trips, alternative_metrics.transit_trips),
        ("car_trips", base_metrics.car_trips, alternative_metrics.car_trips),
        ("walk_trips", base_metrics.walk_trips, alternative_metrics.walk_trips),
        ("transit_share", base_metrics.transit_share, alternative_metrics.transit_share),
        ("average_transit_time_min", base_metrics.average_transit_time_min, alternative_metrics.average_transit_time_min),
        ("average_transfers", base_metrics.average_transfers, alternative_metrics.average_transfers),
        ("max_load_ratio", base.assignment.max_load_ratio, alternative.assignment.max_load_ratio),
        ("passenger_km", base.analytics.passenger_km, alternative.analytics.passenger_km),
        ("bike_trips", base_metrics.bike_trips, alternative_metrics.bike_trips),
    )
    if base.economics is not None and alternative.economics is not None:
        base_economics = base.economics
        alternative_economics = alternative.economics
        pairs = pairs + (
            (
                "daily_vehicle_km",
                base_economics.daily_vehicle_km,
                alternative_economics.daily_vehicle_km,
            ),
            (
                "daily_fleet_cost",
                base_economics.daily_fleet_cost,
                alternative_economics.daily_fleet_cost,
            ),
            (
                "daily_operating_cost",
                base_economics.daily_operating_cost,
                alternative_economics.daily_operating_cost,
            ),
            (
                "daily_fare_revenue",
                base_economics.daily_fare_revenue,
                alternative_economics.daily_fare_revenue,
            ),
            (
                "annual_fleet_cost",
                base_economics.annual_fleet_cost,
                alternative_economics.annual_fleet_cost,
            ),
            (
                "annual_operating_cost",
                base_economics.annual_operating_cost,
                alternative_economics.annual_operating_cost,
            ),
            (
                "annual_fare_revenue",
                base_economics.annual_fare_revenue,
                alternative_economics.annual_fare_revenue,
            ),
            (
                "operating_cost_per_transit_trip",
                base_economics.operating_cost_per_transit_trip,
                alternative_economics.operating_cost_per_transit_trip,
            ),
            (
                "revenue_per_transit_trip",
                base_economics.revenue_per_transit_trip,
                alternative_economics.revenue_per_transit_trip,
            ),
        )

    metrics = tuple(
        MetricDelta(metric=name, base=left, alternative=right)
        for name, left, right in pairs
    )

    base_sections = {
        (item.route_id, item.from_stop_id, item.to_stop_id): item
        for item in base.assignment.section_loads
    }
    alt_sections = {
        (item.route_id, item.from_stop_id, item.to_stop_id): item
        for item in alternative.assignment.section_loads
    }
    base_services = {
        (item.service_id, item.route_id, item.period_id): item
        for item in base.analytics.services
    }
    alt_services = {
        (item.service_id, item.route_id, item.period_id): item
        for item in alternative.analytics.services
    }
    services = tuple(
        ServiceDelta(
            service_id=key[0],
            route_id=key[1],
            period_id=key[2],
            base_riders=base_services[key].riders if key in base_services else 0.0,
            alternative_riders=alt_services[key].riders if key in alt_services else 0.0,
            base_peak_load_factor=(
                base_services[key].peak_load_factor if key in base_services else 0.0
            ),
            alternative_peak_load_factor=(
                alt_services[key].peak_load_factor if key in alt_services else 0.0
            ),
            base_fleet=base_services[key].fleet if key in base_services else 0.0,
            alternative_fleet=alt_services[key].fleet if key in alt_services else 0.0,
            base_effective_headway_min=(
                base_services[key].effective_headway_min if key in base_services else 0.0
            ),
            alternative_effective_headway_min=(
                alt_services[key].effective_headway_min if key in alt_services else 0.0
            ),
            base_minimum_headway_min=(
                base_services[key].minimum_headway_min if key in base_services else 0.0
            ),
            alternative_minimum_headway_min=(
                alt_services[key].minimum_headway_min if key in alt_services else 0.0
            ),
        )
        for key in sorted(set(base_services) | set(alt_services))
    )

    sections = tuple(
        SectionDelta(
            route_id=key[0],
            from_stop_id=key[1],
            to_stop_id=key[2],
            base_passengers=base_sections.get(key).passengers if key in base_sections else 0.0,
            alternative_passengers=alt_sections.get(key).passengers if key in alt_sections else 0.0,
        )
        for key in sorted(set(base_sections) | set(alt_sections))
    )
    return ScenarioComparison(
        base_scenario_id=base.scenario_id,
        alternative_scenario_id=alternative.scenario_id,
        metrics=metrics,
        sections=sections,
        services=services,
    )
