from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from config import get_jwt_auth_manager, get_s3_storage_client
from database import get_db, UserModel, UserProfileModel, UserGroupEnum
from database.models.accounts import GenderEnum
from exceptions import TokenExpiredError, InvalidTokenError, S3FileUploadError, S3ConnectionError
from schemas import ProfileCreateRequestSchema, ProfileResponseSchema
from security.http import get_token
from security.interfaces import JWTAuthManagerInterface
from storages import S3StorageInterface

router = APIRouter()


@router.post(
    "/users/{user_id}/profile/",
    response_model=ProfileResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_profile(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
) -> ProfileResponseSchema:
    token = get_token(request)
    try:
        payload = jwt_manager.decode_access_token(token)
    except TokenExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired.") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.") from exc

    request_user_id = payload.get("user_id")
    if request_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    try:
        form = await request.form()
        profile_data = ProfileCreateRequestSchema(
            first_name=form.get("first_name"),
            last_name=form.get("last_name"),
            gender=form.get("gender"),
            date_of_birth=form.get("date_of_birth"),
            info=form.get("info"),
            avatar=form.get("avatar"),
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    request_user_stmt = (
        select(UserModel)
        .options(joinedload(UserModel.group))
        .where(UserModel.id == request_user_id)
    )
    result = await db.execute(request_user_stmt)
    request_user = result.scalars().first()
    if request_user is None or not request_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or not active.")

    target_user_stmt = (
        select(UserModel)
        .options(joinedload(UserModel.group), joinedload(UserModel.profile))
        .where(UserModel.id == user_id)
    )
    result = await db.execute(target_user_stmt)
    target_user = result.scalars().first()
    if target_user is None or not target_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or not active.")

    if request_user.id != target_user.id and not request_user.has_group(UserGroupEnum.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile.",
        )

    if target_user.profile is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already has a profile.")

    avatar = profile_data.avatar
    avatar_bytes = await avatar.read()
    await avatar.seek(0)
    avatar_extension = profile_data.avatar_extension()
    avatar_key = f"avatars/{target_user.id}_avatar{avatar_extension}"

    try:
        await s3_client.upload_file(avatar_key, BytesIO(avatar_bytes))
    except (S3FileUploadError, S3ConnectionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later.",
        ) from exc

    profile = UserProfileModel(
        user_id=target_user.id,
        first_name=profile_data.first_name,
        last_name=profile_data.last_name,
        gender=GenderEnum(profile_data.gender),
        date_of_birth=profile_data.date_of_birth,
        info=profile_data.info,
        avatar=avatar_key,
    )
    db.add(profile)

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create profile.",
        ) from exc

    await db.refresh(profile)
    avatar_url = await s3_client.get_file_url(avatar_key)

    return ProfileResponseSchema(
        id=profile.id,
        user_id=profile.user_id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        gender=profile.gender.value if profile.gender else None,
        date_of_birth=profile.date_of_birth,
        info=profile.info,
        avatar=avatar_url,
    )
