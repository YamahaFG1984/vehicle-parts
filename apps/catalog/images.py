"""SKU images: validate, store, thumbnail, primary image."""

from __future__ import annotations

import io
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ItemImage, SupplierItem

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
THUMB_SIZE = (480, 480)


def validate_upload(upload) -> None:
    """Reject anything that is not a real JPG / PNG / WebP of reasonable size."""
    name = getattr(upload, "name", "") or ""
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValidationError(f"{name}：只接受 JPG、PNG、WebP 图片")
    if upload.size > MAX_BYTES:
        raise ValidationError(f"{name}：图片超过 {MAX_BYTES // 1024 // 1024} MB")
    try:
        upload.seek(0)
        with Image.open(upload) as img:
            fmt = img.format
            img.verify()  # detects truncated / fake files
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationError(f"{name}：不是有效的图片文件") from exc
    finally:
        upload.seek(0)
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError(f"{name}：图片格式 {fmt} 不受支持")


def _thumbnail(upload) -> ContentFile:
    upload.seek(0)
    with Image.open(upload) as img:
        img = ImageOps.exif_transpose(img)  # phone photos: respect the camera orientation
        img.thumbnail(THUMB_SIZE)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, (245, 246, 242))
            background.paste(img, mask=img.convert("RGBA").getchannel("A"))
            img = background
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
    upload.seek(0)
    return ContentFile(buf.getvalue(), name="thumb.jpg")


def add_images(item: SupplierItem, uploads, *, source: str = ItemImage.Source.SUPPLIER,
               caption: str = "", user=None) -> list[ItemImage]:
    """Validate every file first, then store them all; the first image of a SKU becomes primary."""
    uploads = list(uploads)
    for upload in uploads:
        validate_upload(upload)
    created = []
    with transaction.atomic():
        has_primary = item.images.filter(is_primary=True).exists()
        for upload in uploads:
            image = ItemImage(item=item, source=source, caption=caption, uploaded_by=user,
                              original_name=Path(upload.name).name[:255],
                              is_primary=not has_primary)
            image.image.save(upload.name, upload, save=False)
            image.thumbnail.save("thumb.jpg", _thumbnail(upload), save=False)
            image.save()
            has_primary = True
            created.append(image)
    return created


def set_primary(image: ItemImage) -> None:
    with transaction.atomic():
        image.item.images.filter(is_primary=True).exclude(pk=image.pk).update(is_primary=False)
        if not image.is_primary:
            image.is_primary = True
            image.save(update_fields=["is_primary", "modified"])


def delete_image(image: ItemImage) -> None:
    """Remove an image and its files; if it was primary, the next oldest image takes over."""
    item = image.item
    was_primary = image.is_primary
    with transaction.atomic():
        image.delete()
        if was_primary:
            nxt = item.images.order_by("created").first()
            if nxt:
                set_primary(nxt)
    for f in (image.image, image.thumbnail):
        if f:
            f.storage.delete(f.name)
