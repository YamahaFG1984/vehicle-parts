from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.ingestion.admin import ReadOnlyAdmin

from . import services
from .engine import run_matching
from .models import Issue, MatchCandidate, ReviewDecision


def _decide(action, label):
    @admin.action(description=label)
    def act(modeladmin, request, queryset):
        done = 0
        for cand in queryset:
            try:
                services.decide_candidate(cand, action, user=request.user,
                                          note=f"Admin 批量操作：{label}",
                                          via=ReviewDecision.Via.ADMIN, recluster=False)
                done += 1
            except ValidationError as exc:
                messages.error(request, f"#{cand.pk}: {exc.messages[0]}")
        run_matching()
        messages.success(request, f"{done} 条已记录并重新聚类")
    act.__name__ = f"decide_{action}"
    return act


@admin.register(MatchCandidate)
class MatchCandidateAdmin(ReadOnlyAdmin):
    list_display = ["pk", "item_a", "item_b", "classification", "status", "decided_by",
                    "priority", "confidence", "reasons"]
    list_filter = ["status", "classification", "decided_by", "ruleset_version"]
    search_fields = ["item_a__supplier_part_no", "item_b__supplier_part_no"]
    actions = [_decide(ReviewDecision.Action.MERGE, "确认合并"),
               _decide(ReviewDecision.Action.REJECT, "不是同一产品 / 拆分"),
               _decide(ReviewDecision.Action.NEEDS_INFO, "待补充资料")]

    def has_change_permission(self, request, obj=None):
        return obj is None  # enables actions on the changelist, keeps rows read-only


@admin.register(Issue)
class IssueAdmin(ReadOnlyAdmin):
    list_display = ["pk", "code", "severity", "item", "message", "status"]
    list_filter = ["status", "severity", "code"]
    search_fields = ["message", "item__supplier_part_no"]


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(ReadOnlyAdmin):
    list_display = ["created", "action", "actor_name", "via", "candidate", "issue", "note"]
    list_filter = ["action", "via"]
