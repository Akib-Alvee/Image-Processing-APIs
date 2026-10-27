import io
import uuid

from fastapi import HTTPException
from PIL import Image as PILImage
import pytest

from app.schemas.image import (
    CompressOptions,
    CropOptions,
    FilterOptions,
    ResizeOptions,
    TransformationOptions,
    WatermarkOptions,
)
from app.services.processor import compute_transformation_hash, image_processor


def _create_sample_image_bytes(
    width: int = 400,
    height: int = 300,
    fmt: str = "PNG",
    color: tuple[int, int, int] = (40, 120, 200),
) -> bytes:
    """Helper to generate a valid in-memory test image using Pillow."""
    img = PILImage.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_resize_and_format_conversion() -> None:
    """Verify resize changes dimensions and format conversion outputs valid WEBP."""
    src = _create_sample_image_bytes(400, 300, "PNG")
    opts = TransformationOptions(
        resize=ResizeOptions(width=200, height=150),
        format="webp",
        compress=CompressOptions(quality=80),
    )

    result = image_processor.process_image(src, "png", opts)
    assert result.width == 200
    assert result.height == 150
    assert result.format == "webp"
    assert result.content_type == "image/webp"
    assert result.size_bytes > 0


def test_crop_valid_and_out_of_bounds() -> None:
    """Verify valid crop succeeds and out-of-bounds crop raises 400 Bad Request."""
    src = _create_sample_image_bytes(400, 300, "JPEG")
    valid_opts = TransformationOptions(
        crop=CropOptions(x=50, y=50, width=100, height=120),
    )
    result = image_processor.process_image(src, "jpeg", valid_opts)
    assert result.width == 100
    assert result.height == 120

    invalid_opts = TransformationOptions(
        crop=CropOptions(x=350, y=50, width=100, height=100),
    )
    with pytest.raises(HTTPException) as exc_info:
        image_processor.process_image(src, "jpeg", invalid_opts)
    assert exc_info.value.status_code == 400


def test_rotate_flip_mirror_filters_and_watermark() -> None:
    """Verify rotating 90 degrees swaps width/height and filters + watermark execute cleanly."""
    src = _create_sample_image_bytes(400, 200, "PNG")
    opts = TransformationOptions(
        rotate=90,
        flip=True,
        mirror=True,
        filters=FilterOptions(grayscale=False, sepia=True),
        watermark=WatermarkOptions(
            text="© Alvee API",
            position="bottom-right",
            opacity=180,
        ),
        format="jpeg",
    )

    result = image_processor.process_image(src, "png", opts)
    # 400x200 rotated by 90 degrees with expand=True becomes 200x400
    assert result.width == 200
    assert result.height == 400
    assert result.format == "jpeg"


def test_deterministic_transformation_hash() -> None:
    """Verify identical transformation options always produce the exact same SHA-256 hash."""
    image_id = uuid.uuid4()
    opts_a = TransformationOptions(
        resize=ResizeOptions(width=500, height=500),
        filters=FilterOptions(grayscale=True, sepia=False),
    )
    opts_b = TransformationOptions(
        filters=FilterOptions(sepia=False, grayscale=True),
        resize=ResizeOptions(height=500, width=500),
    )
    assert compute_transformation_hash(image_id, opts_a) == compute_transformation_hash(
        image_id, opts_b
    )
