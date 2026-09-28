from django import forms
from django.utils import timezone

from .models import ItemImage


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Several files from one <input multiple>; format checks happen in images.validate_upload."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={
            "accept": "image/jpeg,image/png,image/webp",
            # FilePond (static/js/uploads.js): check type and size, shrink to 2000 px before upload.
            "data-filepond": "", "data-upload-accept": ".jpg,.jpeg,.png,.webp", "data-upload-max-size": "10MB",
            "data-upload-image-max": "2000", "data-upload-type-hint": "支持 JPG、PNG、WebP 图片",
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data]
        return [single(data, initial)]


class ImageUploadForm(forms.Form):
    images = MultipleImageField(label="图片", help_text="可多选；JPG / PNG / WebP，每张不超过 10 MB")
    source = forms.ChoiceField(label="来源", choices=ItemImage.Source.choices,
                               initial=ItemImage.Source.SUPPLIER)
    caption = forms.CharField(label="说明", max_length=200, required=False)


class StockForm(forms.Form):
    warehouse = forms.CharField(label="仓库", max_length=64, initial="主仓")
    quantity = forms.IntegerField(label="数量", min_value=0)
    # <input type=date> only accepts ISO dates, whatever the locale format is.
    as_of = forms.DateField(label="截至日期", initial=timezone.localdate,
                            widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    note = forms.CharField(label="备注", max_length=200, required=False)


class StockImportForm(forms.Form):
    file = forms.FileField(
        label="库存表", help_text=".xlsx / .csv；需要“编号 / SKU”和“数量 / 库存”两列",
        widget=forms.ClearableFileInput(attrs={
            "accept": ".xlsx,.xlsm,.csv", "data-filepond": "", "data-upload-accept": ".xlsx,.xlsm,.csv",
            "data-upload-max-size": "50MB", "data-upload-type-hint": "支持 .xlsx、.csv（旧版 .xls 请先另存为 .xlsx）",
        }))
    supplier = forms.CharField(label="默认供应商代码", max_length=32, required=False,
                               help_text="文件中没有供应商列时使用")
    warehouse = forms.CharField(label="默认仓库", max_length=64, required=False,
                                help_text="文件中没有仓库列时使用，留空为“主仓”")
    as_of = forms.DateField(label="默认截至日期", required=False,
                            widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                            help_text="文件中没有日期列时使用，留空为今天")
