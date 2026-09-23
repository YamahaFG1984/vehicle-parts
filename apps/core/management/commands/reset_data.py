from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import SupplierItem
from apps.core.reset import confirm, reset_business_data
from apps.ingestion.models import SourceFile


class Command(BaseCommand):
    help = "清空所有业务数据（导入批次、原件归档、档案、候选、异常、复核记录），保留用户账号。"

    def add_arguments(self, parser):
        parser.add_argument("--noinput", action="store_true", help="不询问，直接清空")

    def handle(self, *args, **opts):
        items, files = SupplierItem.objects.count(), SourceFile.objects.count()
        if not confirm(f"将删除 {items} 个条目、{files} 个归档原件及全部复核记录（用户账号保留）。继续？[y/N] ",
                       noinput=opts["noinput"]):
            raise CommandError("已取消")
        removed = reset_business_data()
        self.stdout.write(self.style.SUCCESS(f"已清空业务数据（删除 {items} 个条目、{removed} 个归档原件）"))
