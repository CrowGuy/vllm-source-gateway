from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ModelStatus = Literal["online", "unavailable"]


@dataclass(frozen=True, slots=True)
class UpstreamTarget:
    name: str
    base_url: str
    models: tuple[str, ...]
    authorization_token: str | None = None

    def serves_model(self, model_name: str) -> bool:
        return model_name in self.models


@dataclass(frozen=True, slots=True)
class SelectedUpstream:
    model_name: str
    upstream: UpstreamTarget


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    model_name: str
    total_upstream_count: int
    healthy_upstream_count: int

    @property
    def status(self) -> ModelStatus:
        if self.healthy_upstream_count > 0:
            return "online"
        return "unavailable"


@dataclass(frozen=True, slots=True)
class UpstreamHealthSnapshot:
    upstream_name: str
    healthy: bool
