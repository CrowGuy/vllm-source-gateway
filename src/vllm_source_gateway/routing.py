from __future__ import annotations

from collections import defaultdict
from threading import Lock

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.models import ModelAvailability, SelectedUpstream, UpstreamHealthSnapshot, UpstreamTarget


class RoutingError(RuntimeError):
    """Base error for routing failures."""


class UnknownModelError(RoutingError):
    """Raised when a requested model does not exist in the routing registry."""


class NoHealthyUpstreamError(RoutingError):
    """Raised when a requested model has no healthy upstreams."""


class RoutingRegistry:
    def __init__(self, upstreams: list[UpstreamTarget]) -> None:
        self._upstreams_by_name = {upstream.name: upstream for upstream in upstreams}
        self._model_to_upstreams: dict[str, tuple[UpstreamTarget, ...]] = self._build_model_index(upstreams)
        self._health_by_name = {upstream.name: True for upstream in upstreams}
        self._round_robin_index: dict[str, int] = defaultdict(int)
        self._lock = Lock()

    @classmethod
    def from_config(cls, config: AppConfig) -> "RoutingRegistry":
        upstreams = [
            UpstreamTarget(
                name=upstream.name,
                base_url=str(upstream.base_url).rstrip("/"),
                models=tuple(upstream.models),
                authorization_token=upstream.authorization_token,
            )
            for upstream in config.upstreams
        ]
        return cls(upstreams=upstreams)

    @staticmethod
    def _build_model_index(upstreams: list[UpstreamTarget]) -> dict[str, tuple[UpstreamTarget, ...]]:
        model_index: dict[str, list[UpstreamTarget]] = defaultdict(list)
        for upstream in upstreams:
            for model_name in upstream.models:
                model_index[model_name].append(upstream)
        return {model_name: tuple(targets) for model_name, targets in model_index.items()}

    @property
    def model_names(self) -> list[str]:
        return sorted(self._model_to_upstreams)

    def get_upstream(self, upstream_name: str) -> UpstreamTarget:
        try:
            return self._upstreams_by_name[upstream_name]
        except KeyError as exc:
            raise RoutingError(f"unknown upstream '{upstream_name}'") from exc

    def get_model_upstreams(self, model_name: str) -> tuple[UpstreamTarget, ...]:
        try:
            return self._model_to_upstreams[model_name]
        except KeyError as exc:
            raise UnknownModelError(f"unknown model '{model_name}'") from exc

    def list_models(self) -> list[ModelAvailability]:
        with self._lock:
            return [
                ModelAvailability(
                    model_name=model_name,
                    total_upstream_count=len(upstreams),
                    healthy_upstream_count=sum(
                        1 for upstream in upstreams if self._health_by_name.get(upstream.name, False)
                    ),
                )
                for model_name, upstreams in sorted(self._model_to_upstreams.items())
            ]

    def health_snapshots(self) -> list[UpstreamHealthSnapshot]:
        with self._lock:
            return [
                UpstreamHealthSnapshot(upstream_name=name, healthy=healthy)
                for name, healthy in sorted(self._health_by_name.items())
            ]

    def set_upstream_health(self, upstream_name: str, healthy: bool) -> None:
        with self._lock:
            if upstream_name not in self._upstreams_by_name:
                raise RoutingError(f"unknown upstream '{upstream_name}'")
            self._health_by_name[upstream_name] = healthy

    def select_upstream(
        self,
        model_name: str,
        *,
        excluded_upstream_names: set[str] | None = None,
    ) -> SelectedUpstream:
        with self._lock:
            upstreams = self._model_to_upstreams.get(model_name)
            if upstreams is None:
                raise UnknownModelError(f"unknown model '{model_name}'")

            pool_size = len(upstreams)
            start_index = self._round_robin_index[model_name] % pool_size
            excluded_names = excluded_upstream_names or set()

            selected = None
            selected_index = None
            for offset in range(pool_size):
                candidate_index = (start_index + offset) % pool_size
                candidate = upstreams[candidate_index]
                if candidate.name in excluded_names:
                    continue
                if not self._health_by_name.get(candidate.name, False):
                    continue
                selected = candidate
                selected_index = candidate_index
                break

            if selected is None or selected_index is None:
                raise NoHealthyUpstreamError(f"no healthy upstream available for model '{model_name}'")

            self._round_robin_index[model_name] = (selected_index + 1) % pool_size

            return SelectedUpstream(model_name=model_name, upstream=selected)
