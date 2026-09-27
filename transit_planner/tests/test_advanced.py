from transit_planner.advanced import (
    Bond, Elevation, FinancialLedger, FinancialSnapshot, GradeCrossing,
    HourlyLoad, LifetimeStats, Portal, RouteFinancials, TrackGeometry, track_length,
)


def test_advanced_infrastructure():
    geometry = TrackGeometry("g", ("n1", "n2", "n3"), 2000, curve_radius_m=250)
    crossing = GradeCrossing("x", "t1", 30)
    portal = Portal("p", "t1", 0.5, 100000)
    assert geometry.length_m == 2000
    assert crossing.speed_limit_kph == 30
    assert portal.position == 0.5
    assert track_length(((0, 0), (3, 4), (3, 14))) == 15


def test_financial_history_and_debt():
    ledger = FinancialLedger()
    ledger.add_bond(Bond("b", 1_000_000, 0.05, 20))
    ledger.add_snapshot(FinancialSnapshot(1, 500, 300, 50, 100, 50, 0))
    assert ledger.debt == 1_000_000
    assert ledger.annual_interest == 50_000


def test_hourly_and_lifetime_stats():
    load = HourlyLoad("r1", 8, "forward", 900, 1000)
    route = RouteFinancials("r1", 100, 40, 10, 1000)
    stats = LifetimeStats().advance(passengers=100, passenger_km=500, revenue=200, operating_cost=120)
    assert load.load_factor == 0.9
    assert route.net_operating_result == 50
    assert stats.days == 1
