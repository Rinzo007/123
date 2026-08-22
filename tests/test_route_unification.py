from __future__ import annotations

from dataclasses import dataclass

import wikiroutes.pipeline as pipeline
from wikiroutes.enums import RouteType
from wikiroutes.units import DirectionKey


@dataclass(frozen=True)
class _Link:
    name: str
    route_id: int


@dataclass(frozen=True)
class _Section:
    route_type: RouteType
    links: tuple[_Link, ...]


@dataclass(frozen=True)
class _Catalog:
    sections: tuple[_Section, ...]


class _Config:
    type_filter: frozenset[RouteType] = frozenset()
    disabled_types: frozenset[RouteType] = frozenset()
    route_filter: str | None = None
    max_route_number: int = 0


def test_all_route_types_use_single_loading_group() -> None:
    assert pipeline.BASE_ROUTE_TYPES == frozenset(RouteType)

    catalog = _Catalog(
        sections=tuple(
            _Section(
                route_type=route_type,
                links=(_Link(name=f"{route_type.value}-1", route_id=index + 1),),
            )
            for index, route_type in enumerate(RouteType)
        )
    )

    tasks = pipeline.build_base_tasks(catalog, "city", _Config())
    assert len(tasks) == len(RouteType)


def test_secondary_group_is_empty_after_unification() -> None:
    catalog = _Catalog(
        sections=tuple(
            _Section(
                route_type=route_type,
                links=(_Link(name=f"{route_type.value}-1", route_id=index + 1),),
            )
            for index, route_type in enumerate(RouteType)
        )
    )

    assert pipeline._secondary_types(_Config()) == set()
    assert pipeline.build_secondary_tasks(catalog, "city", _Config()) == []


def test_direction_key_api_remains_available() -> None:
    key: DirectionKey = (123, 0)
    assert key == (123, 0)
