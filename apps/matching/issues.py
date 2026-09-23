"""Persist normalizer findings and pipeline problems as Issues, and retire superseded ones."""

from __future__ import annotations

from django.utils import timezone

from apps.catalog.normalizers import Finding

from .models import Issue


def record_findings(findings: list[Finding], *, item=None, record=None, batch=None) -> int:
    created = 0
    for f in findings:
        _, was_created = Issue.objects.get_or_create(
            source_record=record,
            code=f.code,
            field=f.field,
            defaults={
                "item": item,
                "batch": batch,
                "severity": f.severity,
                "message": f.message,
                "details": f.details,
            },
        )
        created += was_created
    return created


def open_item_issue(item, code: str, severity: str, message: str, *, field: str = "",
                    details: dict | None = None, batch=None) -> Issue:
    """Item-level issue not tied to a single row (e.g. NOT_IN_LATEST); one open issue per code."""
    issue = Issue.objects.filter(item=item, code=code, field=field, status=Issue.Status.OPEN,
                                 source_record__isnull=True).first()
    if issue:
        issue.message, issue.details = message, details or {}
        issue.save(update_fields=["message", "details", "modified"])
        return issue
    return Issue.objects.create(item=item, code=code, severity=severity, field=field,
                                message=message, details=details or {}, batch=batch)


def resolve_item_issues(item, *, codes=None, exclude_record=None, note: str) -> int:
    qs = Issue.objects.filter(item=item, status=Issue.Status.OPEN)
    if codes is not None:
        qs = qs.filter(code__in=codes)
    if exclude_record is not None:
        qs = qs.exclude(source_record=exclude_record)
    return qs.update(status=Issue.Status.RESOLVED, resolution_note=note, modified=timezone.now())


def open_batch_problem(batch, problem: dict) -> Issue:
    """A problem that belongs to the file rather than a row (e.g. a PDF page without a table)."""
    return Issue.objects.create(
        batch=batch, code="UNPARSEABLE", severity=Issue.Severity.ERROR, field="",
        message=problem["message"], details={"locator": problem.get("locator", {})},
    )


SYSTEM_NOTE = "[系统] 重新计算后不再适用"


def sync_system_findings(items: dict, findings: dict, *, codes) -> None:
    """Keep engine-computed issues in step with the latest run.

    Open the ones that apply now (reopening only those the system closed itself), and close
    the ones that no longer apply. Issues a reviewer resolved or ignored are left alone.
    """
    wanted = {(item_id, f.code) for item_id, fs in findings.items() for f in fs}
    for item_id, fs in findings.items():
        item = items[item_id]
        for f in fs:
            issue, created = Issue.objects.get_or_create(
                source_record=item.current_record, code=f.code, field=f.field,
                defaults={"item": item, "severity": f.severity, "message": f.message,
                          "details": f.details})
            if not created and (issue.message != f.message or (
                    issue.status == Issue.Status.RESOLVED and issue.resolution_note == SYSTEM_NOTE)):
                issue.message, issue.details = f.message, f.details
                if issue.resolution_note == SYSTEM_NOTE:
                    issue.status, issue.resolution_note = Issue.Status.OPEN, ""
                issue.save()
    for issue in Issue.objects.filter(code__in=codes, status=Issue.Status.OPEN):
        item = items.get(issue.item_id)
        stale_row = item is not None and issue.source_record_id != item.current_record_id
        if (issue.item_id, issue.code) not in wanted or stale_row:
            issue.status, issue.resolution_note = Issue.Status.RESOLVED, SYSTEM_NOTE
            issue.save(update_fields=["status", "resolution_note", "modified"])


RULES_NOTE = "[系统] 按新规则重新标准化后不再适用"


def refresh_normalizer_findings(item, record, findings) -> None:
    """After re-normalizing a row: close normalizer issues that no longer apply, open new ones."""
    from apps.catalog.normalizers import NORMALIZER_CODES

    wanted = {(f.code, f.field) for f in findings}
    for issue in Issue.objects.filter(source_record=record, code__in=NORMALIZER_CODES,
                                      status=Issue.Status.OPEN):
        if (issue.code, issue.field) not in wanted:
            issue.status, issue.resolution_note = Issue.Status.RESOLVED, RULES_NOTE
            issue.save(update_fields=["status", "resolution_note", "modified"])
    for f in findings:
        issue, created = Issue.objects.get_or_create(
            source_record=record, code=f.code, field=f.field,
            defaults={"item": item, "severity": f.severity, "message": f.message,
                      "details": f.details})
        if not created and issue.status == Issue.Status.RESOLVED \
                and issue.resolution_note == RULES_NOTE:
            issue.status, issue.resolution_note = Issue.Status.OPEN, ""
            issue.message, issue.details = f.message, f.details
            issue.save()
