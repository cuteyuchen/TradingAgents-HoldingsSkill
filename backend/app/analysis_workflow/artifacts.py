"""Immutable artifact constructors. Artifacts are insert-only."""
from __future__ import annotations

from typing import Any

from .constants import ArtifactType
from .models import AnalysisArtifact
from .serializers import prepare_artifact_content


def build_artifact(
    *,
    analysis_run_id: int,
    artifact_type: str,
    artifact_key: str,
    content: Any,
    stage_id: int | None = None,
    node_id: int | None = None,
    attempt_id: int | None = None,
) -> AnalysisArtifact:
    content_json, content_text, digest, size, redacted = prepare_artifact_content(content)
    mime = "application/json" if content_json is not None else "text/plain"
    encoding = "json" if content_json is not None else "utf-8"
    if artifact_type not in vars(ArtifactType).values() and not str(artifact_type).isupper():
        artifact_type = str(artifact_type).upper()
    return AnalysisArtifact(
        analysis_run_id=analysis_run_id,
        stage_id=stage_id,
        node_id=node_id,
        attempt_id=attempt_id,
        artifact_type=artifact_type,
        artifact_key=artifact_key,
        content_json=content_json,
        content_text=content_text,
        sha256=digest,
        content_size=size,
        redacted=redacted,
        content_encoding=encoding,
        mime_type=mime,
    )
