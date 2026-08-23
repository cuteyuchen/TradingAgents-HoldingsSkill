"""In-memory provider-health tracking and a lightweight circuit breaker."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Lock, RLock
from time import monotonic
from typing import Any, Callable


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

    def restore(self, *, failures: int, opened_elapsed_seconds: float | None = None) -> None:
        """Restore a persisted breaker without serializing a monotonic clock.

        ``opened_elapsed_seconds`` is measured from a wall-clock failure time
        at the process boundary.  Internally the breaker continues to use its
        monotonic clock, so NTP adjustments cannot affect a live circuit.
        """

        with self._lock:
            self._failures = max(0, int(failures))
            self._probe_in_flight = False
            if opened_elapsed_seconds is None:
                self._opened_at = None
            else:
                self._opened_at = self._clock() - max(0.0, float(opened_elapsed_seconds))


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
        self._lock = RLock()

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
        with self._lock:
            self._sync_status(state, breaker)
        return state

    def breaker(self, provider: str, data_type: str = "quote") -> CircuitBreaker:
        return self._get(provider, data_type)[1]

    def allow(self, provider: str, data_type: str = "quote") -> bool:
        state, breaker = self._get(provider, data_type)
        allowed = breaker.allow_request()
        with self._lock:
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
        breaker.record_success()
        with self._lock:
            state.success_count += 1
            state.consecutive_failures = 0
            state.last_success_at = datetime.now(UTC)
            state.last_latency_ms = latency_ms
            state.last_error = None
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
        breaker.record_failure()
        with self._lock:
            state.failure_count += 1
            state.consecutive_failures += 1
            state.last_failure_at = datetime.now(UTC)
            state.last_latency_ms = latency_ms
            state.last_error = str(error)
            self._sync_status(state, breaker)
        return state

    def snapshot(self) -> list[ProviderHealth]:
        with self._lock:
            values = list(self._states.values())
            for state in values:
                self._sync_status(state, self.breaker(state.provider_name, state.data_type))
        return values

    def hydrate(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        overwrite: bool = False,
    ) -> list[ProviderHealth]:
        """Restore process state from durable health metadata after a restart.

        The database cannot persist ``monotonic()`` values.  For an open
        circuit, reconstruct its age from ``last_failure_at`` so the remaining
        cooldown is honored instead of silently reopening the provider.
        Existing runtime state wins by default because it may already contain
        newer requests than the durable projection.
        """

        now = datetime.now(UTC)
        restored: list[ProviderHealth] = []
        for raw_row in rows:
            provider = str(raw_row.get("provider_name") or "").strip().lower()
            data_type = str(raw_row.get("data_type") or "quote").strip().lower()
            if not provider:
                continue
            key = (provider, data_type)
            with self._lock:
                if key in self._states and not overwrite:
                    restored.append(self._states[key])
                    continue
            state, breaker = self._get(provider, data_type)
            raw_status = str(raw_row.get("status") or "HEALTHY").upper()
            consecutive_failures = max(0, int(raw_row.get("consecutive_failures") or 0))
            failure_count = max(0, int(raw_row.get("failure_count") or 0))
            last_failure_at = _as_utc_datetime(raw_row.get("last_failure_at"))
            is_open = raw_status in {
                ProviderHealthStatus.CIRCUIT_OPEN.value,
                ProviderHealthStatus.RECOVERING.value,
            }
            elapsed_seconds = None
            if is_open:
                elapsed_seconds = (
                    max(0.0, (now - last_failure_at).total_seconds())
                    if last_failure_at
                    else 0.0
                )
            breaker.restore(
                failures=max(consecutive_failures, self.failure_threshold if is_open else 0),
                opened_elapsed_seconds=elapsed_seconds,
            )
            with self._lock:
                state.success_count = max(0, int(raw_row.get("success_count") or 0))
                state.failure_count = failure_count
                state.consecutive_failures = consecutive_failures
                state.last_success_at = _as_utc_datetime(raw_row.get("last_success_at"))
                state.last_failure_at = last_failure_at
                state.last_error = str(raw_row.get("last_error")) if raw_row.get("last_error") else None
                latency = raw_row.get("last_latency_ms")
                state.last_latency_ms = float(latency) if latency is not None else None
                self._sync_status(state, breaker)
                restored.append(state)
        return restored

    def clear(self) -> None:
        """Forget runtime counters, primarily for controlled lifecycle resets."""

        with self._lock:
            self._states.clear()
            self._breakers.clear()


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


ProviderHealthRegistry = ProviderHealthTracker


_runtime_registry: ProviderHealthRegistry | None = None
_runtime_registry_lock = Lock()


def _runtime_defaults() -> tuple[int, float]:
    # Import lazily so the provider primitives remain usable in standalone
    # tests and tools that do not initialize the FastAPI application.
    try:
        from ...config import settings

        return settings.PROVIDER_FAILURE_THRESHOLD, settings.PROVIDER_CIRCUIT_COOLDOWN_SECONDS
    except (ImportError, AttributeError):
        return 3, 60.0


def get_runtime_provider_health_registry() -> ProviderHealthRegistry:
    """Return the process-wide registry shared by independently built chains."""

    global _runtime_registry
    with _runtime_registry_lock:
        if _runtime_registry is None:
            failure_threshold, cooldown_seconds = _runtime_defaults()
            _runtime_registry = ProviderHealthRegistry(
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
        return _runtime_registry


def reset_runtime_provider_health_registry(
    *,
    failure_threshold: int | None = None,
    cooldown_seconds: float | None = None,
    clock: Callable[[], float] | None = None,
) -> ProviderHealthRegistry:
    """Replace the shared registry for tests or an explicit app restart hook."""

    global _runtime_registry
    default_threshold, default_cooldown = _runtime_defaults()
    with _runtime_registry_lock:
        _runtime_registry = ProviderHealthRegistry(
            failure_threshold=failure_threshold if failure_threshold is not None else default_threshold,
            cooldown_seconds=cooldown_seconds if cooldown_seconds is not None else default_cooldown,
            clock=clock,
        )
        return _runtime_registry


def runtime_provider_health_snapshot() -> list[dict[str, object]]:
    """Return a serializable copy suitable for persistence or health APIs."""

    return [state.to_dict() for state in get_runtime_provider_health_registry().snapshot()]
