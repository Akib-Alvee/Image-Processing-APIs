from dataclasses import dataclass
import hashlib
import io
import json
import uuid
from typing import Any

from fastapi import HTTPException, status
from PIL import Image as PILImage, ImageDraw, ImageFont, ImageOps

from app.schemas.image import TransformationOptions


@dataclass
class ProcessedImageResult:
    """Output produced by the Pillow transformation pipeline."""

    data: bytes
    format: str
    content_type: str
    width: int
    height: int
    size_bytes: int


FORMAT_TO_PILLOW_AND_MIME: dict[str, tuple[str, str, str]] = {
    "jpeg": ("JPEG", "jpeg", "image/jpeg"),
    "jpg": ("JPEG", "jpeg", "image/jpeg"),
    "png": ("PNG", "png", "image/png"),
    "webp": ("WEBP", "webp", "image/webp"),
}


def compute_transformation_hash(
    image_id: uuid.UUID,
    options: TransformationOptions,
) -> str:
    """
    Compute a deterministic SHA-256 hash of (image_id + normalized transformation options).
    If a user requests the exact same transformation parameters twice, the hash matches
    so we can return the cached result immediately without re-running Pillow.
    """
    canonical_dict = options.model_dump(exclude_none=True, mode="json")
    canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    raw_string = f"{image_id}:{canonical_json}"
    return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()


class ImageProcessor:
    """
    Executes image transformations in a deterministic pipeline using Pillow (PIL):
    1. Crop -> 2. Resize -> 3. Rotate -> 4. Flip / Mirror -> 5. Filters (Grayscale / Sepia)
    -> 6. Watermark -> 7. Format Conversion & Compression.
    """

    def process_image(
        self,
        source_bytes: bytes,
        source_format: str,
        options: TransformationOptions,
    ) -> ProcessedImageResult:
        with PILImage.open(io.BytesIO(source_bytes)) as img:
            # Normalize orientation from EXIF tag if present
            img = ImageOps.exif_transpose(img) or img
            img.load()

            # 1. Crop
            if options.crop is not None:
                img = self._apply_crop(img, options)

            # 2. Resize
            if options.resize is not None:
                img = img.resize(
                    (options.resize.width, options.resize.height),
                    resample=PILImage.Resampling.LANCZOS,
                )

            # 3. Rotate (clockwise degrees -> Pillow rotates counter-clockwise, so negate angle)
            if options.rotate is not None and options.rotate != 0:
                img = img.rotate(-options.rotate, expand=True, resample=PILImage.Resampling.BICUBIC)

            # 4. Flip (vertical) & Mirror (horizontal)
            if options.flip:
                img = img.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)
            if options.mirror:
                img = img.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)

            # 5. Filters (Grayscale & Sepia)
            if options.filters is not None:
                if options.filters.grayscale:
                    img = self._apply_grayscale(img)
                if options.filters.sepia:
                    img = self._apply_sepia(img)

            # 6. Watermark (Text overlay with alpha blending)
            if options.watermark is not None:
                img = self._apply_watermark(img, options)

            # 7. Output Format & Compression
            target_fmt_key = (options.format or source_format or "jpeg").lower()
            if target_fmt_key not in FORMAT_TO_PILLOW_AND_MIME:
                target_fmt_key = "jpeg"

            pillow_fmt, normalized_fmt, content_type = FORMAT_TO_PILLOW_AND_MIME[target_fmt_key]
            quality = options.compress.quality if options.compress is not None else 85

            # JPEG does not support alpha transparency (RGBA/LA/P); composite onto white or convert to RGB
            if pillow_fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")

            output_buffer = io.BytesIO()
            save_kwargs: dict[str, Any] = {"format": pillow_fmt}
            if pillow_fmt in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
            elif pillow_fmt == "PNG":
                save_kwargs["optimize"] = True

            img.save(output_buffer, **save_kwargs)
            output_bytes = output_buffer.getvalue()
            final_width, final_height = img.size

        return ProcessedImageResult(
            data=output_bytes,
            format=normalized_fmt,
            content_type=content_type,
            width=final_width,
            height=final_height,
            size_bytes=len(output_bytes),
        )

    @staticmethod
    def _apply_crop(img: PILImage.Image, options: TransformationOptions) -> PILImage.Image:
        assert options.crop is not None
        src_w, src_h = img.size
        left = options.crop.x
        top = options.crop.y
        right = left + options.crop.width
        bottom = top + options.crop.height

        if left >= src_w or top >= src_h or right > src_w or bottom > src_h:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Crop region (x={left}, y={top}, width={options.crop.width}, "
                    f"height={options.crop.height}) exceeds source image bounds ({src_w}x{src_h})."
                ),
            )
        return img.crop((left, top, right, bottom))

    @staticmethod
    def _apply_grayscale(img: PILImage.Image) -> PILImage.Image:
        """Convert image to grayscale while preserving alpha channel if present."""
        if img.mode == "RGBA":
            r, g, b, a = img.split()
            gray = ImageOps.grayscale(PILImage.merge("RGB", (r, g, b)))
            return PILImage.merge("RGBA", (gray, gray, gray, a))
        return ImageOps.grayscale(img).convert("RGB")

    @staticmethod
    def _apply_sepia(img: PILImage.Image) -> PILImage.Image:
        """Apply standard sepia color matrix transformation using Pillow's C-level convert()."""
        alpha = img.getchannel("A") if img.mode == "RGBA" else None
        rgb_img = img.convert("RGB")
        # 3x4 color matrix for classic warm sepia toning
        sepia_matrix = (
            0.393, 0.769, 0.189, 0,
            0.349, 0.686, 0.168, 0,
            0.272, 0.534, 0.131, 0,
        )
        sepia_img = rgb_img.convert("RGB", sepia_matrix)
        if alpha is not None:
            sepia_img.putalpha(alpha)
        return sepia_img

    @staticmethod
    def _apply_watermark(img: PILImage.Image, options: TransformationOptions) -> PILImage.Image:
        """Draw a semi-transparent text watermark on an RGBA overlay layer."""
        assert options.watermark is not None
        base = img.convert("RGBA")
        overlay = PILImage.new("RGBA", base.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        # Scale font size proportionally to image width (~4% of image width, min 14px)
        font_size = max(14, int(min(base.size) * 0.05))
        font = ImageFont.load_default(size=font_size)

        text = options.watermark.text
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        margin = max(10, int(min(base.size) * 0.03))

        img_w, img_h = base.size
        pos_map: dict[str, tuple[int, int]] = {
            "top-left": (margin, margin),
            "top-right": (max(0, img_w - text_w - margin), margin),
            "bottom-left": (margin, max(0, img_h - text_h - margin)),
            "bottom-right": (max(0, img_w - text_w - margin), max(0, img_h - text_h - margin)),
            "center": (max(0, (img_w - text_w) // 2), max(0, (img_h - text_h) // 2)),
        }
        x, y = pos_map.get(options.watermark.position, pos_map["bottom-right"])
        opacity = options.watermark.opacity

        # Subtle dark drop shadow for readability on bright backgrounds
        draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, min(255, opacity)))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, opacity))

        return PILImage.alpha_composite(base, overlay)


image_processor = ImageProcessor()
