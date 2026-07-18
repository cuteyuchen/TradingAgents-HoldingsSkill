"""Per-user V2 model provider and model profile settings."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import encrypt_secret
from ..v2_dependencies import get_current_user
from ..v2_models import ModelProfile, ModelProvider, User
from ..v2_schemas import (
    ModelProfileCreate,
    ModelProfileResponse,
    ModelProfileUpdate,
    ModelProviderCreate,
    ModelProviderResponse,
    ModelProviderUpdate,
)

router = APIRouter(prefix="/api/v2/model-settings", tags=["v2-model-settings"])


def _get_provider(db: Session, user_id: int, provider_id: int) -> ModelProvider:
    provider = (
        db.query(ModelProvider)
        .filter(ModelProvider.id == provider_id, ModelProvider.user_id == user_id)
        .first()
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Model provider not found.")
    return provider


def _get_profile(db: Session, user_id: int, profile_id: int) -> ModelProfile:
    profile = (
        db.query(ModelProfile)
        .filter(ModelProfile.id == profile_id, ModelProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Model profile not found.")
    return profile


def _provider_response(provider: ModelProvider) -> ModelProviderResponse:
    has_api_key = bool(provider.encrypted_api_key)
    return ModelProviderResponse(
        id=provider.id,
        provider=provider.provider,
        display_name=provider.display_name,
        base_url=provider.base_url,
        enabled=provider.enabled,
        has_api_key=has_api_key,
        api_key_masked="••••••••" if has_api_key else None,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _profile_response(profile: ModelProfile) -> ModelProfileResponse:
    return ModelProfileResponse(
        id=profile.id,
        provider_id=profile.provider_id,
        purpose=profile.purpose,
        model_name=profile.model_name,
        parameters=profile.parameters_json or {},
        is_default=profile.is_default,
        last_health_status=profile.last_health_status,
        last_health_at=profile.last_health_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _unset_other_defaults(db: Session, user_id: int, purpose: str, keep_id: int | None = None) -> None:
    query = db.query(ModelProfile).filter(
        ModelProfile.user_id == user_id,
        ModelProfile.purpose == purpose,
        ModelProfile.is_default.is_(True),
    )
    if keep_id is not None:
        query = query.filter(ModelProfile.id != keep_id)
    for row in query.all():
        row.is_default = False


@router.get("/providers", response_model=list[ModelProviderResponse])
def list_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelProviderResponse]:
    rows = (
        db.query(ModelProvider)
        .filter(ModelProvider.user_id == current_user.id)
        .order_by(ModelProvider.id.asc())
        .all()
    )
    return [_provider_response(row) for row in rows]


@router.post("/providers", response_model=ModelProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ModelProviderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelProviderResponse:
    duplicate = (
        db.query(ModelProvider)
        .filter(
            ModelProvider.user_id == current_user.id,
            ModelProvider.display_name == payload.display_name,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Provider display name already exists.")

    provider = ModelProvider(
        user_id=current_user.id,
        provider=payload.provider,
        display_name=payload.display_name,
        base_url=payload.base_url,
        encrypted_api_key=encrypt_secret(payload.api_key) if payload.api_key else None,
        enabled=payload.enabled,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return _provider_response(provider)


@router.patch("/providers/{provider_id}", response_model=ModelProviderResponse)
def update_provider(
    provider_id: int,
    payload: ModelProviderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelProviderResponse:
    provider = _get_provider(db, current_user.id, provider_id)
    fields = payload.model_fields_set
    if "provider" in fields and payload.provider is not None:
        provider.provider = payload.provider
    if "display_name" in fields and payload.display_name is not None:
        duplicate = (
            db.query(ModelProvider)
            .filter(
                ModelProvider.user_id == current_user.id,
                ModelProvider.display_name == payload.display_name,
                ModelProvider.id != provider.id,
            )
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Provider display name already exists.")
        provider.display_name = payload.display_name
    if "base_url" in fields:
        provider.base_url = payload.base_url
    if payload.clear_api_key:
        provider.encrypted_api_key = None
    elif "api_key" in fields and payload.api_key:
        provider.encrypted_api_key = encrypt_secret(payload.api_key)
    if "enabled" in fields and payload.enabled is not None:
        provider.enabled = payload.enabled

    db.commit()
    db.refresh(provider)
    return _provider_response(provider)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    provider = _get_provider(db, current_user.id, provider_id)
    db.delete(provider)
    db.commit()


@router.get("/profiles", response_model=list[ModelProfileResponse])
def list_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelProfileResponse]:
    rows = (
        db.query(ModelProfile)
        .filter(ModelProfile.user_id == current_user.id)
        .order_by(ModelProfile.purpose.asc(), ModelProfile.id.asc())
        .all()
    )
    return [_profile_response(row) for row in rows]


@router.post("/profiles", response_model=ModelProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ModelProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelProfileResponse:
    _get_provider(db, current_user.id, payload.provider_id)
    if payload.is_default:
        _unset_other_defaults(db, current_user.id, payload.purpose)

    profile = ModelProfile(
        user_id=current_user.id,
        provider_id=payload.provider_id,
        purpose=payload.purpose,
        model_name=payload.model_name,
        parameters_json=payload.parameters,
        is_default=payload.is_default,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_response(profile)


@router.patch("/profiles/{profile_id}", response_model=ModelProfileResponse)
def update_profile(
    profile_id: int,
    payload: ModelProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelProfileResponse:
    profile = _get_profile(db, current_user.id, profile_id)
    fields = payload.model_fields_set
    if "provider_id" in fields and payload.provider_id is not None:
        _get_provider(db, current_user.id, payload.provider_id)
        profile.provider_id = payload.provider_id
    if "purpose" in fields and payload.purpose is not None:
        profile.purpose = payload.purpose
    if "model_name" in fields and payload.model_name is not None:
        profile.model_name = payload.model_name
    if "parameters" in fields and payload.parameters is not None:
        profile.parameters_json = payload.parameters
    if "is_default" in fields and payload.is_default is not None:
        profile.is_default = payload.is_default
        if payload.is_default:
            _unset_other_defaults(db, current_user.id, profile.purpose, keep_id=profile.id)

    db.commit()
    db.refresh(profile)
    return _profile_response(profile)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    profile = _get_profile(db, current_user.id, profile_id)
    db.delete(profile)
    db.commit()
