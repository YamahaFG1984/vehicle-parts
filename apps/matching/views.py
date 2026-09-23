from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .forms import CandidateDecisionForm, IssueDecisionForm
from .models import Issue, MatchCandidate
from .rules import FIELD_LABELS, REASON_LABELS

OPEN = [MatchCandidate.Status.PENDING, MatchCandidate.Status.NEEDS_INFO]


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
            nxt = MatchCandidate.objects.filter(status__in=OPEN).first()
            return redirect(nxt or "matching:review_queue")
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
