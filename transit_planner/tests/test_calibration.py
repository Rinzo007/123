from transit_planner.calibration import (
    ObservedRouteRidership,
    calibrate_route_ridership,
    route_boardings_from_assignment,
)


def test_calibration_report_contains_mae_rmse_and_mape():
    report = calibrate_route_ridership(
        (
            ObservedRouteRidership("r1", 100.0),
            ObservedRouteRidership("r2", 200.0),
        ),
        {"r1": 110.0, "r2": 180.0},
    )

    assert report.mae == 15.0
    assert abs(report.rmse - (250.0 ** 0.5)) < 1e-12
    assert report.mape == 0.1


def test_calibration_uses_zero_when_simulated_route_is_missing():
    report = calibrate_route_ridership(
        (ObservedRouteRidership("r1", 100.0),),
        {},
    )

    assert report.routes[0].simulated == 0.0
    assert report.routes[0].relative_error == 1.0


def test_route_boardings_adapter():
    class Flow:
        route_id = "r1"
        boardings = 123.0

    assert route_boardings_from_assignment((Flow(),)) == {"r1": 123.0}
