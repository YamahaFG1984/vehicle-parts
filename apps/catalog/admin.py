from django.contrib import admin

from apps.ingestion.admin import ReadOnlyAdmin

from .models import FieldValue, PartNumber, Product, SupplierItem, SupplierOffer


class ItemInline(admin.TabularInline):
    model = SupplierItem
    fields = ["supplier", "supplier_part_no", "name", "category", "position", "fitment_model",
              "dims_cm"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["code", "status", "category", "position", "fitment_label", "dims_label"]
    list_filter = ["status", "category"]
    search_fields = ["code", "items__supplier_part_no"]
    readonly_fields = ["code", "status", "merged_into", "category", "position", "fitment_label",
                       "dims_label", "attr_consensus"]
    inlines = [ItemInline]

    def has_add_permission(self, request):
        return False


class FieldValueInline(admin.TabularInline):
    model = FieldValue
    fields = ["field", "raw_value", "normalized_value", "source_record", "source_column",
              "is_current"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SupplierItem)
class SupplierItemAdmin(ReadOnlyAdmin):
    list_display = ["__str__", "name", "category", "position", "fitment_model", "product",
                    "present_in_latest"]
    list_filter = ["supplier", "category", "present_in_latest"]
    search_fields = ["supplier_part_no", "name", "source_ref", "oe_numbers"]
    inlines = [FieldValueInline]


@admin.register(SupplierOffer)
class SupplierOfferAdmin(ReadOnlyAdmin):
    list_display = ["item", "price", "currency", "moq", "quote_date", "is_current"]
    list_filter = ["currency", "is_current", "item__supplier"]


@admin.register(PartNumber)
class PartNumberAdmin(ReadOnlyAdmin):
    list_display = ["number_raw", "number_norm", "kind", "item"]
    list_filter = ["kind"]
    search_fields = ["number_norm", "number_raw"]
