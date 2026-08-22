"""In-memory provider-health tracking and a lightweight circuit breaker."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from time import monotonic
from typing import Callable


class ProviderHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    RECOVERING = "RECOVERING"


class CircuitState(str, Enum):
    HEALTHY = "HEALTHY"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    RECOVERING = "RECOVERING"


@dataclass(slots=True)
class ProviderHealth:
    provider_name: str
    data_type: str = "quote"
    status: ProviderHealthStatus = ProviderHealthStatus.HEALTHY
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["status"] = self.status.value
        data["last_success_at"] = self.last_success_at.isoformat() if self.last_success_at else None
        data["last_failure_at"] = self.last_failure_at.isoformat() if self.last_failure_at else None
        return data


ProviderHealthState = ProviderHealth


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock or monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._opened_at is None:
                return CircuitState.HEALTHY
            if self._clock() - self._opened_at >= self.cooldown_seconds:
                return CircuitState.RECOVERING
            return CircuitState.CIRCUIT_OPEN

    def allow_request(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self.cooldown_seconds:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()

    def reset(self) -> None:
        self.record_success()


class ProviderHealthTracker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or monotonic
        self._states: dict[tuple[str, str], ProviderHealth] = {}
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}
        self._lock = Lock()

    def _get(self, provider: str, data_type: str) -> tuple[ProviderHealth, CircuitBreaker]:
        key = (str(provider).lower(), str(data_type).lower())
        with self._lock:
            state = self._states.setdefault(key, ProviderHealth(key[0], key[1]))
            breaker = self._breakers.setdefault(
                key,
                CircuitBreaker(
                    failure_threshold=self.failure_threshold,
                    cooldown_seconds=self.cooldown_seconds,
                    clock=self._clock,
                ),
            )
        return state, breaker

    def get(self, provider: str, data_type: str = "quote") -> ProviderHealth:
        state, breaker = self._get(provider, data_type)
        self._sync_status(state, breaker)
        return state

    def breaker(self, provider: str, data_type: str = "quote") -> CircuitBreaker:
        return self._get(provider, data_type)[1]

    def allow(self, provider: str, data_type: str = "quote") -> bool:
        state, breaker = self._get(provider, data_type)
        allowed = breaker.allow_request()
        self._sync_status(state, breaker)
        return allowed

    @staticmethod
    def _sync_status(state: ProviderHealth, breaker: CircuitBreaker) -> None:
        circuit_state = breaker.state
        if circuit_state == CircuitState.CIRCUIT_OPEN:
            state.status = ProviderHealthStatus.CIRCUIT_OPEN
        elif circuit_state == CircuitState.RECOVERING:
            state.status = ProviderHealthStatus.RECOVERING
        elif state.consecutive_failures == 0:
            state.status = ProviderHealthStatus.HEALTHY
        else:
            state.status = ProviderHealthStatus.DEGRADED

    def record_success(
        self,
        provider: str,
        data_type: str = "quote",
        latency_ms: float | None = None,
    ) -> ProviderHealth:
        state, breaker = self._get(provider, data_type)
        state.success_count += 1
        state.consecutive_failures = 0
        state.last_success_at = datetime.now(UTC)
        state.last_latency_ms = latency_ms
        state.last_error = None
        breaker.record_success()
        self._sync_status(state, breaker)
        return state

    def record_failure(
        self,
        provider: str,
        error: str,
        data_type: str = "quote",
        latency_ms: float | None = None,
    ) -> ProviderHealth:
        state, breaker = self._get(provider, data_type)
        state.failure_count += 1
        state.consecutive_failures += 1
        state.last_failure_at = datetime.now(UTC)
        state.last_latency_ms = latency_ms
        state.last_error = str(error)
        breaker.record_failure()
        self._sync_status(state, breaker)
        return state

    def snapshot(self) -> list[ProviderHealth]:
        with self._lock:
            values = list(self._states.values())
        for state in values:
            self._sync_status(state, self.breaker(state.provider_name, state.data_type))
        return values


ProviderHealthRegistry = ProviderHealthTracker
