"""Catalog layer: derived from evidence + rules; every value points back to a SourceRecord."""

import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse

from apps.core.models import TimeStampedModel


class Product(TimeStampedModel):
    """A normalized product. Every SupplierItem belongs to exactly one (a singleton at first)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        RETIRED = "retired", "已并入/退役"

    code = models.CharField(max_length=16, unique=True, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_default=Status.ACTIVE)
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="absorbed"
    )
    category = models.CharField(max_length=64, blank=True)
    position = models.CharField(max_length=16, blank=True)
    fitment_label = models.CharField(max_length=200, blank=True)
    dims_label = models.CharField(max_length=64, blank=True)
    attr_consensus = models.JSONField(
        default=dict, blank=True, help_text="字段 → {value, agreed: bool, values: [...]}"
    )

    class Meta:
        ordering = ["code"]
        verbose_name = "归一产品"
        verbose_name_plural = "归一产品"

    def __str__(self):
        return self.code or f"Product#{self.pk}"

    def get_absolute_url(self):
        return reverse("catalog:product_detail", args=[self.code])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.code:
            self.code = f"P-{self.pk:06d}"
            super().save(update_fields=["code"])


class SupplierItem(TimeStampedModel):
    """A supplier's listing, identified by (supplier, supplier_part_no) across imports."""

    supplier = models.ForeignKey(
        "ingestion.Supplier", on_delete=models.PROTECT, related_name="items"
    )
    supplier_part_no = models.CharField(max_length=100)
    source_ref = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=300, blank=True)
    category = models.CharField(max_length=64, blank=True)
    position = models.CharField(max_length=16, blank=True)
    fitment_make = models.CharField(max_length=64, blank=True)
    fitment_model = models.CharField(max_length=64, blank=True)
    year_from = models.PositiveSmallIntegerField(null=True, blank=True)
    year_to = models.PositiveSmallIntegerField(null=True, blank=True)
    dims_cm = ArrayField(models.FloatField(), size=3, null=True, blank=True)
    oe_numbers = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="items"
    )
    current_record = models.ForeignKey(
        "ingestion.SourceRecord", on_delete=models.PROTECT, related_name="+"
    )
    attr_hash = models.CharField(max_length=64, help_text="关键归一属性的哈希，用作匹配证据指纹")
    present_in_latest = models.BooleanField(default=True)

    class Meta:
        ordering = ["supplier__code", "supplier_part_no"]
        verbose_name = "供应商条目"
        verbose_name_plural = "供应商条目"
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "supplier_part_no"], name="uniq_item_per_supplier"
            ),
        ]
        indexes = [
            GinIndex(fields=["oe_numbers"], name="item_oe_gin"),
            GinIndex(fields=["name"], name="item_name_trgm", opclasses=["gin_trgm_ops"]),
            models.Index(fields=["category", "fitment_make", "fitment_model"]),
        ]

    def __str__(self):
        return f"{self.supplier.code}:{self.supplier_part_no}"

    def get_absolute_url(self):
        return reverse("catalog:item_detail", args=[self.pk])

    @property
    def label(self):
        return f"{self.supplier.code}:{self.supplier_part_no}"

    @property
    def fitment_label(self):
        parts = [self.fitment_make, self.fitment_model]
        if self.year_from:
            parts.append(f"{self.year_from}-{self.year_to or ''}")
        return " ".join(p for p in parts if p)

    @property
    def dims_label(self):
        if not self.dims_cm:
            return ""
        return " x ".join(f"{d:g}" for d in self.dims_cm) + " cm"


class FieldValue(TimeStampedModel):
    """Field-level provenance: which row and column a value came from. History is kept."""

    item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="field_values")
    field = models.CharField(max_length=32)
    raw_value = models.TextField(blank=True)
    normalized_value = models.JSONField(null=True, blank=True)
    source_record = models.ForeignKey(
        "ingestion.SourceRecord", on_delete=models.PROTECT, related_name="field_values"
    )
    source_column = models.CharField(max_length=200, blank=True)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["item_id", "field", "-created"]
        verbose_name = "字段来源"
        verbose_name_plural = "字段来源"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "field"],
                condition=models.Q(is_current=True),
                name="uniq_current_field_value",
            ),
        ]

    def __str__(self):
        return f"{self.item} {self.field}={self.raw_value}"


class PartNumber(TimeStampedModel):
    class Kind(models.TextChoices):
        SUPPLIER_SKU = "supplier_sku", "供应商/品牌编号"
        OE = "oe", "OE/交叉参考号"

    item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="part_numbers")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    number_raw = models.CharField(max_length=100)
    number_norm = models.CharField(max_length=100, db_index=True)
    source_record = models.ForeignKey(
        "ingestion.SourceRecord", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["kind", "number_norm"]
        verbose_name = "编号"
        verbose_name_plural = "编号"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "kind", "number_norm"], name="uniq_part_number_per_item"
            ),
        ]
        indexes = [
            GinIndex(fields=["number_norm"], name="pn_norm_trgm", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return self.number_raw


class SupplierOffer(TimeStampedModel):
    """One quote from one supplier at one point in time. Never converted, never overwritten."""

    item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="offers")
    price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    moq = models.PositiveIntegerField(null=True, blank=True)
    quote_date = models.DateField(null=True, blank=True)
    source_record = models.ForeignKey(
        "ingestion.SourceRecord", on_delete=models.PROTECT, related_name="offers"
    )
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["item_id", "-quote_date", "-created"]
        verbose_name = "供应商报价"
        verbose_name_plural = "供应商报价"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__isnull=True) | models.Q(price__gte=0),
                name="offer_price_non_negative",
            ),
            models.UniqueConstraint(
                fields=["item"], condition=models.Q(is_current=True), name="uniq_current_offer"
            ),
        ]

    def __str__(self):
        return f"{self.item} {self.price} {self.currency}"


# ---------------------------------------------------------------------------- images


def item_image_upload_to(instance, filename):
    ext = Path(filename).suffix.lower()
    return f"item_images/{instance.item_id}/{uuid.uuid4().hex}{ext}"


def item_thumb_upload_to(instance, filename):
    return f"item_images/{instance.item_id}/thumbs/{uuid.uuid4().hex}.jpg"


class ItemImage(TimeStampedModel):
    """A photo of one SKU. Product pages show the images of all their member SKUs, so images
    follow their SKU automatically when products are merged or split."""

    class Source(models.TextChoices):
        SUPPLIER = "supplier", "供应商提供"
        OWN_PHOTO = "own_photo", "自己拍摄"
        CATALOG = "catalog", "目录截图"
        OTHER = "other", "其他"

    item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=item_image_upload_to, width_field="width",
                              height_field="height", max_length=300)
    thumbnail = models.ImageField(upload_to=item_thumb_upload_to, blank=True, max_length=300)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.SUPPLIER)
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["item_id", "-is_primary", "created"]
        verbose_name = "SKU 图片"
        verbose_name_plural = "SKU 图片"
        constraints = [
            models.UniqueConstraint(fields=["item"], condition=models.Q(is_primary=True),
                                    name="uniq_primary_image_per_item"),
        ]

    def __str__(self):
        return f"{self.item} · {self.original_name or self.pk}"

    def get_absolute_url(self):
        return reverse("catalog:item_image", args=[self.pk])

    @property
    def thumb_url(self):
        return reverse("catalog:item_image", args=[self.pk]) + "?size=thumb"


# ---------------------------------------------------------------------------- stock


class StockImport(TimeStampedModel):
    """One uploaded stock file (e.g. an ERP export). Rows it produced point back here."""

    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    stats = models.JSONField(default=dict, blank=True)
    report = models.JSONField(default=dict, blank=True, help_text="未匹配的行、无法解析的行")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created"]
        verbose_name = "库存导入"
        verbose_name_plural = "库存导入"

    def __str__(self):
        return f"#{self.pk} {self.original_name}"


class StockLevel(TimeStampedModel):
    """Stock snapshot of one SKU in one warehouse. A new snapshot supersedes the previous one
    (kept as history); stock movements themselves belong to the ERP / WMS."""

    class Source(models.TextChoices):
        MANUAL = "manual", "手工录入"
        IMPORT = "import", "库存表导入"

    item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="stock_levels")
    warehouse = models.CharField(max_length=64, default="主仓")
    quantity = models.IntegerField()
    as_of = models.DateField(help_text="库存截至日期")
    source = models.CharField(max_length=8, choices=Source.choices)
    stock_import = models.ForeignKey(StockImport, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="levels")
    locator = models.CharField(max_length=300, blank=True, help_text="导入文件中的位置")
    note = models.CharField(max_length=200, blank=True)
    is_current = models.BooleanField(default=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["item_id", "warehouse", "-is_current", "-as_of", "-created"]
        verbose_name = "库存"
        verbose_name_plural = "库存"
        constraints = [
            models.UniqueConstraint(fields=["item", "warehouse"], condition=models.Q(is_current=True),
                                    name="uniq_current_stock_per_warehouse"),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="stock_non_negative"),
        ]

    def __str__(self):
        return f"{self.item} @ {self.warehouse}: {self.quantity}"
