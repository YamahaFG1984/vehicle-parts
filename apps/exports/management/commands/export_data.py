from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.exports.exporters import (
    EXPORTS,
    export_all,
    export_search,
    scope_from_search,
    search_filename,
)


class Command(BaseCommand):
    help = ("导出 xlsx。不带查询条件时导出全量：主数据、供应商报价、待人工确认清单、导入报告；"
            "带 --query / --supplier / --state 时导出这次查询的整理结果（与网页搜索结果一致）。")

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.EXPORT_DIR))
        parser.add_argument("--only", nargs="*", choices=list(EXPORTS),
                            help="全量导出时只导出其中几项：master offers review report")
        parser.add_argument("--query", default="", help="任一编号 / OE / 关键词 / 品牌 / 产品编号")
        parser.add_argument("--supplier", default="", help="供应商代码或名称")
        parser.add_argument("--state", default="", choices=["", "已确认归一", "疑似重复", "独立产品", "待补充"])

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        if opts["query"] or opts["supplier"] or opts["state"]:
            scope = scope_from_search(opts["query"], opts["supplier"], opts["state"])
            if not scope.product_ids:
                self.stdout.write(self.style.WARNING("没有匹配的产品，仍导出一份只含查询条件的文件"))
            out.mkdir(parents=True, exist_ok=True)
            path = export_search(out / search_filename(scope.criteria), scope)
            self.stdout.write(self.style.SUCCESS(
                f"已导出查询结果 {path}（{len(scope.product_ids)} 个归一产品，"
                f"{len(scope.hit_item_ids)} 个命中条目）"))
            return
        for path in export_all(out, opts["only"]):
            self.stdout.write(self.style.SUCCESS(f"已导出 {path}"))
