"""Model profile connection tests and warmup."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.model_client import ModelCallError, ModelTimeoutError, health_check
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
        # 把实际链路信息带回前端：是否流式、重试了几次，便于确认超时配置是否生效。
        details = ["流式" if result.streamed else "非流式"]
        if result.retries:
            details.append(f"重试 {result.retries} 次")
        return ModelHealthResponse(
            status="ok",
            message=f"模型连接成功（{('，').join(details)}）",
            latency_ms=result.latency_ms,
            raw_excerpt=result.text[:240],
        )
    except ModelTimeoutError as exc:
        # 超时单独用 504，前端可以据此提示用户去调超时而不是怀疑 API Key。
        profile.last_health_status = "timeout"
        profile.last_health_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ModelCallError as exc:
        profile.last_health_status = "failed"
        profile.last_health_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
