from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .infrastructure import TrackSection


class AssetState(StrEnum):
    BLUEPRINT = "blueprint"
    CONSTRUCTED = "constructed"
    ACTIVE = "active"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class TrackBlueprint:
    id: str
    sections: tuple[TrackSection, ...]
    state: AssetState = AssetState.BLUEPRINT

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Blueprint id cannot be empty")
        if not self.sections:
            raise ValueError("Blueprint needs at least one track section")
        if self.state == AssetState.REMOVED:
            raise ValueError("New blueprint cannot start removed")


@dataclass(slots=True)
class BlueprintProject:
    id: str
    name: str
    blueprints: dict[str, TrackBlueprint]
    history: list[str]

    def __init__(self, id: str, name: str) -> None:
        if not id.strip() or not name.strip():
            raise ValueError("Project id and name cannot be empty")
        self.id = id
        self.name = name
        self.blueprints = {}
        self.history = []

    def add(self, blueprint: TrackBlueprint) -> None:
        if blueprint.id in self.blueprints:
            raise ValueError(f"Duplicate blueprint: {blueprint.id}")
        self.blueprints[blueprint.id] = blueprint
        self.history.append(f"add:{blueprint.id}")

    def transition(self, blueprint_id: str, state: AssetState) -> None:
        current = self.blueprints[blueprint_id]
        if current.state == AssetState.REMOVED:
            raise ValueError("Removed assets cannot be transitioned")
        allowed = {
            AssetState.BLUEPRINT: {AssetState.CONSTRUCTED, AssetState.REMOVED},
            AssetState.CONSTRUCTED: {AssetState.ACTIVE, AssetState.REMOVED},
            AssetState.ACTIVE: {AssetState.REMOVED},
        }
        if state not in allowed[current.state]:
            raise ValueError(f"Invalid asset transition: {current.state} -> {state}")
        self.blueprints[blueprint_id] = TrackBlueprint(current.id, current.sections, state)
        self.history.append(f"state:{blueprint_id}:{state}")

    def undo(self) -> str:
        if not self.history:
            raise ValueError("No blueprint history")
        action = self.history.pop()
        if action.startswith("add:"):
            self.blueprints.pop(action[4:], None)
        elif action.startswith("state:"):
            raise ValueError("State undo requires an explicit prior snapshot")
        return action

    def build_all(self) -> tuple[str, ...]:
        built = []
        for blueprint_id, blueprint in tuple(self.blueprints.items()):
            if blueprint.state == AssetState.BLUEPRINT:
                self.transition(blueprint_id, AssetState.CONSTRUCTED)
                built.append(blueprint_id)
        return tuple(built)

    def activate_all(self) -> tuple[str, ...]:
        active = []
        for blueprint_id, blueprint in tuple(self.blueprints.items()):
            if blueprint.state == AssetState.CONSTRUCTED:
                self.transition(blueprint_id, AssetState.ACTIVE)
                active.append(blueprint_id)
        return tuple(active)
