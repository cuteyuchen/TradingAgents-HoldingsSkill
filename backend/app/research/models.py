"""Research-owned persistence for historical replay and calibration evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class BacktestRun(Base):
    """Durable research job.  It is never an AnalysisJob or a live decision."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        UniqueConstraint("calculation_key", name="uq_backtest_runs_calculation_key"),
        Index("ix_backtest_runs_owner_status", "user_id", "portfolio_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    replay_mode: Mapped[str] = mapped_column(String(32), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[str] = mapped_column(String(32), default="DATA_AUDIT", index=True)
    config_version: Mapped[str] = mapped_column(String(128), default="candidate-engine-v1")
    engine_version: Mapped[str] = mapped_column(String(64), default="historical-replay-v1")
    baseline_config_json: Mapped[dict | None] = mapped_column(JSON)
    experiment_config_json: Mapped[dict | None] = mapped_column(JSON)
    data_manifest_json: Mapped[dict | None] = mapped_column(JSON)
    data_hash: Mapped[str] = mapped_column(String(64), index=True)
    calculation_key: Mapped[str] = mapped_column(String(255), index=True)
    random_seed: Mapped[int] = mapped_column(Integer, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_trade_dates: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(32), default="MISSING", index=True)
    leakage_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    result_summary_json: Mapped[dict | None] = mapped_column(JSON)
    failure_counts_json: Mapped[dict | None] = mapped_column(JSON)
    horizons_json: Mapped[list | None] = mapped_column(JSON)
    known_limitations_json: Mapped[list | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    metric_slices: Mapped[list["BacktestMetricSlice"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="BacktestMetricSlice.id"
    )
    calibration_reports: Mapped[list["CalibrationReport"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="CalibrationReport.id"
    )


class BacktestMetricSlice(Base):
    """Compact aggregate; no per-security-per-day observation table is created."""

    __tablename__ = "backtest_metric_slices"
    __table_args__ = (
        UniqueConstraint("run_id", "slice_key", name="uq_backtest_metric_slices_run_key"),
        Index("ix_backtest_metric_slices_lookup", "run_id", "metric_family", "horizon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    slice_key: Mapped[str] = mapped_column(String(255), index=True)
    metric_family: Mapped[str] = mapped_column(String(64), index=True)
    security_type: Mapped[str | None] = mapped_column(String(24), index=True)
    market_regime: Mapped[str | None] = mapped_column(String(32), index=True)
    stage: Mapped[str | None] = mapped_column(String(24), index=True)
    score_bucket: Mapped[str | None] = mapped_column(String(32), index=True)
    horizon: Mapped[int | None] = mapped_column(Integer, index=True)
    parameter_variant: Mapped[str | None] = mapped_column(String(128), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    trade_date_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float | None] = mapped_column(Float)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    confidence_interval_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(32), default="INSUFFICIENT", index=True)
    limitations_json: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    run: Mapped[BacktestRun] = relationship(back_populates="metric_slices")


class CalibrationReport(Base):
    """Human-review evidence.  There is intentionally no apply mutation API."""

    __tablename__ = "calibration_reports"
    __table_args__ = (
        UniqueConstraint("backtest_run_id", "target_parameter", name="uq_calibration_reports_run_parameter"),
        Index("ix_calibration_reports_owner_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED", index=True)
    target_parameter: Mapped[str] = mapped_column(String(128), index=True)
    current_value_json: Mapped[object | None] = mapped_column(JSON)
    challenger_value_json: Mapped[object | None] = mapped_column(JSON)
    recommendation: Mapped[str] = mapped_column(String(32), index=True)
    train_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    validation_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    test_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    robustness_json: Mapped[dict | None] = mapped_column(JSON)
    sample_counts_json: Mapped[dict | None] = mapped_column(JSON)
    risk_notes_json: Mapped[list | None] = mapped_column(JSON)
    proposal_json: Mapped[dict | None] = mapped_column(JSON)
    report_json: Mapped[dict | None] = mapped_column(JSON)
    calibration_version: Mapped[str] = mapped_column(String(64), default="calibration-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    run: Mapped[BacktestRun] = relationship(back_populates="calibration_reports")


def _research_immutable_update(_mapper, _connection, target) -> None:
    final_statuses = {"COMPLETED", "FAILED", "CANCELLED", "INSUFFICIENT_DATA", "INVALIDATED"}
    is_final = isinstance(target, BacktestMetricSlice) or getattr(target, "status", "") in final_statuses
    if isinstance(target, (BacktestRun, BacktestMetricSlice, CalibrationReport)) and is_final and getattr(
        target, "_allow_research_update", False
    ) is not True:
        # Completed research evidence is audit data.  The worker may update a
        # run before completion; later corrections must create a new run.
        raise RuntimeError("completed_research_record_is_immutable")


def _research_immutable_delete(_mapper, _connection, target) -> None:
    if isinstance(target, (BacktestRun, BacktestMetricSlice, CalibrationReport)):
        raise RuntimeError("research_record_is_immutable")


def _clear_research_update_flag(_mapper, _connection, target) -> None:
    if hasattr(target, "_allow_research_update"):
        setattr(target, "_allow_research_update", False)


event.listen(BacktestRun, "before_update", _research_immutable_update)
event.listen(BacktestMetricSlice, "before_update", _research_immutable_update)
event.listen(CalibrationReport, "before_update", _research_immutable_update)
event.listen(BacktestRun, "after_update", _clear_research_update_flag)
event.listen(BacktestMetricSlice, "after_update", _clear_research_update_flag)
event.listen(CalibrationReport, "after_update", _clear_research_update_flag)
event.listen(BacktestRun, "before_delete", _research_immutable_delete)
event.listen(BacktestMetricSlice, "before_delete", _research_immutable_delete)
event.listen(CalibrationReport, "before_delete", _research_immutable_delete)


__all__ = ["BacktestRun", "BacktestMetricSlice", "CalibrationReport"]
