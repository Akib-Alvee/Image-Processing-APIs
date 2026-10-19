import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCredentials(BaseModel):
    """Request body for POST /register and POST /login."""

    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.-]+$",
        description="Alphanumeric username (3-64 characters)",
        examples=["user1"],
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="User password (minimum 6 characters)",
        examples=["password123"],
    )


class RefreshTokenRequest(BaseModel):
    """Request body for POST /refresh and POST /logout."""

    refresh_token: str = Field(
        ...,
        description="Long-lived JWT refresh token",
    )


class UserResponse(BaseModel):
    """Public user profile returned in API responses (never exposes password_hash)."""

    id: uuid.UUID
    username: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    """Response returned on POST /register and POST /login containing user object and tokens."""

    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPairResponse(BaseModel):
    """Response returned on POST /refresh containing rotated tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
