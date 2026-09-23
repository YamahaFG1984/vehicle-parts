from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.exports.exporters import EXPORTS, export_all


class Command(BaseCommand):
    help = "导出主数据、供应商报价、待人工确认清单、导入报告（xlsx）。"

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.EXPORT_DIR))
        parser.add_argument("--only", nargs="*", choices=list(EXPORTS),
                            help="只导出其中几项：master offers review report")

    def handle(self, *args, **opts):
        for path in export_all(Path(opts["out"]), opts["only"]):
            self.stdout.write(self.style.SUCCESS(f"已导出 {path}"))
