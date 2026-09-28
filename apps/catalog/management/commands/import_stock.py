from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.stock import StockImportError, import_stock


class Command(BaseCommand):
    help = ("导入库存表（xlsx / csv，如 ERP 导出）：按“供应商 + 编号”匹配 SKU，写入各仓库的库存快照；"
            "需要“编号 / SKU”和“数量 / 库存”两列，供应商、仓库、日期列可选。")

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--supplier", default="", help="文件中没有供应商列时使用的供应商代码")
        parser.add_argument("--warehouse", default="", help="文件中没有仓库列时使用，默认“主仓”")
        parser.add_argument("--as-of", default="", help="文件中没有日期列时使用（YYYY-MM-DD），默认今天")

    def handle(self, *args, **opts):
        try:
            as_of = date.fromisoformat(opts["as_of"]) if opts["as_of"] else None
        except ValueError as exc:
            raise CommandError("--as-of 需要 YYYY-MM-DD 格式") from exc
        try:
            result = import_stock(opts["path"], supplier=opts["supplier"],
                                  warehouse=opts["warehouse"], as_of=as_of)
        except StockImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"库存导入 #{result.stock_import.pk}：{dict(result.stats)}"))
        for u in result.unmatched:
            self.stdout.write(self.style.WARNING(f"  未匹配 {u['locator']} {u['part_no']}：{u['reason']}"))
        for p in result.problems:
            self.stdout.write(self.style.ERROR(f"  无法识别 {p['locator']}：{p['message']}"))
