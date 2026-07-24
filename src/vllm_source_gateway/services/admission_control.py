from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException, status

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.metrics import GatewayMetrics


@dataclass(frozen=True, slots=True)
class AdmissionLease:
    department: str
    model_name: str
    endpoint: str


class AdmissionController:
    def __init__(
        self,
        *,
        config: AppConfig,
        metrics: GatewayMetrics,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config.admission_control
        self._metrics = metrics
        self._time_source = time_source
        self._lock = Lock()
        self._active_by_model: dict[str, int] = defaultdict(int)
        self._active_by_department_model: dict[tuple[str, str], int] = defaultdict(int)
        self._token_events: dict[tuple[str, str], deque[tuple[float, int]]] = defaultdict(deque)
        self._token_totals: dict[tuple[str, str], int] = defaultdict(int)
        self._retry_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._cooldown_until: dict[tuple[str, str], float] = {}

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def request_retry_after_seconds(self) -> int:
        return self._config.default_retry_after_seconds

    def check_request_shape(
        self,
        *,
        department: str,
        model_name: str,
        endpoint: str,
        body_size: int,
        payload: dict,
    ) -> dict:
        if not self.enabled:
            return payload

        limit = self._config.request_shape_limits.get(model_name)
        if limit is None:
            return payload

        if limit.max_request_body_bytes is not None and body_size > limit.max_request_body_bytes:
            self._reject_shape(
                department=department,
                model_name=model_name,
                reason="request_body_too_large",
            )

        has_output_cap, max_output_tokens = _extract_valid_max_output_tokens(payload)
        if limit.max_output_tokens is not None:
            if max_output_tokens is None and has_output_cap:
                self._reject_shape(
                    department=department,
                    model_name=model_name,
                    reason="max_output_tokens",
                )
            if max_output_tokens is not None and max_output_tokens > limit.max_output_tokens:
                self._reject_shape(
                    department=department,
                    model_name=model_name,
                    reason="max_output_tokens",
                )

        if limit.reject_n_greater_than_one and payload.get("n", 1) != 1:
            self._reject_shape(
                department=department,
                model_name=model_name,
                reason="n_greater_than_one",
            )

        if limit.max_output_tokens is not None and not has_output_cap:
            payload = dict(payload)
            output_key = "max_output_tokens" if endpoint == "responses" else "max_tokens"
            payload[output_key] = limit.max_output_tokens

        return payload

    def acquire(self, *, department: str, model_name: str, endpoint: str) -> AdmissionLease | None:
        if not self.enabled:
            return None

        now = self._time_source()
        key = (department, model_name)

        with self._lock:
            cooldown_until = self._cooldown_until.get(key, 0.0)
            if cooldown_until > now:
                self._record_rejection_locked(
                    department=department,
                    model_name=model_name,
                    reason="retry_guard_open",
                )
                raise _too_many_requests(
                    "retry guard is open",
                    retry_after_seconds=max(
                        1,
                        int(cooldown_until - now),
                    ),
                )

            model_limit = self._config.global_model_limits.get(model_name)
            if (
                model_limit is not None
                and self._active_by_model[model_name] >= model_limit.max_active_requests
            ):
                self._record_rejection_locked(
                    department=department,
                    model_name=model_name,
                    reason="model_concurrency",
                )
                raise _too_many_requests(
                    "model concurrency limit exceeded",
                    retry_after_seconds=self._config.default_retry_after_seconds,
                )

            department_limit = self._department_model_limit(department, model_name)
            if (
                department_limit is not None
                and self._active_by_department_model[key] >= department_limit
            ):
                self._record_rejection_locked(
                    department=department,
                    model_name=model_name,
                    reason="department_model_concurrency",
                )
                raise _too_many_requests(
                    "department model concurrency limit exceeded",
                    retry_after_seconds=self._config.default_retry_after_seconds,
                )

            budget = self._token_budget(department, model_name)
            if budget is not None:
                events = self._token_events[key]
                removed_tokens = _drop_old_token_events(
                    events,
                    now=now,
                    window_seconds=budget.window_seconds,
                )
                if removed_tokens:
                    self._token_totals[key] = max(0, self._token_totals[key] - removed_tokens)
                if self._token_totals[key] >= budget.max_tokens:
                    self._record_rejection_locked(
                        department=department,
                        model_name=model_name,
                        reason="token_budget",
                    )
                    self._metrics.record_token_budget_rejection(
                        department=department,
                        model_name=model_name,
                    )
                    raise _too_many_requests(
                        "token budget exceeded",
                        retry_after_seconds=self._config.default_retry_after_seconds,
                    )

            self._active_by_model[model_name] += 1
            self._active_by_department_model[key] += 1
            self._metrics.inc_inflight_request(
                department=department,
                model_name=model_name,
                endpoint=endpoint,
            )
            return AdmissionLease(department=department, model_name=model_name, endpoint=endpoint)

    def release(self, lease: AdmissionLease | None) -> None:
        if lease is None:
            return

        key = (lease.department, lease.model_name)
        with self._lock:
            self._active_by_model[lease.model_name] = max(
                0,
                self._active_by_model[lease.model_name] - 1,
            )
            self._active_by_department_model[key] = max(
                0,
                self._active_by_department_model[key] - 1,
            )
            self._metrics.dec_inflight_request(
                department=lease.department,
                model_name=lease.model_name,
                endpoint=lease.endpoint,
            )

    def record_tokens(self, *, department: str, model_name: str, tokens: int) -> None:
        if not self.enabled or tokens <= 0:
            return

        budget = self._token_budget(department, model_name)
        if budget is None:
            return

        now = self._time_source()
        with self._lock:
            key = (department, model_name)
            events = self._token_events[key]
            removed_tokens = _drop_old_token_events(
                events,
                now=now,
                window_seconds=budget.window_seconds,
            )
            if removed_tokens:
                self._token_totals[key] = max(0, self._token_totals[key] - removed_tokens)
            events.append((now, tokens))
            self._token_totals[key] += tokens

    def record_retry_event(self, *, department: str, model_name: str) -> None:
        if not self.enabled or not self._config.retry_guard.enabled:
            return

        with self._lock:
            self._record_retry_event_locked(department=department, model_name=model_name)

    def _record_rejection_locked(self, *, department: str, model_name: str, reason: str) -> None:
        self._metrics.record_admission_rejection(
            department=department,
            model_name=model_name,
            reason=reason,
        )

    def _record_retry_event_locked(self, *, department: str, model_name: str) -> None:
        now = self._time_source()
        key = (department, model_name)
        guard = self._config.retry_guard
        if self._cooldown_until.get(key, 0.0) > now:
            return

        events = self._retry_events[key]
        while events and now - events[0] > guard.window_seconds:
            events.popleft()
        events.append(now)
        if len(events) >= guard.max_events and self._cooldown_until.get(key, 0.0) <= now:
            self._cooldown_until[key] = now + guard.cooldown_seconds
            events.clear()
            self._metrics.record_retry_guard_open(department=department, model_name=model_name)

    def _department_model_limit(self, department: str, model_name: str) -> int | None:
        for limit in self._config.department_model_limits:
            if limit.department == department and limit.model_name == model_name:
                return limit.max_active_requests
        return None

    def _token_budget(self, department: str, model_name: str):
        for budget in self._config.token_budgets:
            if budget.department == department and budget.model_name == model_name:
                return budget
        return None

    def _reject_shape(self, *, department: str, model_name: str, reason: str) -> None:
        with self._lock:
            self._record_rejection_locked(
                department=department,
                model_name=model_name,
                reason=reason,
            )
        raise _shape_error(
            status_code=_SHAPE_ERROR_STATUS[reason],
            detail=_SHAPE_ERROR_DETAIL[reason],
        )


_SHAPE_ERROR_STATUS = {
    "request_body_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "max_output_tokens": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "n_greater_than_one": status.HTTP_422_UNPROCESSABLE_CONTENT,
}
_SHAPE_ERROR_DETAIL = {
    "request_body_too_large": "request body too large",
    "max_output_tokens": "max output tokens limit exceeded or invalid",
    "n_greater_than_one": "n greater than one is not allowed",
}


def _shape_error(*, status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _too_many_requests(detail: str, *, retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _extract_valid_max_output_tokens(payload: dict) -> tuple[bool, int | None]:
    values = []
    for key in ("max_tokens", "max_output_tokens"):
        if key not in payload:
            continue

        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return True, None
        values.append(value)

    if not values:
        return False, None
    return True, max(values)


def _drop_old_token_events(
    events: deque[tuple[float, int]],
    *,
    now: float,
    window_seconds: float,
) -> int:
    removed_tokens = 0
    while events and now - events[0][0] > window_seconds:
        _, tokens = events.popleft()
        removed_tokens += tokens
    return removed_tokens
