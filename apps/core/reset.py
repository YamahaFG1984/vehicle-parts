"""Wipe all business data (imports, catalog, matching, reviews). User accounts are kept."""

from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

from apps.catalog.models import (
    FieldValue,
    ItemImage,
    PartNumber,
    Product,
    StockImport,
    StockLevel,
    SupplierItem,
    SupplierOffer,
)
from apps.ingestion.models import ImportBatch, SourceFile, SourceRecord, Supplier
from apps.matching.models import Issue, MatchCandidate, ReviewDecision

MODELS = (ReviewDecision, Issue, MatchCandidate, ItemImage, StockLevel, StockImport, SupplierOffer,
          PartNumber, FieldValue, SupplierItem, Product, SourceRecord, ImportBatch, SourceFile,
          Supplier)


def reset_business_data() -> int:
    """Returns the number of archived original files removed."""
    files = [sf.file for sf in SourceFile.objects.all()]
    files += [f for img in ItemImage.objects.all() for f in (img.image, img.thumbnail) if f]
    tables = ", ".join(connection.ops.quote_name(m._meta.db_table) for m in MODELS)
    with transaction.atomic(), connection.cursor() as cursor:
        # Restart ids so product codes begin at P-000001 again.
        cursor.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    # Only a deliberate reset removes archived originals; normal operation never does.
    for f in files:
        f.delete(save=False)
    # Remove the now-empty per-file folders (originals/<sha256>/, item_images/<id>/thumbs/).
    for root in (Path(settings.MEDIA_ROOT) / "originals", Path(settings.MEDIA_ROOT) / "item_images"):
        if root.is_dir():
            for folder in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
    return len(files)


def confirm(prompt: str, *, noinput: bool) -> bool:
    """Ask on a terminal; refuse (instead of hanging) when there is no one to answer."""
    import sys

    from django.core.management.base import CommandError

    if noinput:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        raise CommandError("当前不是交互式终端，无法确认清空数据；请加 --noinput 明确执行。")
    return input(prompt).strip().lower() == "y"
