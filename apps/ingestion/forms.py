from django import forms


class UploadForm(forms.Form):
    file = forms.FileField(label="资料文件", help_text=".xlsx / .csv / .pdf")
    supplier = forms.SlugField(label="供应商代码", max_length=32, required=False,
                               help_text="留空时从文件名“供应商X”推断")
    supplier_name = forms.CharField(label="供应商名称", max_length=200, required=False)
    partial = forms.BooleanField(label="增补文件（不判定“本次未出现”）", required=False)
