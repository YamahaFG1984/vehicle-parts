"""Write side of the catalog: turn one observed row into item / field provenance / numbers / offer."""

from __future__ import annotations

import hashlib
import json

from .models import FieldValue, PartNumber, Product, SupplierItem, SupplierOffer
from .normalizers import NormalizedRecord, normalize_number, normalize_record

# Semantic fields whose raw value is tracked with provenance.
TRACKED_FIELDS = [
    "supplier_part_no", "source_ref", "brand", "name", "category", "position", "fitment",
    "package_dims", "oe_numbers", "price", "currency", "moq", "quote_date",
]
OFFER_FIELDS = ("price", "currency", "moq", "quote_date")
# A change in these makes an import row a CONFLICT. Package size is soft evidence (as in
# matching), so a new carton is an UPDATE with a PACKAGE_DIMS_CHANGED note instead.
CONFLICT_FIELDS = ("category", "position", "fitment", "oe_numbers")


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
    return {k: {"old": old[k], "new": new[k]} for k in CONFLICT_FIELDS if old[k] != new[k]}


def changed_dims(item: SupplierItem, rec: NormalizedRecord) -> dict | None:
    old, new = item_key_attrs(item)["dims"], rec.key_attrs()["dims"]
    return {"old": old, "new": new} if old != new else None


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
    """New current FieldValue for every field whose raw text or normalized value changed
    (the latter happens when rules change). Returns the changed fields."""
    current = {fv.field: fv for fv in item.field_values.filter(is_current=True)}
    changed = []
    for fld in TRACKED_FIELDS:
        entry = record.mapped.get(fld) or {}
        raw = entry.get("value", "")
        old = current.get(fld)
        if old is None and not raw:
            continue
        normalized = rec.normalized.get(fld)
        if old is not None and old.raw_value == raw and old.normalized_value == normalized:
            continue
        if old is not None:
            old.is_current = False
            old.save(update_fields=["is_current", "modified"])
        FieldValue.objects.create(
            item=item, field=fld, raw_value=raw, normalized_value=normalized,
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
        pn = existing.get((kind, norm))
        if pn is not None and pn.number_raw != raw:  # same number, new spelling
            pn.number_raw, pn.source_record = raw, record
            pn.save(update_fields=["number_raw", "source_record", "modified"])
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
    item.supplier_part_no = rec.supplier_part_no  # latest spelling; history is in FieldValue
    item.current_record = record
    item.present_in_latest = True
    _apply_attrs(item, rec, brand)
    item.save()
    changed = _sync_field_values(item, record, rec)
    _sync_part_numbers(item, record, rec)
    _sync_offer(item, record, rec)
    return changed


def renormalize_item(item: SupplierItem) -> dict | None:
    """Re-apply the current rules to the item's current row (after synonyms or rules change).

    Uses the values stored on that row, so no file is needed. Returns what changed, or None.
    Field history is kept: changed normalized values get a new current FieldValue.
    """
    from apps.matching import issues as issue_service

    record = item.current_record
    override = (record.batch.mapping or {}).get("override") or {}
    values = {fld: entry.get("value", "") for fld, entry in record.mapped.items()}
    rec = normalize_record(values, override.get("sku_position_suffix"))
    before = (item.attr_hash, item.category, item.position, item.fitment_label)
    changed_attrs = changed_key_attrs(item, rec)
    dims = changed_dims(item, rec)
    _apply_attrs(item, rec, item.brand)
    changed_fields = _sync_field_values(item, record, rec)
    if before == (item.attr_hash, item.category, item.position, item.fitment_label) \
            and not changed_fields:
        return None
    item.save()
    _sync_part_numbers(item, record, rec)
    _sync_offer(item, record, rec)
    issue_service.refresh_normalizer_findings(item, record, rec.findings)
    changes = dict(changed_attrs)
    if dims:
        changes["dims"] = dims
    return {"fields": changed_fields, "attrs": changes}
