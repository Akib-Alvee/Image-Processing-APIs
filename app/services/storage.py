import json
import uuid
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core.config import settings


class S3StorageService:
    """
    S3-compatible object storage adapter powered by boto3.
    Works seamlessly with MinIO in Docker locally, as well as Cloudflare R2 or AWS S3 in production.
    """

    def __init__(self) -> None:
        self.bucket_name = settings.S3_BUCKET_NAME
        self.public_base_url = settings.S3_PUBLIC_URL.rstrip("/")
        self._bucket_verified = False
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket_exists(self) -> None:
        """
        Automatically create the S3/MinIO bucket if it does not exist yet
        and apply a public read policy so browser image URLs work out of the box.
        """
        if self._bucket_verified:
            return
        try:
            self._client.head_bucket(Bucket=self.bucket_name)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket_name)
            public_read_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]},
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{self.bucket_name}/*"],
                    }
                ],
            }
            try:
                self._client.put_bucket_policy(
                    Bucket=self.bucket_name,
                    Policy=json.dumps(public_read_policy),
                )
            except ClientError:
                pass
        self._bucket_verified = True

    def generate_storage_key(
        self,
        user_id: uuid.UUID,
        extension: str,
        prefix: str = "originals",
    ) -> str:
        """
        Build a structured S3 object key, e.g.:
        'originals/<user_id>/<uuid>.jpg' or 'transformed/<user_id>/<uuid>.webp'
        """
        clean_ext = extension.lower().lstrip(".")
        if clean_ext == "jpeg":
            clean_ext = "jpg"
        return f"{prefix}/{user_id}/{uuid.uuid4().hex}.{clean_ext}"

    def get_public_url(self, storage_key: str) -> str:
        """Construct the public browser-accessible URL for a stored object."""
        return f"{self.public_base_url}/{self.bucket_name}/{storage_key}"

    def upload_bytes(
        self,
        data: bytes,
        storage_key: str,
        content_type: str,
    ) -> str:
        """Upload raw image bytes to the S3/MinIO bucket and return its public URL."""
        try:
            self.ensure_bucket_exists()
            self._client.put_object(
                Bucket=self.bucket_name,
                Key=storage_key,
                Body=data,
                ContentType=content_type,
            )
            return self.get_public_url(storage_key)
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to upload image to object storage: {exc}",
            ) from exc

    def download_bytes(self, storage_key: str) -> bytes:
        """Download raw image bytes from the S3/MinIO bucket by its storage key."""
        try:
            response: dict[str, Any] = self._client.get_object(
                Bucket=self.bucket_name,
                Key=storage_key,
            )
            return response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not retrieve image from storage ({storage_key}): {exc}",
            ) from exc

    def delete_object(self, storage_key: str) -> None:
        """Delete an object from the S3/MinIO bucket."""
        try:
            self._client.delete_object(Bucket=self.bucket_name, Key=storage_key)
        except (BotoCoreError, ClientError):
            pass


storage_service = S3StorageService()
