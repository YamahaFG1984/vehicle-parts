from django.core.management.base import BaseCommand

from apps.matching.engine import run_matching


class Command(BaseCommand):
    help = "按当前规则重新评估所有候选并重新聚类；人工决定在证据未变时保持不变。"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="全量评估（默认即为全量，保留该参数以便脚本可读）")
        parser.add_argument("--dry-run", action="store_true", help="只输出变化，不写库")

    def handle(self, *args, **opts):
        summary = run_matching(dry_run=opts["dry_run"])
        if opts["dry_run"]:
            self.stdout.write(self.style.MIGRATE_HEADING(f"变化（未写库）：{len(summary.changes)} 项"))
            for line in summary.changes:
                self.stdout.write(f"  {line}")
        self.stdout.write(self.style.SUCCESS(summary.as_text()))
