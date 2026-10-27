import uuid

from fastapi.testclient import TestClient
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.schemas.image import TransformationOptions


def test_health_check_endpoint() -> None:
    """Verify GET /health returns 200 OK."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_password_hashing_and_jwt_access_and_refresh_tokens() -> None:
    """Verify Bcrypt password hashing and separate Access vs Refresh token cryptography."""
    raw_password = "MySecurePassword123!"
    hashed = hash_password(raw_password)
    assert hashed != raw_password
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False

    user_id = uuid.uuid4()
    access_token = create_access_token(user_id=user_id, username="alvee")
    refresh_token, jti, expires_at = create_refresh_token(user_id=user_id)

    decoded_access = decode_access_token(access_token)
    assert decoded_access["sub"] == str(user_id)
    assert decoded_access["username"] == "alvee"
    assert decoded_access["type"] == "access"

    decoded_refresh = decode_refresh_token(refresh_token)
    assert decoded_refresh["sub"] == str(user_id)
    assert decoded_refresh["jti"] == jti
    assert decoded_refresh["type"] == "refresh"
    assert expires_at is not None


def test_empty_transformation_payload_rejected_by_pydantic() -> None:
    """Verify TransformationOptions rejects an empty payload where no operation is requested."""
    with pytest.raises(ValueError, match="At least one transformation"):
        TransformationOptions()


def test_protected_endpoints_require_bearer_token() -> None:
    """Verify /images endpoints reject unauthenticated requests with 401 Unauthorized."""
    client = TestClient(app)
    res_list = client.get("/images")
    assert res_list.status_code == 401

    res_upload = client.post(
        "/images",
        files={"file": ("test.png", b"fake", "image/png")},
    )
    assert res_upload.status_code == 401
