"""Model profile connection tests and warmup."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.model_client import ModelCallError, health_check
from ..v2_dependencies import get_current_user
from ..v2_models import ModelProfile, User
from ..v2_schemas import ModelHealthResponse

router = APIRouter(prefix="/api/v2/model-settings", tags=["v2-model-settings"])


@router.post("/profiles/{profile_id}/test", response_model=ModelHealthResponse)
def test_model_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelHealthResponse:
    profile = (
        db.query(ModelProfile)
        .filter(ModelProfile.id == profile_id, ModelProfile.user_id == current_user.id)
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Model profile not found.")
    try:
        result = health_check(profile)
        profile.last_health_status = "ok"
        profile.last_health_at = datetime.now(UTC)
        db.commit()
        return ModelHealthResponse(
            status="ok",
            message="模型连接成功",
            latency_ms=result.latency_ms,
            raw_excerpt=result.text[:240],
        )
    except ModelCallError as exc:
        profile.last_health_status = "failed"
        profile.last_health_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
