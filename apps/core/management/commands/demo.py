"""One-shot demo: reset business data, import the samples, match, export."""

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import SupplierItem
from apps.core.reset import confirm, reset_business_data
from apps.exports.exporters import export_all, export_search, scope_from_search
from apps.ingestion.services import import_source
from apps.matching.engine import run_matching

BASE = [("候选人材料_供应商A报价表.xlsx", "A", "供应商A"),
        ("候选人材料_供应商B报价表.xlsx", "B", "供应商B")]
SEARCH_EXAMPLE_QUERY = "OE-VNL-1001"
SEARCH_EXAMPLE_FILE = "search_result_OE-VNL-1001.xlsx"
INCREMENT = [("samples/demo/供应商A报价表_v2.xlsx", "A", "供应商A"),
             ("samples/demo/供应商C目录.pdf", "C", "供应商C")]


class Command(BaseCommand):
    help = "演示：清空业务数据 → 导入样本 → 匹配 → 导出。--with-increment 追加导入增量演示文件。"

    def add_arguments(self, parser):
        parser.add_argument("--with-increment", action="store_true")
        parser.add_argument("--out", default=str(settings.EXPORT_DIR))
        parser.add_argument("--noinput", action="store_true", help="不询问，直接清空业务数据")
        parser.add_argument("--reviewer", help="同时创建一个可登录的复核账号（staff）")
        parser.add_argument("--password", help="复核账号密码（与 --reviewer 一起使用）")

    def handle(self, *args, **opts):
        if SupplierItem.objects.exists() and not confirm(
                "将清空所有导入、档案、候选与复核记录（用户账号保留）。继续？[y/N] ",
                noinput=opts["noinput"]):
            raise CommandError("已取消")
        self.reset()
        steps = BASE + (INCREMENT if opts["with_increment"] else [])
        for rel, code, name in steps:
            path = settings.BASE_DIR / rel
            if not path.exists():
                raise CommandError(f"缺少文件 {path}（增量素材可用 make_demo_samples 生成）")
            result = import_source(path, code, supplier_name=name)
            summary = run_matching()
            self.stdout.write(self.style.SUCCESS(f"导入 {rel}: {result.batch.stats}"))
            self.stdout.write(f"  匹配：{summary.as_text()}")
        for path in export_all(Path(opts["out"])):
            self.stdout.write(self.style.SUCCESS(f"已导出 {path}"))
        # A search-and-export example: one OE number → the normalized product with every
        # brand number, each supplier's offer, provenance and related review items.
        example = Path(opts["out"]) / SEARCH_EXAMPLE_FILE
        export_search(example, scope_from_search(SEARCH_EXAMPLE_QUERY))
        self.stdout.write(self.style.SUCCESS(f"已导出查询结果示例 {example}"))
        if opts["reviewer"]:
            self.make_reviewer(opts["reviewer"], opts["password"])

    def reset(self):
        reset_business_data()
        self.stdout.write("已清空业务数据")

    def make_reviewer(self, username, password):
        if not password:
            raise CommandError("--reviewer 需要同时提供 --password")
        user, created = get_user_model().objects.get_or_create(
            username=username, defaults={"is_staff": True})
        user.is_staff = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"复核账号 {username} 已{'创建' if created else '更新'}"))
