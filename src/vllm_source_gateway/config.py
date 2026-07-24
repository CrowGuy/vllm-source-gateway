from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PrivateAttr,
    ValidationError,
    model_validator,
)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    max_request_body_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    max_sse_decode_buffer_bytes: int = Field(default=256 * 1024, gt=0)


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
    def validate_probe_path(self) -> HealthConfig:
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


class ModelConcurrencyLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_active_requests: int = Field(gt=0)


class DepartmentModelConcurrencyLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    max_active_requests: int = Field(gt=0)

    @model_validator(mode="after")
    def normalize_keys(self) -> DepartmentModelConcurrencyLimitConfig:
        self.department = self.department.strip()
        self.model_name = self.model_name.strip()
        if not self.department or not self.model_name:
            raise ValueError("department and model_name must be non-empty")
        return self


class TokenBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    window_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def normalize_keys(self) -> TokenBudgetConfig:
        self.department = self.department.strip()
        self.model_name = self.model_name.strip()
        if not self.department or not self.model_name:
            raise ValueError("department and model_name must be non-empty")
        return self


class RequestShapeLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_request_body_bytes: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    reject_n_greater_than_one: bool = True


class RetryGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    window_seconds: float = Field(default=60.0, gt=0)
    max_events: int = Field(default=10, gt=0)
    cooldown_seconds: float = Field(default=30.0, gt=0)


class AdmissionControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_retry_after_seconds: int = Field(default=30, gt=0)
    global_model_limits: dict[str, ModelConcurrencyLimitConfig] = Field(default_factory=dict)
    department_model_limits: list[DepartmentModelConcurrencyLimitConfig] = Field(
        default_factory=list
    )
    token_budgets: list[TokenBudgetConfig] = Field(default_factory=list)
    request_shape_limits: dict[str, RequestShapeLimitConfig] = Field(default_factory=dict)
    retry_guard: RetryGuardConfig = Field(default_factory=RetryGuardConfig)

    @model_validator(mode="after")
    def normalize_model_keys(self) -> AdmissionControlConfig:
        normalized_global_limits = {}
        for model_name, limit in self.global_model_limits.items():
            normalized = model_name.strip()
            if not normalized:
                raise ValueError("admission_control global_model_limits keys must be non-empty")
            normalized_global_limits[normalized] = limit
        self.global_model_limits = normalized_global_limits

        normalized_shape_limits = {}
        for model_name, limit in self.request_shape_limits.items():
            normalized = model_name.strip()
            if not normalized:
                raise ValueError("admission_control request_shape_limits keys must be non-empty")
            normalized_shape_limits[normalized] = limit
        self.request_shape_limits = normalized_shape_limits
        return self


class ModelCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    hosted_on: str | None = None
    use_cases: list[str] = Field(default_factory=list)
    api_paths: list[str] = Field(default_factory=list)
    context_window: str | None = None
    recommended_for: list[str] = Field(default_factory=list)
    known_limits: list[str] = Field(default_factory=list)
    example_prompt: str | None = None

    @staticmethod
    def _normalize_optional_string(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_string_list(values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @staticmethod
    def _validate_hosted_on(value: str | None) -> None:
        if value is None:
            return
        if "://" in value or re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value):
            raise ValueError(
                "model_catalog hosted_on must be an abstract location description, "
                "not an upstream URL or IP address"
            )

    @model_validator(mode="after")
    def normalize_fields(self) -> ModelCatalogEntry:
        self.display_name = self._normalize_optional_string(self.display_name)
        self.hosted_on = self._normalize_optional_string(self.hosted_on)
        self._validate_hosted_on(self.hosted_on)
        self.context_window = self._normalize_optional_string(self.context_window)
        self.example_prompt = self._normalize_optional_string(self.example_prompt)
        self.use_cases = self._normalize_string_list(self.use_cases)
        self.api_paths = self._normalize_string_list(self.api_paths)
        self.recommended_for = self._normalize_string_list(self.recommended_for)
        self.known_limits = self._normalize_string_list(self.known_limits)
        unsupported_api_paths = sorted(set(self.api_paths) - {"chat", "responses", "messages"})
        if unsupported_api_paths:
            raise ValueError(
                "model_catalog api_paths may only include supported gateway paths: "
                f"chat, responses, messages; got {unsupported_api_paths}"
            )
        return self


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _authorization_token: str | None = PrivateAttr(default=None)

    name: str = Field(min_length=1)
    base_url: HttpUrl
    models: list[str] = Field(min_length=1)
    authorization_from_env: str | None = None

    @staticmethod
    def _resolve_authorization_token(env_var_name: str) -> str:
        raw_value = os.environ.get(env_var_name)
        if raw_value is None:
            raise ValueError(f"upstream authorization_from_env '{env_var_name}' is not set")

        token = raw_value.strip()
        if not token:
            raise ValueError(f"upstream authorization_from_env '{env_var_name}' is empty")

        return token

    @model_validator(mode="after")
    def validate_models(self) -> UpstreamConfig:
        normalized = [model.strip() for model in self.models if model.strip()]
        if not normalized:
            raise ValueError("upstream must declare at least one non-empty model name")
        self.models = normalized

        if self.authorization_from_env is not None:
            env_var_name = self.authorization_from_env.strip()
            if not env_var_name:
                raise ValueError("upstream authorization_from_env must be non-empty")
            self.authorization_from_env = env_var_name
            self._authorization_token = self._resolve_authorization_token(env_var_name)

        return self

    @property
    def authorization_token(self) -> str | None:
        return self._authorization_token


class DepartmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_keys: list[str] | None = None
    api_keys_from_env: str | None = None

    @staticmethod
    def _parse_api_keys_env_value(env_var_name: str) -> list[str]:
        raw_value = os.environ.get(env_var_name)
        if raw_value is None:
            raise ValueError(f"department api_keys_from_env '{env_var_name}' is not set")

        normalized_value = raw_value.strip()
        if not normalized_value:
            raise ValueError(f"department api_keys_from_env '{env_var_name}' is empty")

        parsed_values: list[str]
        if normalized_value.startswith("["):
            try:
                parsed_json = json.loads(normalized_value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"department api_keys_from_env '{env_var_name}' must be a valid "
                    "JSON array or comma-separated string"
                ) from exc
            if not isinstance(parsed_json, list) or not all(
                isinstance(item, str) for item in parsed_json
            ):
                raise ValueError(
                    f"department api_keys_from_env '{env_var_name}' must resolve to "
                    "a list of strings"
                )
            parsed_values = parsed_json
        else:
            parsed_values = normalized_value.split(",")

        normalized_api_keys = [api_key.strip() for api_key in parsed_values if api_key.strip()]
        if not normalized_api_keys:
            raise ValueError(
                f"department api_keys_from_env '{env_var_name}' must contain at least "
                "one non-empty api key"
            )

        return normalized_api_keys

    @model_validator(mode="after")
    def validate_api_keys(self) -> DepartmentConfig:
        has_inline_api_keys = self.api_keys is not None
        has_api_keys_from_env = self.api_keys_from_env is not None
        if has_inline_api_keys == has_api_keys_from_env:
            raise ValueError("department must declare exactly one of api_keys or api_keys_from_env")

        if has_api_keys_from_env:
            env_var_name = (
                self.api_keys_from_env.strip() if self.api_keys_from_env is not None else ""
            )
            if not env_var_name:
                raise ValueError("department api_keys_from_env must be non-empty")
            self.api_keys_from_env = env_var_name
            self.api_keys = self._parse_api_keys_env_value(env_var_name)

        assert self.api_keys is not None
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
    admission_control: AdmissionControlConfig = Field(default_factory=AdmissionControlConfig)
    upstreams: list[UpstreamConfig] = Field(min_length=1)
    departments: dict[str, DepartmentConfig] = Field(min_length=1)
    model_catalog: dict[str, ModelCatalogEntry] = Field(default_factory=dict)

    @staticmethod
    def _format_allowed(values: set[str]) -> str:
        return ", ".join(sorted(values))

    @classmethod
    def _validate_known_admission_model(
        cls,
        *,
        field_name: str,
        model_name: str,
        allowed_model_names: set[str],
    ) -> None:
        if model_name not in allowed_model_names:
            raise ValueError(
                f"admission_control {field_name} references unknown model '{model_name}'; "
                f"allowed models: {cls._format_allowed(allowed_model_names)}"
            )

    @classmethod
    def _validate_known_admission_department(
        cls,
        *,
        field_name: str,
        department: str,
        allowed_departments: set[str],
    ) -> None:
        if department not in allowed_departments:
            raise ValueError(
                f"admission_control {field_name} references unknown department '{department}'; "
                f"allowed departments: {cls._format_allowed(allowed_departments)}"
            )

    @classmethod
    def _validate_unique_admission_pairs(
        cls,
        *,
        field_name: str,
        pairs: list[tuple[str, str]],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for department, model_name in pairs:
            pair = (department, model_name)
            if pair in seen:
                raise ValueError(
                    f"admission_control {field_name} contains duplicate "
                    f"department/model pair '{department}'/'{model_name}'"
                )
            seen.add(pair)

    @model_validator(mode="after")
    def validate_unique_constraints(self) -> AppConfig:
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

        if self.admission_control.enabled:
            allowed_model_names = {
                model_name for upstream in self.upstreams for model_name in upstream.models
            }
            allowed_departments = set(api_key_to_department.values())

            for model_name in self.admission_control.global_model_limits:
                self._validate_known_admission_model(
                    field_name="global_model_limits",
                    model_name=model_name,
                    allowed_model_names=allowed_model_names,
                )
            for model_name in self.admission_control.request_shape_limits:
                self._validate_known_admission_model(
                    field_name="request_shape_limits",
                    model_name=model_name,
                    allowed_model_names=allowed_model_names,
                )

            department_model_pairs = [
                (limit.department, limit.model_name)
                for limit in self.admission_control.department_model_limits
            ]
            token_budget_pairs = [
                (budget.department, budget.model_name)
                for budget in self.admission_control.token_budgets
            ]
            self._validate_unique_admission_pairs(
                field_name="department_model_limits",
                pairs=department_model_pairs,
            )
            self._validate_unique_admission_pairs(
                field_name="token_budgets",
                pairs=token_budget_pairs,
            )

            for limit in self.admission_control.department_model_limits:
                self._validate_known_admission_department(
                    field_name="department_model_limits",
                    department=limit.department,
                    allowed_departments=allowed_departments,
                )
                self._validate_known_admission_model(
                    field_name="department_model_limits",
                    model_name=limit.model_name,
                    allowed_model_names=allowed_model_names,
                )
            for budget in self.admission_control.token_budgets:
                self._validate_known_admission_department(
                    field_name="token_budgets",
                    department=budget.department,
                    allowed_departments=allowed_departments,
                )
                self._validate_known_admission_model(
                    field_name="token_budgets",
                    model_name=budget.model_name,
                    allowed_model_names=allowed_model_names,
                )

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
