"""Evidence layer: original files and every row observed in them. Rows here are append-only."""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Supplier(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["code"]
        verbose_name = "供应商"
        verbose_name_plural = "供应商"

    def __str__(self):
        return f"{self.code} · {self.name}"


def original_upload_to(instance, filename):
    # Content-addressed: two different files never share a path, so nothing is ever overwritten.
    return f"originals/{instance.sha256}/{filename}"


class SourceFile(TimeStampedModel):
    class FileType(models.TextChoices):
        XLSX = "xlsx", "Excel"
        CSV = "csv", "CSV"
        PDF = "pdf", "PDF"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="files")
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=original_upload_to, max_length=400)
    sha256 = models.CharField(max_length=64, unique=True)
    size = models.PositiveBigIntegerField()
    file_type = models.CharField(max_length=8, choices=FileType.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created"]
        verbose_name = "原始文件"
        verbose_name_plural = "原始文件"

    def __str__(self):
        return self.original_name


class ImportBatch(TimeStampedModel):
    class Status(models.TextChoices):
        RUNNING = "running", "进行中"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"
        DUPLICATE = "duplicate", "重复文件"

    source_file = models.ForeignKey(SourceFile, on_delete=models.PROTECT, related_name="batches")
    status = models.CharField(max_length=16, choices=Status.choices, db_default=Status.RUNNING)
    partial = models.BooleanField(default=False, help_text="增补文件：不做“本次未出现”判定")
    mapping = models.JSONField(default=dict, blank=True, help_text="本次使用的表头识别与列映射")
    ruleset_version = models.CharField(max_length=32, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    report = models.JSONField(default=dict, blank=True, help_text="未识别列、未出现条目等")
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created"]
        verbose_name = "导入批次"
        verbose_name_plural = "导入批次"

    def __str__(self):
        return f"#{self.pk} {self.source_file} ({self.get_status_display()})"


class SourceRecord(TimeStampedModel):
    """One observed row: the raw values exactly as found, plus where they were found."""

    class DiffStatus(models.TextChoices):
        NEW = "new", "新增"
        UNCHANGED = "unchanged", "未变"
        UPDATED = "updated", "更新"
        CONFLICT = "conflict", "冲突"
        DUPLICATE = "duplicate", "文件内重复"
        INVALID = "invalid", "无法识别"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="records")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="records")
    locator = models.JSONField(help_text='{"sheet": .., "row": ..} 或 {"page": .., "table": .., "row": ..}')
    locator_label = models.CharField(max_length=400)
    source_ref = models.CharField(max_length=100, blank=True)
    supplier_part_no = models.CharField(max_length=100, blank=True)
    raw_data = models.JSONField(help_text="[[原表头, 原值（文本）], ...]，保持原列顺序")
    mapped = models.JSONField(default=dict, help_text="语义字段 → {value, column}")
    row_hash = models.CharField(max_length=64)
    diff_status = models.CharField(max_length=16, choices=DiffStatus.choices)

    class Meta:
        ordering = ["batch_id", "id"]
        verbose_name = "原始记录"
        verbose_name_plural = "原始记录"
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "locator_label"], name="uniq_record_locator_per_batch"
            ),
        ]
        indexes = [models.Index(fields=["supplier", "supplier_part_no"])]

    def __str__(self):
        return self.locator_label
