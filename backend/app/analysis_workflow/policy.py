"""Node retry policy. Separate from model transport/structured retry."""
from __future__ import annotations

from .constants import DEFAULT_NODE_MAX_ATTEMPTS, Criticality, FailureClass, NodeSpec


class NodeRetryPolicy:
    """Node Attempt -> Model Call -> Transport Retry -> Structured Retry."""

    def __init__(self, max_attempts: int = DEFAULT_NODE_MAX_ATTEMPTS) -> None:
        self.max_attempts = max(1, int(max_attempts))

    def max_attempts_for(self, spec: NodeSpec) -> int:
        if not spec.retryable:
            return 1
        return max(1, int(spec.max_attempts or self.max_attempts))

    def remaining_attempts(self, spec: NodeSpec, attempt_count: int) -> int:
        return max(0, self.max_attempts_for(spec) - int(attempt_count or 0))

    def should_retry(self, spec: NodeSpec, failure_class: str, attempt_count: int) -> bool:
        if not spec.retryable or failure_class not in FailureClass.RETRYABLE:
            return False
        return self.remaining_attempts(spec, attempt_count) > 0

    def fail_run_on_terminal(self, spec: NodeSpec) -> bool:
        return spec.criticality == Criticality.MANDATORY

    def degrade_on_terminal(self, spec: NodeSpec) -> bool:
        return spec.criticality == Criticality.IMPORTANT

    def skip_on_terminal(self, spec: NodeSpec) -> bool:
        return spec.criticality == Criticality.OPTIONAL
