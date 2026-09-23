from django import forms

from .models import ReviewDecision


class CandidateDecisionForm(forms.Form):
    action = forms.ChoiceField(label="决定", choices=[
        (ReviewDecision.Action.MERGE, "确认合并（是同一产品）"),
        (ReviewDecision.Action.REJECT, "不是同一产品"),
        (ReviewDecision.Action.NEEDS_INFO, "待补充资料"),
    ], widget=forms.RadioSelect)
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class IssueDecisionForm(forms.Form):
    action = forms.ChoiceField(choices=[
        (ReviewDecision.Action.RESOLVE, "已解决"), (ReviewDecision.Action.IGNORE, "忽略"),
    ])
    note = forms.CharField(required=False, max_length=500)
