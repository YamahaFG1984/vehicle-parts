from django import template

from apps.matching.rules import FIELD_LABELS

register = template.Library()

EXTRA_LABELS = {"supplier_part_no": "供应商编号", "source_ref": "原始记录ID", "brand": "品牌",
                "package_dims": "尺寸(包装)", "oe_numbers": "OE 号", "price": "价格",
                "currency": "币种", "moq": "MOQ", "quote_date": "报价日期", "key_attrs": "关键属性"}


@register.filter
def show(value):
    """Human-readable rendering of comparison values (lists, dims, empties)."""
    if value in (None, "", []):
        return "（空）"
    if isinstance(value, (list, tuple)):
        if all(isinstance(x, (int, float)) for x in value):
            return " x ".join(f"{x:g}" for x in value)
        return ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return "；".join(f"{k}={v}" for k, v in value.items() if v not in (None, ""))
    return value


@register.filter
def field_label(name):
    return FIELD_LABELS.get(name) or EXTRA_LABELS.get(name) or name
