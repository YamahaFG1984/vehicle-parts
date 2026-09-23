from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.services import ImportFailed, import_source


class Command(BaseCommand):
    help = "导入一份供应商资料（xlsx / csv / pdf）。原件按 sha256 归档，重复导入会被识别。"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--supplier", help="供应商代码；缺省时从文件名“供应商X”推断")
        parser.add_argument("--supplier-name")
        parser.add_argument("--mapping", help="列映射 YAML（sheet / header_row / columns / brand）")
        parser.add_argument("--partial", action="store_true",
                            help="增补文件：不判定“本次未出现”")
        parser.add_argument("--dry-run", action="store_true", help="只解析与预览，不写库")
        parser.add_argument("--reprocess", action="store_true",
                            help="该文件已成功导入过时，仍用当前映射/规则重新处理（新建批次，复用已归档原件）")
        parser.add_argument("--no-match", action="store_true", help="导入后不自动运行匹配")

    def handle(self, *args, **opts):
        try:
            result = import_source(
                opts["path"], opts["supplier"], supplier_name=opts["supplier_name"],
                mapping_path=opts["mapping"], partial=opts["partial"], dry_run=opts["dry_run"],
                reprocess=opts["reprocess"],
            )
        except ImportFailed as exc:
            raise CommandError(str(exc)) from exc

        if result.duplicate:
            self.stdout.write(self.style.WARNING(
                f"该文件已导入过（内容相同，sha256 一致），记为批次 #{result.batch.pk} [duplicate]，"
                "未产生任何数据变化。如需用新的映射或规则重新处理，加 --reprocess。"))
            return
        self._print_mapping(result.parse)
        if result.dry_run:
            self.stdout.write(self.style.MIGRATE_HEADING("预览（未写库）"))
            for row in result.preview:
                self.stdout.write(
                    f"  {row['locator']:<48} {row['part_no']:<12} {row['status']:<9} "
                    f"{row['category'] or '?':<24} {row['position'] or '-':<6} "
                    f"{','.join(row['findings'])}")
            self.stdout.write(f"统计：{result.stats}")
            return
        batch = result.batch
        self.stdout.write(self.style.SUCCESS(f"批次 #{batch.pk} 完成：{batch.stats}"))
        if batch.report.get("not_in_latest"):
            self.stdout.write(f"本次未出现：{', '.join(batch.report['not_in_latest'])}")
        if not opts["no_match"]:
            from apps.matching.engine import run_matching

            summary = run_matching()
            self.stdout.write(self.style.SUCCESS(f"匹配完成：{summary.as_text()}"))

    def _print_mapping(self, parsed):
        for m in parsed.mappings:
            head = m["header_locator"]
            inherited = "（沿用上一页表头）" if m["inherited_header"] else ""
            self.stdout.write(self.style.MIGRATE_HEADING(f"表 {m['table']} 表头 {head}{inherited}"))
            for header, fld in m["columns"].items():
                self.stdout.write(f"  {header!r:<28} → {fld}")
            if m["unmapped"]:
                self.stdout.write(self.style.WARNING(f"  未识别列（原值已保留）：{m['unmapped']}"))
            if m["duplicates"]:
                self.stdout.write(self.style.WARNING(f"  重复映射列（已忽略）：{m['duplicates']}"))
        for p in parsed.problems:
            self.stdout.write(self.style.ERROR(f"  问题 {p['locator']}: {p['message']}"))
        for t in parsed.unmatched:
            self.stdout.write(self.style.WARNING(f"  表 {t['table']} 前几行（可用 --mapping 的 header_row 指定表头行）："))
            for r in t["rows"]:
                self.stdout.write(f"    {r['locator']}: {r['cells']}")
