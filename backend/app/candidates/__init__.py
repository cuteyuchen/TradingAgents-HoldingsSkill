"""Deterministic Phase F candidate engine.

The service layer is intentionally lazy here. Database startup imports the
model module to register tables, and importing the orchestration service from
package initialisation would pull the whole portfolio stack into that path.
"""

from .config import DEFAULT_CONFIG

__all__ = [
    "DEFAULT_CONFIG",
    "get_candidate_run",
    "latest_candidate_context",
    "list_candidate_runs",
    "scan_candidates",
]


def __getattr__(name: str):
    if name in __all__ and name != "DEFAULT_CONFIG":
        from . import service

        return getattr(service, name)
    raise AttributeError(name)
