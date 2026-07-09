from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from .models import Group, Person, AnomalyLog, ImportBatch
from .balances import all_net_balances, simplified_debts, individual_balance

@login_required
def dashboard(request):
    group = Group.objects.first()
    nets = all_net_balances(group) if group else {}
    settlements = simplified_debts(group) if group else []
    return render(request, "ledger/dashboard.html", {
        "group": group,
        "nets": sorted(nets.items(), key=lambda x: -x[1]),
        "settlements": settlements,
    })

@login_required
def person_detail(request, person_id):
    person = Person.objects.get(id=person_id)
    net, breakdown = individual_balance(person)
    return render(request, "ledger/person_detail.html", {
        "person": person, "net": net, "breakdown": breakdown,
    })

@login_required
def import_report(request):
    batch = ImportBatch.objects.order_by("-imported_at").first()
    anomalies = batch.anomalies.all().order_by("source_row") if batch else []
    return render(request, "ledger/import_report.html", {"batch": batch, "anomalies": anomalies})
