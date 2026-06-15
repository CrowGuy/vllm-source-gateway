from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PrivateAttr, ValidationError, model_validator


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    max_request_body_bytes: int = Field(default=4 * 1024 * 1024, gt=0)


class TimeoutsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_seconds: float = Field(ge=0)
    upstream_request_seconds: float = Field(gt=0)
    stream_idle_seconds: float = Field(gt=0)


class HealthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    probe_path: str = "/v1/models"
    check_interval_seconds: float = Field(default=15.0, gt=0)
    request_timeout_seconds: float = Field(default=3.0, gt=0)

    @model_validator(mode="after")
    def validate_probe_path(self) -> "HealthConfig":
        normalized = self.probe_path.strip()
        if not normalized.startswith("/"):
            raise ValueError("health probe_path must start with '/'")
        self.probe_path = normalized
        return self


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["round_robin"] = "round_robin"


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reject_unknown_api_keys: bool = False


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    base_url: HttpUrl
    models: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_models(self) -> "UpstreamConfig":
        normalized = [model.strip() for model in self.models if model.strip()]
        if not normalized:
            raise ValueError("upstream must declare at least one non-empty model name")
        self.models = normalized
        return self


class DepartmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_keys: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_api_keys(self) -> "DepartmentConfig":
        normalized = [api_key.strip() for api_key in self.api_keys if api_key.strip()]
        if not normalized:
            raise ValueError("department must declare at least one non-empty api key")
        self.api_keys = normalized
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _api_key_to_department: dict[str, str] = PrivateAttr(default_factory=dict)

    server: ServerConfig
    timeouts: TimeoutsConfig
    health: HealthConfig = Field(default_factory=HealthConfig)
    routing: RoutingConfig
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    upstreams: list[UpstreamConfig] = Field(min_length=1)
    departments: dict[str, DepartmentConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_constraints(self) -> "AppConfig":
        upstream_names = [upstream.name for upstream in self.upstreams]
        if len(upstream_names) != len(set(upstream_names)):
            raise ValueError("upstream names must be unique")

        seen_api_keys: set[str] = set()
        api_key_to_department: dict[str, str] = {}
        for department, config in self.departments.items():
            normalized_department = department.strip()
            if not normalized_department:
                raise ValueError("department names must be non-empty")

            for api_key in config.api_keys:
                if api_key in seen_api_keys:
                    raise ValueError(f"api key '{api_key}' is assigned to more than one department")
                seen_api_keys.add(api_key)
                api_key_to_department[api_key] = normalized_department

        self._api_key_to_department = api_key_to_department

        return self

    @property
    def model_names(self) -> list[str]:
        names = {model_name for upstream in self.upstreams for model_name in upstream.models}
        return sorted(names)

    @property
    def api_key_to_department(self) -> dict[str, str]:
        return self._api_key_to_department


class ConfigError(RuntimeError):
    """Raised when application configuration cannot be loaded."""


DEFAULT_CONFIG_ENV_VAR = "VLLM_SOURCE_GATEWAY_CONFIG"
DEFAULT_CONFIG_PATH = Path("config.yaml")


def load_config(config_path: str | Path | None = None) -> AppConfig:
    resolved_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH

    if not resolved_path.exists():
        raise ConfigError(
            f"configuration file not found: {resolved_path}. "
            "Create config.yaml from config.example.yaml or set VLLM_SOURCE_GATEWAY_CONFIG."
        )

    try:
        raw_config = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse configuration file {resolved_path}: {exc}") from exc

    if raw_config is None:
        raise ConfigError(f"configuration file is empty: {resolved_path}")

    if not isinstance(raw_config, dict):
        raise ConfigError(f"configuration root must be a mapping: {resolved_path}")

    try:
        return AppConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {resolved_path}: {exc}") from exc
