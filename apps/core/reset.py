"""Wipe all business data (imports, catalog, matching, reviews). User accounts are kept."""

from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

from apps.catalog.models import FieldValue, PartNumber, Product, SupplierItem, SupplierOffer
from apps.ingestion.models import ImportBatch, SourceFile, SourceRecord, Supplier
from apps.matching.models import Issue, MatchCandidate, ReviewDecision

MODELS = (ReviewDecision, Issue, MatchCandidate, SupplierOffer, PartNumber, FieldValue,
          SupplierItem, Product, SourceRecord, ImportBatch, SourceFile, Supplier)


def reset_business_data() -> int:
    """Returns the number of archived original files removed."""
    files = [sf.file for sf in SourceFile.objects.all()]
    tables = ", ".join(connection.ops.quote_name(m._meta.db_table) for m in MODELS)
    with transaction.atomic(), connection.cursor() as cursor:
        # Restart ids so product codes begin at P-000001 again.
        cursor.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    # Only a deliberate reset removes archived originals; normal operation never does.
    for f in files:
        f.delete(save=False)
    # Remove the now-empty per-file folders (originals/<sha256>/).
    root = Path(settings.MEDIA_ROOT) / "originals"
    if root.is_dir():
        for folder in root.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    return len(files)
