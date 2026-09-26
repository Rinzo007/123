from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class ObservedRouteRidership:
    route_id: str
    observed_boardings_per_day: float

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise ValueError("route_id cannot be empty")
        if self.observed_boardings_per_day < 0:
            raise ValueError("Observed ridership cannot be negative")


@dataclass(frozen=True, slots=True)
class CalibrationRouteResult:
    route_id: str
    observed: float
    simulated: float
    absolute_error: float
    relative_error: float


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    routes: tuple[CalibrationRouteResult, ...]
    mae: float
    rmse: float
    mape: float


def calibrate_route_ridership(
    observed: tuple[ObservedRouteRidership, ...],
    simulated: dict[str, float],
) -> CalibrationReport:
    results: list[CalibrationRouteResult] = []
    relative_errors: list[float] = []

    for item in observed:
        actual = max(0.0, float(item.observed_boardings_per_day))
        predicted = max(0.0, float(simulated.get(item.route_id, 0.0)))
        absolute_error = abs(predicted - actual)
        relative_error = (
            0.0 if actual == 0.0 else absolute_error / actual
        )
        results.append(
            CalibrationRouteResult(
                route_id=item.route_id,
                observed=actual,
                simulated=predicted,
                absolute_error=absolute_error,
                relative_error=relative_error,
            )
        )
        if actual > 0:
            relative_errors.append(relative_error)

    if not results:
        return CalibrationReport((), 0.0, 0.0, 0.0)

    errors = [item.absolute_error for item in results]
    return CalibrationReport(
        routes=tuple(results),
        mae=sum(errors) / len(errors),
        rmse=sqrt(sum(error * error for error in errors) / len(errors)),
        mape=sum(relative_errors) / len(relative_errors) if relative_errors else 0.0,
    )


def route_boardings_from_assignment(route_flows) -> dict[str, float]:
    return {
        flow.route_id: flow.boardings
        for flow in route_flows
    }
