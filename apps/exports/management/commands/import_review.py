from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.exports.review_import import ReviewImportError, import_review


class Command(BaseCommand):
    help = "把填写了 decision / note 的 review_list.xlsx 回写到系统（全部成功才提交）。"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--reviewer", required=True, help="复核人姓名（写入审计日志）")

    def handle(self, *args, **opts):
        try:
            result = import_review(Path(opts["path"]), opts["reviewer"])
        except ReviewImportError as exc:
            for line in exc.errors:
                self.stderr.write(self.style.ERROR(line))
            raise CommandError(f"{len(exc.errors)} 处错误，未写入任何决定") from exc
        for line in result.skipped:
            self.stdout.write(self.style.WARNING(line))
        self.stdout.write(self.style.SUCCESS(
            f"已回写：候选 {result.candidates} 条，异常 {result.issues} 条（审计来源 xlsx）"))
