"""Request contracts for the server-owned Candidate Engine API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


CandidateScanMode = Literal["fast", "standard", "deep"]


class CandidateScanRequest(BaseModel):
    mode: CandidateScanMode = "standard"
    as_of: datetime | None = None

    model_config = ConfigDict(extra="forbid")


__all__ = ["CandidateScanMode", "CandidateScanRequest"]
