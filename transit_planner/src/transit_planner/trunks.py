from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees

from .network import Network, Route


@dataclass(frozen=True, slots=True)
class TrunkSegment:
    route_id: str
    from_stop_id: str
    to_stop_id: str
    length_km: float


@dataclass(frozen=True, slots=True)
class Trunk:
    trunk_id: str
    route_ids: tuple[str, ...]
    segment_ids: tuple[tuple[str, str, str], ...]
    length_km: float
    max_pair_overlap: int


def detect_shared_trunks(
    network: Network,
    *,
    max_bearing_deg: float = 20.0,
    min_overlap_ratio: float = 0.3,
    min_pair_overlap: int = 8,
) -> tuple[Trunk, ...]:
    """Group route pairs that run together over long shared corridors.

    Simplified port of the Borough Studio interlined pipeline: pairs qualify
    when the shorter route's segments overlap the other's at least
    min_overlap_ratio in length, their start bearings differ by no more than
    max_bearing_deg, and they share at least min_pair_overlap segment pairs.
    Only pairs are modelled (no mega-merges), and a route may appear in
    several trunks because in reality it does.
    """
    if max_bearing_deg < 0:
        raise ValueError("max_bearing_deg cannot be negative")
    if not 0.0 <= min_overlap_ratio <= 1.0:
        raise ValueError("min_overlap_ratio must be in [0, 1]")
    if min_pair_overlap < 1:
        raise ValueError("min_pair_overlap must be positive")

    routes = tuple(network.routes.values())
    route_segments = {
        route.id: tuple(
            TrunkSegment(route.id, left, right, network.route_segment_length_km(route, index))
            for index, (left, right) in enumerate(route.segment_pairs())
        )
        for route in routes
    }
    segments_by_key: dict[tuple[str, str, str], TrunkSegment] = {}
    for route in routes:
        for segment in route_segments[route.id]:
            segments_by_key[(segment.route_id, segment.from_stop_id, segment.to_stop_id)] = segment
    normalized = {
        route.id: {(left, right): length for left, right, length in (
            (segment.from_stop_id, segment.to_stop_id, segment.length_km)
            for segment in route_segments[route.id]
        )}
        for route in routes
    }

    trunks: list[Trunk] = []
    index = 0
    for first_index, left in enumerate(routes):
        for right in routes[first_index + 1:]:
            overlap = 0.0
            shared_pairs: list[tuple[str, str, str]] = []
            shorter = normalized[left.id]
            for key, length in normalized[right.id].items():
                if key in shorter:
                    overlap += min(length, shorter[key])
                    shared_pairs.append((right.id, key[0], key[1]))
            for key, length in shorter.items():
                if key in normalized[right.id]:
                    shared_pairs.append((left.id, key[0], key[1]))
            left_total = sum(normalized[left.id].values())
            right_total = sum(normalized[right.id].values())
            shorter_total = min(left_total, right_total)
            if shorter_total <= 0 or overlap / shorter_total < min_overlap_ratio:
                continue
            if len(shared_pairs) < min_pair_overlap:
                continue
            if _bearing_delta(network, left, right) > max_bearing_deg:
                continue
            trunk_segments = tuple(
                segments_by_key[pair]
                for pair in shared_pairs
                if pair in segments_by_key
            )
            total_km = sum(segment.length_km for segment in trunk_segments)
            trunks.append(
                Trunk(
                    f"trunk-{index:03d}",
                    (left.id, right.id),
                    tuple(shared_pairs),
                    total_km,
                    max(1, len(shared_pairs) // 2),
                )
            )
            index += 1
    return tuple(trunks)


def _bearing_delta(network: Network, left: Route, right: Route) -> float:
    if left.stop_ids[0] not in network.stops or right.stop_ids[0] not in network.stops:
        return 0.0
    a = network.stops[left.stop_ids[0]].location
    b = network.stops[right.stop_ids[0]].location
    if (a.x, a.y) == (b.x, b.y):
        return 0.0
    if left.stop_ids[-1] not in network.stops or right.stop_ids[-1] not in network.stops:
        return 0.0
    c = network.stops[left.stop_ids[-1]].location
    d = network.stops[right.stop_ids[-1]].location
    bearing_a = atan2(c.y - a.y, c.x - a.x)
    bearing_b = atan2(d.y - b.y, d.x - b.x)
    delta = abs(degrees(bearing_a - bearing_b))
    return min(delta, 360.0 - delta)
