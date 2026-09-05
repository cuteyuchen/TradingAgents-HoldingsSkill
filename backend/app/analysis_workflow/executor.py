"""Durable node executor with retry, skip, and criticality handling."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from .constants import ArtifactType, Criticality, FailureClass, NodeStatus, node_spec
from .context import compress_payload, next_context_mode
from .failures import classify_failure
from .policy import NodeRetryPolicy
from .resume import should_skip_node


@dataclass
class NodeExecuteResult:
    node_key: str
    status: str
    output: Any = None
    skipped: bool = False
    degraded: bool = False
    warning: str | None = None
    failure_class: str | None = None
    attempt_count: int = 0


class NodeExecutor:
    """Workflow Node -> Attempt -> Result/Error -> Checkpoint."""

    def __init__(self, recorder, policy: NodeRetryPolicy | None = None) -> None:
        self.recorder = recorder
        self.policy = policy or NodeRetryPolicy()

    def execute(
        self,
        node_key: str,
        fn: Callable[..., Any],
        *,
        input_payload: Any = None,
        output_artifact_type: str | None = None,
        profile=None,
        metadata: dict[str, Any] | None = None,
        fail_closed: bool = False,
    ) -> NodeExecuteResult:
        spec = node_spec(node_key)
        completed = list(getattr(self.recorder, "_completed_nodes", []) or [])
        force_restart = bool(getattr(self.recorder, "force_restart", False))
        if should_skip_node(node_key, completed, force_restart=force_restart):
            output = self.recorder.load_node_output(node_key)
            existing = self.recorder.node_by_key(node_key)
            return NodeExecuteResult(
                node_key=node_key,
                status=existing.status if existing is not None else NodeStatus.SUCCEEDED,
                output=output,
                skipped=True,
                attempt_count=int(getattr(existing, "attempt_count", 0) or 0),
            )

        node = self.recorder.start_node(node_key, metadata=metadata)
        if self.recorder.node_skipped:
            return NodeExecuteResult(
                node_key=node_key,
                status=node.status,
                output=self.recorder.load_node_output(node_key),
                skipped=True,
                attempt_count=int(node.attempt_count or 0),
            )

        provider = model = profile_id = None
        if profile is not None:
            try:
                provider = getattr(getattr(profile, "provider", None), "provider", None)
            except Exception:  # noqa: BLE001
                provider = None
            model = getattr(profile, "model_name", None)
            profile_id = getattr(profile, "id", None)

        context_mode = "full"
        last_error: BaseException | None = None
        last_class: str | None = None
        accepts_mode = _accepts_context_mode(fn)
        max_attempts = self.policy.max_attempts_for(spec)

        while True:
            attempt = self.recorder.start_attempt(provider=provider, model=model, model_profile_id=profile_id)
            if input_payload is not None:
                payload = compress_payload(input_payload, context_mode) if context_mode != "full" else input_payload
                self.recorder.bind_input_hash(f"{node_key}.{context_mode}", payload)
                if spec.llm:
                    self.recorder.record_artifact(
                        ArtifactType.RENDERED_PROMPT,
                        payload,
                        artifact_key=f"{node_key}.prompt.{attempt.attempt_no}",
                    )
            try:
                output = fn(context_mode) if accepts_mode else fn()
                if output_artifact_type and output is not None:
                    artifact = self.recorder.record_artifact(
                        output_artifact_type,
                        output,
                        artifact_key=f"{node_key}.output.{attempt.attempt_no}",
                    )
                    self.recorder.finish_attempt(output_hash=artifact.sha256)
                else:
                    self.recorder.finish_attempt(output=output)
                self.recorder.finish_node(output=output)
                return NodeExecuteResult(
                    node_key=node_key,
                    status=NodeStatus.SUCCEEDED,
                    output=output,
                    attempt_count=int(self.recorder.node_by_key(node_key).attempt_count if self.recorder.node_by_key(node_key) else attempt.attempt_no),
                )
            except Exception as exc:
                last_error = exc
                last_class = classify_failure(exc)
                attempt_count = int(attempt.attempt_no or 0)
                retry = self.policy.should_retry(spec, last_class, attempt_count) and attempt_count < max_attempts
                if retry and last_class == FailureClass.CONTEXT_OVERFLOW:
                    nxt = next_context_mode(context_mode)
                    if nxt is None:
                        retry = False
                    else:
                        context_mode = nxt
                self.recorder.fail_attempt(
                    exc,
                    retryable=retry,
                    failure_class=last_class,
                    waiting_retry=retry,
                    structured_retry_count=getattr(exc, "retry_count", None),
                    transport_retry_count=getattr(exc, "transport_retry_count", None),
                )
                if retry:
                    continue
                break

        assert last_error is not None
        if self.policy.skip_on_terminal(spec):
            self.recorder.fail_node(last_error)
            node_row = self.recorder.node_by_key(node_key)
            if node_row is not None:
                node_row.status = NodeStatus.SKIPPED
                self.recorder._commit()
            return NodeExecuteResult(
                node_key=node_key,
                status=NodeStatus.SKIPPED,
                skipped=True,
                warning=str(last_error)[:500],
                failure_class=last_class,
                attempt_count=int(getattr(self.recorder.node_by_key(node_key), "attempt_count", 0) or 0),
            )
        self.recorder.fail_node(last_error)
        result = NodeExecuteResult(
            node_key=node_key,
            status=NodeStatus.FAILED,
            degraded=self.policy.degrade_on_terminal(spec),
            warning=str(last_error)[:500] if spec.criticality == Criticality.IMPORTANT else None,
            failure_class=last_class,
            attempt_count=int(getattr(self.recorder.node_by_key(node_key), "attempt_count", 0) or 0),
        )
        if fail_closed or self.policy.fail_run_on_terminal(spec):
            raise last_error
        return result


def _accepts_context_mode(fn: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "context_mode" in parameters or any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
