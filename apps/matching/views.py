from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import selectors, services
from .engine import run_matching
from .forms import CandidateDecisionForm, IssueDecisionForm
from .models import Issue, MatchCandidate, ReviewDecision
from .rules import FIELD_LABELS, REASON_LABELS

OPEN = [MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]


def group_queue(request):
    reason = request.GET.get("reason", "")
    groups = selectors.review_groups(reason)
    return render(request, "matching/group_queue.html", {
        "groups": groups, "reason": reason, "reason_labels": REASON_LABELS,
        "pair_count": sum(len(g.candidates) for g in groups),
    })


@require_http_methods(["GET", "POST"])
def group_detail(request, key):
    group = selectors.group_containing(key)
    if group is None:
        messages.info(request, "该组已处理完毕。")
        return redirect("matching:group_queue")
    error = None
    if request.method == "POST":
        note = request.POST.get("note", "").strip()
        bulk = request.POST.get("bulk")
        plan = []
        for cand in group.candidates:
            choice = "reject" if bulk == "reject_all" else request.POST.get(f"decision_{cand.pk}", "")
            if not choice:
                continue
            action = services.parse_action(choice)
            hard = [c["field"] for c in cand.conflicts if c.get("hard")]
            if action == ReviewDecision.Action.MERGE and hard and not note:
                error = f"候选 #{cand.pk} 有硬冲突（{', '.join(hard)}），确认合并必须填写备注"
                break
            plan.append((cand, action))
        if not error and not plan:
            error = "没有选择任何决定"
        if not error:
            for cand, action in plan:
                services.decide_candidate(cand, action, user=request.user, note=note,
                                          recluster=False)
            run_matching()
            messages.success(request, f"已记录 {len(plan)} 条决定并重新聚类。")
            groups = selectors.review_groups()
            return redirect("matching:group_detail", key=groups[0].key) if groups \
                else redirect("matching:group_queue")
    return render(request, "matching/group_detail.html", {
        "group": group, "error": error, "reason_labels": REASON_LABELS,
        "field_labels": FIELD_LABELS,
    })


def review_queue(request):
    status = request.GET.get("status", "open")
    reason = request.GET.get("reason", "")
    qs = MatchCandidate.objects.select_related(
        "item_a__supplier", "item_b__supplier", "item_a__product", "item_b__product")
    if status == "open":
        qs = qs.filter(status__in=OPEN)
    elif status:
        qs = qs.filter(status=status)
    if reason:
        qs = qs.filter(reasons__contains=[reason])
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "matching/review_queue.html", {
        "page": page, "status": status, "reason": reason, "reason_labels": REASON_LABELS,
        "statuses": MatchCandidate.Status.choices,
    })


@require_http_methods(["GET", "POST"])
def candidate_detail(request, pk):
    cand = get_object_or_404(MatchCandidate.objects.select_related(
        "item_a__supplier", "item_a__product", "item_a__current_record",
        "item_b__supplier", "item_b__product", "item_b__current_record"), pk=pk)
    form = CandidateDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.decide_candidate(cand, form.cleaned_data["action"], user=request.user,
                                      note=form.cleaned_data["note"])
        except ValidationError as exc:
            form.add_error("note", exc)
        else:
            messages.success(request, f"候选 #{cand.pk} 已记录决定并重新聚类。")
            return redirect("matching:group_queue")
    rows = []
    for fld in ("oe", "category", "fitment", "dims", "position", "name"):
        c = cand.comparisons.get(fld)
        if c:
            rows.append({"field": FIELD_LABELS.get(fld, fld), **c})
    return render(request, "matching/candidate_detail.html", {
        "cand": cand, "form": form, "rows": rows,
        "reasons": [(r, REASON_LABELS.get(r, r)) for r in cand.reasons],
        "missing": [FIELD_LABELS.get(m, m) for m in cand.missing],
        "decisions": cand.decisions.select_related("actor"),
        "hard": [c for c in cand.conflicts if c.get("hard")],
    })


def issue_list(request):
    code = request.GET.get("code", "")
    status = request.GET.get("status", Issue.Status.OPEN)
    qs = Issue.objects.select_related("item__supplier", "item__product", "source_record", "batch")
    if status:
        qs = qs.filter(status=status)
    if code:
        qs = qs.filter(code=code)
    codes = Issue.objects.filter(status=Issue.Status.OPEN).values_list("code", flat=True)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "matching/issue_list.html", {
        "page": page, "code": code, "status": status, "codes": sorted(set(codes)),
        "statuses": Issue.Status.choices,
    })


@require_POST
def issue_decide(request, pk):
    issue = get_object_or_404(Issue, pk=pk)
    form = IssueDecisionForm(request.POST)
    if form.is_valid():
        services.decide_issue(issue, form.cleaned_data["action"], user=request.user,
                              note=form.cleaned_data["note"])
        messages.success(request, f"异常 #{issue.pk} 已标记为 {issue.get_status_display()}。")
    else:
        messages.error(request, "提交无效")
    return redirect(request.POST.get("next") or "matching:issue_list")
