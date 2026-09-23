"""Judgement layer: candidate pairs, record issues and the append-only review log."""

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import TimeStampedModel


class MatchCandidate(TimeStampedModel):
    class Classification(models.TextChoices):
        AUTO_CONFIRMED = "auto_confirmed", "已确认归一（规则）"
        SUSPECT = "suspect", "疑似重复"
        AUTO_REJECTED = "auto_rejected", "规则排除"

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "已合并"
        PENDING = "pending", "待确认"
        REJECTED = "rejected", "不是同一产品"
        NEEDS_INFO = "needs_info", "待补充资料"
        SUPERSEDED = "superseded", "已失效"

    class DecidedBy(models.TextChoices):
        RULE = "rule", "规则"
        HUMAN = "human", "人工"

    item_a = models.ForeignKey(
        "catalog.SupplierItem", on_delete=models.CASCADE, related_name="candidates_as_a"
    )
    item_b = models.ForeignKey(
        "catalog.SupplierItem", on_delete=models.CASCADE, related_name="candidates_as_b"
    )
    classification = models.CharField(max_length=16, choices=Classification.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    decided_by = models.CharField(max_length=8, choices=DecidedBy.choices, default=DecidedBy.RULE)
    priority = models.PositiveSmallIntegerField(default=9, help_text="越小越优先")
    score = models.PositiveSmallIntegerField(default=0)
    confidence = models.FloatField(default=0)
    reasons = models.JSONField(default=list, help_text="原因码列表")
    comparisons = models.JSONField(default=dict, help_text="字段 → {result, a, b}")
    conflicts = models.JSONField(default=list)
    missing = models.JSONField(default=list)
    suggested_action = models.TextField(blank=True)
    ruleset_version = models.CharField(max_length=32)
    evidence_hash = models.CharField(max_length=64)
    rule_classification = models.CharField(
        max_length=16, choices=Classification.choices, blank=True,
        help_text="规则最近一次给出的分类（人工决定后仍保留，便于对照）",
    )

    class Meta:
        ordering = ["priority", "-confidence", "id"]
        verbose_name = "匹配候选"
        verbose_name_plural = "匹配候选"
        constraints = [
            models.UniqueConstraint(fields=["item_a", "item_b"], name="uniq_candidate_pair"),
            models.CheckConstraint(
                condition=models.Q(item_a__lt=models.F("item_b")), name="candidate_pair_ordered"
            ),
        ]
        indexes = [models.Index(fields=["status", "priority"])]

    def __str__(self):
        return f"#{self.pk} {self.item_a} ↔ {self.item_b} [{self.status}]"

    def get_absolute_url(self):
        return reverse("matching:candidate_detail", args=[self.pk])


class Issue(TimeStampedModel):
    class Severity(models.TextChoices):
        ERROR = "error", "错误"
        WARNING = "warning", "警告"
        INFO = "info", "提示"

    class Status(models.TextChoices):
        OPEN = "open", "未处理"
        RESOLVED = "resolved", "已解决"
        IGNORED = "ignored", "忽略"

    item = models.ForeignKey(
        "catalog.SupplierItem", null=True, blank=True, on_delete=models.CASCADE,
        related_name="issues",
    )
    source_record = models.ForeignKey(
        "ingestion.SourceRecord", null=True, blank=True, on_delete=models.CASCADE,
        related_name="issues",
    )
    batch = models.ForeignKey(
        "ingestion.ImportBatch", null=True, blank=True, on_delete=models.CASCADE,
        related_name="issues",
    )
    code = models.CharField(max_length=40)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    field = models.CharField(max_length=32, blank=True)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "severity", "id"]
        verbose_name = "异常"
        verbose_name_plural = "异常"
        constraints = [
            models.UniqueConstraint(
                fields=["source_record", "code", "field"],
                condition=models.Q(source_record__isnull=False),
                name="uniq_issue_per_record",
            ),
        ]
        indexes = [models.Index(fields=["status", "code"])]

    def __str__(self):
        return f"{self.code} {self.message}"


class ReviewDecision(models.Model):
    """Append-only audit log of every decision, human or automatic."""

    class Action(models.TextChoices):
        MERGE = "merge", "确认合并"
        REJECT = "reject", "不是同一产品"
        NEEDS_INFO = "needs_info", "待补充资料"
        REOPEN = "reopen", "重新打开"
        RULE = "rule", "规则决定"
        RESOLVE = "resolve", "异常已解决"
        IGNORE = "ignore", "异常忽略"

    class Via(models.TextChoices):
        WEB = "web", "Web"
        XLSX = "xlsx", "xlsx 回写"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "系统"

    candidate = models.ForeignKey(
        MatchCandidate, null=True, blank=True, on_delete=models.CASCADE, related_name="decisions"
    )
    issue = models.ForeignKey(
        Issue, null=True, blank=True, on_delete=models.CASCADE, related_name="decisions"
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    actor_name = models.CharField(max_length=150, blank=True)
    via = models.CharField(max_length=8, choices=Via.choices)
    note = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]
        verbose_name = "复核记录"
        verbose_name_plural = "复核记录"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(candidate__isnull=False) | models.Q(issue__isnull=False),
                name="decision_has_target",
            ),
        ]

    def __str__(self):
        return f"{self.get_action_display()} by {self.actor_name or self.actor or 'system'}"
