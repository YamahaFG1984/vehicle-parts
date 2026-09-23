"""Human review: decisions on candidates and issues. Every decision is audited, then reclustered."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from .engine import run_matching
from .models import Issue, MatchCandidate, ReviewDecision

MC = MatchCandidate
ACTION_TO_STATUS = {
    ReviewDecision.Action.MERGE: MC.Status.ACCEPTED,
    ReviewDecision.Action.REJECT: MC.Status.REJECTED,
    ReviewDecision.Action.NEEDS_INFO: MC.Status.NEEDS_INFO,
}
ACTION_ALIASES = {
    "merge": ReviewDecision.Action.MERGE, "合并": ReviewDecision.Action.MERGE,
    "确认合并": ReviewDecision.Action.MERGE, "same": ReviewDecision.Action.MERGE,
    "reject": ReviewDecision.Action.REJECT, "不同": ReviewDecision.Action.REJECT,
    "不是同一产品": ReviewDecision.Action.REJECT, "different": ReviewDecision.Action.REJECT,
    "split": ReviewDecision.Action.REJECT, "拆分": ReviewDecision.Action.REJECT,
    "needs_info": ReviewDecision.Action.NEEDS_INFO, "待补充": ReviewDecision.Action.NEEDS_INFO,
    "resolve": ReviewDecision.Action.RESOLVE, "已解决": ReviewDecision.Action.RESOLVE,
    "ignore": ReviewDecision.Action.IGNORE, "忽略": ReviewDecision.Action.IGNORE,
}


def parse_action(value: str) -> str:
    key = (value or "").strip().lower()
    if key not in ACTION_ALIASES:
        raise ValidationError(f"无法识别的决定：{value!r}（可用：{', '.join(sorted(ACTION_ALIASES))}）")
    return ACTION_ALIASES[key]


def _snapshot(cand: MatchCandidate) -> dict:
    return {
        "a": str(cand.item_a), "b": str(cand.item_b), "status_before": cand.status,
        "classification": cand.classification, "reasons": cand.reasons,
        "conflicts": cand.conflicts, "missing": cand.missing, "evidence_hash": cand.evidence_hash,
    }


def decide_candidate(cand: MatchCandidate, action: str, *, user=None, actor_name: str = "",
                     note: str = "", via: str = ReviewDecision.Via.WEB,
                     recluster: bool = True) -> MatchCandidate:
    if action not in ACTION_TO_STATUS:
        raise ValidationError(f"候选只能做 合并 / 不同 / 待补充，收到：{action}")
    hard = [c["field"] for c in cand.conflicts if c.get("hard")]
    if action == ReviewDecision.Action.MERGE and hard and not note.strip():
        raise ValidationError(f"该候选存在硬冲突（{', '.join(hard)}），确认合并时必须填写备注说明理由")
    with transaction.atomic():
        ReviewDecision.objects.create(
            candidate=cand, action=action, actor=user,
            actor_name=actor_name or (user.get_username() if user else ""),
            via=via, note=note, snapshot=_snapshot(cand),
        )
        cand.status = ACTION_TO_STATUS[action]
        cand.decided_by = MC.DecidedBy.HUMAN
        cand.save(update_fields=["status", "decided_by", "modified"])
    if recluster:
        run_matching()
    cand.refresh_from_db()
    return cand


def decide_issue(issue: Issue, action: str, *, user=None, actor_name: str = "", note: str = "",
                 via: str = ReviewDecision.Via.WEB) -> Issue:
    mapping = {ReviewDecision.Action.RESOLVE: Issue.Status.RESOLVED,
               ReviewDecision.Action.IGNORE: Issue.Status.IGNORED}
    if action not in mapping:
        raise ValidationError(f"异常只能做 已解决 / 忽略，收到：{action}")
    with transaction.atomic():
        ReviewDecision.objects.create(
            issue=issue, action=action, actor=user,
            actor_name=actor_name or (user.get_username() if user else ""), via=via, note=note,
            snapshot={"code": issue.code, "message": issue.message, "status_before": issue.status},
        )
        issue.status = mapping[action]
        issue.resolution_note = note
        issue.save(update_fields=["status", "resolution_note", "modified"])
    return issue
