"""Write side of the catalog: turn one observed row into item / field provenance / numbers / offer."""

from __future__ import annotations

import hashlib
import json

from .models import FieldValue, PartNumber, Product, SupplierItem, SupplierOffer
from .normalizers import NormalizedRecord, normalize_number

# Semantic fields whose raw value is tracked with provenance.
TRACKED_FIELDS = [
    "supplier_part_no", "source_ref", "brand", "name", "category", "position", "fitment",
    "package_dims", "oe_numbers", "price", "currency", "moq", "quote_date",
]
OFFER_FIELDS = ("price", "currency", "moq", "quote_date")


def attr_hash(key_attrs: dict) -> str:
    return hashlib.sha256(json.dumps(key_attrs, sort_keys=True).encode()).hexdigest()


def item_key_attrs(item: SupplierItem) -> dict:
    return {
        "category": item.category or None,
        "position": item.position or None,
        "fitment": item.fitment_label or None,
        "dims": list(item.dims_cm) if item.dims_cm else None,
        "oe_numbers": sorted(item.oe_numbers),
    }


def changed_key_attrs(item: SupplierItem, rec: NormalizedRecord) -> dict:
    old, new = item_key_attrs(item), rec.key_attrs()
    return {k: {"old": old[k], "new": new[k]} for k in old if old[k] != new[k]}


def _apply_attrs(item: SupplierItem, rec: NormalizedRecord, brand: str) -> None:
    fit = rec.fitment
    item.source_ref = rec.source_ref
    item.brand = brand
    item.name = rec.name
    item.category = rec.category or ""
    item.position = rec.position or ""
    item.fitment_make = fit.make if fit else ""
    item.fitment_model = fit.model if fit else ""
    item.year_from = fit.year_from if fit else None
    item.year_to = fit.year_to if fit else None
    item.dims_cm = list(rec.dims) if rec.dims else None
    item.oe_numbers = sorted(n for _, n in rec.oe_numbers)
    item.attr_hash = attr_hash(rec.key_attrs())


def _sync_field_values(item, record, rec: NormalizedRecord) -> list[str]:
    """Create a new current FieldValue for every field whose raw text changed. Returns fields."""
    current = {fv.field: fv for fv in item.field_values.filter(is_current=True)}
    changed = []
    for fld in TRACKED_FIELDS:
        entry = record.mapped.get(fld) or {}
        raw = entry.get("value", "")
        old = current.get(fld)
        if old is None and not raw:
            continue
        if old is not None and old.raw_value == raw:
            continue
        if old is not None:
            old.is_current = False
            old.save(update_fields=["is_current", "modified"])
        FieldValue.objects.create(
            item=item, field=fld, raw_value=raw, normalized_value=rec.normalized.get(fld),
            source_record=record, source_column=entry.get("column", ""),
        )
        changed.append(fld)
    return changed


def _sync_part_numbers(item, record, rec: NormalizedRecord) -> None:
    wanted = {(PartNumber.Kind.SUPPLIER_SKU, normalize_number(rec.supplier_part_no)):
              rec.supplier_part_no}
    for raw, norm in rec.oe_numbers:
        wanted.setdefault((PartNumber.Kind.OE, norm), raw)
    existing = {(pn.kind, pn.number_norm): pn for pn in item.part_numbers.all()}
    for key, pn in existing.items():
        if key not in wanted:
            pn.delete()  # history of the raw value stays in FieldValue
    for (kind, norm), raw in wanted.items():
        if (kind, norm) not in existing and norm:
            PartNumber.objects.create(item=item, kind=kind, number_raw=raw, number_norm=norm,
                                      source_record=record)


def _sync_offer(item, record, rec: NormalizedRecord) -> bool:
    new = (rec.price, rec.currency or "", rec.moq, rec.quote_date)
    if all(v in (None, "") for v in new):
        return False
    current = item.offers.filter(is_current=True).first()
    if current and (current.price, current.currency, current.moq, current.quote_date) == new:
        return False
    if current:
        current.is_current = False
        current.save(update_fields=["is_current", "modified"])
    SupplierOffer.objects.create(item=item, price=rec.price, currency=rec.currency or "",
                                 moq=rec.moq, quote_date=rec.quote_date, source_record=record)
    return True


def create_item(supplier, record, rec: NormalizedRecord, brand: str) -> SupplierItem:
    item = SupplierItem(supplier=supplier, supplier_part_no=rec.supplier_part_no,
                        current_record=record, product=Product.objects.create())
    _apply_attrs(item, rec, brand)
    item.save()
    _sync_field_values(item, record, rec)
    _sync_part_numbers(item, record, rec)
    _sync_offer(item, record, rec)
    return item


def update_item(item: SupplierItem, record, rec: NormalizedRecord, brand: str) -> list[str]:
    item.current_record = record
    item.present_in_latest = True
    _apply_attrs(item, rec, brand)
    item.save()
    changed = _sync_field_values(item, record, rec)
    _sync_part_numbers(item, record, rec)
    _sync_offer(item, record, rec)
    return changed
