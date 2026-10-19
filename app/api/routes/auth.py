from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DBSession
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    AuthResponse,
    RefreshTokenRequest,
    TokenPairResponse,
    UserCredentials,
    UserResponse,
)

router = APIRouter(tags=["Authentication"])


async def _issue_token_pair(db: DBSession, user: User) -> tuple[str, str]:
    """Generate an Access + Refresh token pair and persist the RefreshToken JTI in PostgreSQL."""
    access_token = create_access_token(user_id=user.id, username=user.username)
    refresh_token, token_jti, expires_at = create_refresh_token(user_id=user.id)

    db_refresh = RefreshToken(
        user_id=user.id,
        token_jti=token_jti,
        is_revoked=False,
        expires_at=expires_at,
    )
    db.add(db_refresh)
    await db.commit()
    return access_token, refresh_token


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register_user(payload: UserCredentials, db: DBSession) -> AuthResponse:
    """
    Create a new user account with a Bcrypt-hashed password and immediately issue
    a short-lived Access Token and a long-lived Refresh Token.
    """
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    try:
        db.add(user)
        await db.flush()  # Sends INSERT to DB transaction & populates user.id before commit
        access_token, refresh_token = await _issue_token_pair(db, user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' is already registered",
        ) from exc

    await db.refresh(user)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Log in an existing user",
)
async def login_user(payload: UserCredentials, db: DBSession) -> AuthResponse:
    """Authenticate username and password and issue a fresh Access + Refresh token pair."""
    result = await db.execute(
        select(User).where(User.username == payload.username)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access_token, refresh_token = await _issue_token_pair(db, user)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate Refresh Token and get a new Access Token",
)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    db: DBSession,
) -> TokenPairResponse:
    """
    Exchange a valid Refresh Token for a brand new Access Token + Refresh Token pair.
    Implements Refresh Token Rotation: the used Refresh Token is immediately marked as revoked.
    """
    decoded = decode_refresh_token(payload.refresh_token)
    token_jti = decoded["jti"]

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_jti == token_jti)
    )
    stored_token = result.scalar_one_or_none()

    if stored_token is None or stored_token.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked or is invalid",
        )

    if stored_token.expires_at < datetime.now(timezone.utc):
        stored_token.is_revoked = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
        )

    user_result = await db.execute(
        select(User).where(User.id == stored_token.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Rotate: revoke the old refresh token and issue a new pair
    stored_token.is_revoked = True
    access_token, new_refresh_token = await _issue_token_pair(db, user)

    return TokenPairResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Revoke a Refresh Token to log out",
)
async def logout_user(
    payload: RefreshTokenRequest,
    db: DBSession,
) -> dict[str, str]:
    """Revoke the provided Refresh Token so it can never be used again."""
    decoded = decode_refresh_token(payload.refresh_token)
    token_jti = decoded["jti"]

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_jti == token_jti)
    )
    stored_token = result.scalar_one_or_none()
    if stored_token is not None and not stored_token.is_revoked:
        stored_token.is_revoked = True
        await db.commit()

    return {"message": "Successfully logged out and revoked refresh token"}
