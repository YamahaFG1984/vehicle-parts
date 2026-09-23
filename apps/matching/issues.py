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
