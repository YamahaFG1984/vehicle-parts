from django.contrib import admin

from .models import ImportBatch, SourceFile, SourceRecord, Supplier


class ReadOnlyAdmin(admin.ModelAdmin):
    """Evidence is append-only: visible in admin, never edited there."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "created"]
    search_fields = ["code", "name"]


@admin.register(SourceFile)
class SourceFileAdmin(ReadOnlyAdmin):
    list_display = ["original_name", "supplier", "file_type", "size", "sha256", "created"]
    list_filter = ["supplier", "file_type"]
    search_fields = ["original_name", "sha256"]


@admin.register(ImportBatch)
class ImportBatchAdmin(ReadOnlyAdmin):
    list_display = ["pk", "source_file", "status", "stats", "ruleset_version", "created"]
    list_filter = ["status", "source_file__supplier"]


@admin.register(SourceRecord)
class SourceRecordAdmin(ReadOnlyAdmin):
    list_display = ["locator_label", "supplier_part_no", "diff_status", "batch"]
    list_filter = ["diff_status", "supplier", "batch"]
    search_fields = ["supplier_part_no", "source_ref", "locator_label"]
