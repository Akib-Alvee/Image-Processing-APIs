import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SupportedFormat = Literal["jpeg", "jpg", "png", "webp"]
WatermarkPosition = Literal[
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
]


# ==========================================
# 1. Transformation Request Schemas
# ==========================================


class ResizeOptions(BaseModel):
    """Target dimensions in pixels for resizing an image."""

    width: int = Field(..., gt=0, le=8000, description="Target width in pixels (1-8000)")
    height: int = Field(..., gt=0, le=8000, description="Target height in pixels (1-8000)")


class CropOptions(BaseModel):
    """Rectangular region to crop from the image starting at (x, y)."""

    width: int = Field(..., gt=0, le=8000, description="Crop width in pixels")
    height: int = Field(..., gt=0, le=8000, description="Crop height in pixels")
    x: int = Field(0, ge=0, description="Left offset in pixels")
    y: int = Field(0, ge=0, description="Top offset in pixels")


class WatermarkOptions(BaseModel):
    """Text watermark overlay configuration."""

    text: str = Field(..., min_length=1, max_length=100, description="Watermark text to draw")
    position: WatermarkPosition = Field(
        default="bottom-right",
        description="Corner or center placement of the watermark",
    )
    opacity: int = Field(
        default=160,
        ge=10,
        le=255,
        description="Alpha opacity from 10 (faint) to 255 (solid)",
    )


class CompressOptions(BaseModel):
    """Output compression settings."""

    quality: int = Field(
        default=85,
        ge=1,
        le=100,
        description="Compression quality (1 = lowest size, 100 = highest quality)",
    )


class FilterOptions(BaseModel):
    """Color filter toggles."""

    grayscale: bool = Field(default=False, description="Convert image to grayscale")
    sepia: bool = Field(default=False, description="Apply warm vintage sepia tone")


class TransformationOptions(BaseModel):
    """All supported transformations that can be combined in a single request."""

    resize: ResizeOptions | None = None
    crop: CropOptions | None = None
    rotate: float | None = Field(
        default=None,
        ge=-360.0,
        le=360.0,
        description="Clockwise rotation angle in degrees",
    )
    flip: bool = Field(default=False, description="Flip vertically (top-to-bottom)")
    mirror: bool = Field(default=False, description="Mirror horizontally (left-to-right)")
    watermark: WatermarkOptions | None = None
    compress: CompressOptions | None = None
    format: SupportedFormat | None = Field(
        default=None,
        description="Output image format ('jpeg', 'jpg', 'png', 'webp')",
    )
    filters: FilterOptions | None = None

    @model_validator(mode="after")
    def ensure_at_least_one_transformation(self) -> "TransformationOptions":
        """Reject empty transformation payloads where no operation is requested."""
        has_filter = bool(
            self.filters and (self.filters.grayscale or self.filters.sepia)
        )
        has_any = any(
            [
                self.resize is not None,
                self.crop is not None,
                self.rotate is not None and self.rotate != 0,
                self.flip,
                self.mirror,
                self.watermark is not None,
                self.compress is not None,
                self.format is not None,
                has_filter,
            ]
        )
        if not has_any:
            raise ValueError(
                "At least one transformation (resize, crop, rotate, flip, mirror, "
                "watermark, compress, format, or filters) must be specified."
            )
        return self


class TransformRequest(BaseModel):
    """Request body for POST /images/{id}/transform."""

    transformations: TransformationOptions


# ==========================================
# 2. Image & Transformation Response Schemas
# ==========================================


class TransformationResponse(BaseModel):
    """Metadata and URL for a transformed image variant."""

    id: uuid.UUID
    parent_image_id: uuid.UUID
    status: str
    url: str | None = None
    format: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    parameters: dict[str, Any]
    error_message: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AsyncJobResponse(BaseModel):
    """Response returned when POST /images/{id}/transform?async=true is queued via RabbitMQ/Celery."""

    job_id: uuid.UUID
    celery_task_id: str | None = None
    parent_image_id: uuid.UUID
    status: str
    status_url: str
    message: str


class ImageResponse(BaseModel):
    """Metadata and URL for an uploaded original image."""

    id: uuid.UUID
    user_id: uuid.UUID
    original_filename: str
    url: str
    format: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImageDetailResponse(ImageResponse):
    """Detailed image response including its transformed variants."""

    transformations: list[TransformationResponse] = Field(default_factory=list)


class PaginatedImagesResponse(BaseModel):
    """Paginated response for GET /images?page=1&limit=10."""

    items: list[ImageResponse]
    total: int
    page: int
    limit: int
    pages: int
