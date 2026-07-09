"""
Balance computation. Two views on the same underlying data:
  - individual_balance(person): every Split + Settlement that touches them,
    with a running net -- this is Rohan's "no magic numbers" requirement.
  - simplified_debts(group): a minimal set of payments (greedy debt
    simplification) that settles everyone up -- this is Aisha's "one number
    per person, done" requirement.
Both are derived from the same Split/Settlement rows, so they can never
disagree with each other.
"""
from decimal import Decimal
from collections import defaultdict
from .models import Person, Split, Settlement, Expense


def individual_balance(person: Person, group=None):
    """
    Returns (net, breakdown) where positive net = others owe this person,
    negative = this person owes others. breakdown lists every contributing
    row with a trace back to the source Expense/Settlement.
    """
    breakdown = []
    net = Decimal("0")

    paid_qs = Expense.objects.filter(paid_by=person, status="confirmed")
    if group:
        paid_qs = paid_qs.filter(group=group)
    for e in paid_qs:
        total_owed_by_others = sum(
            s.amount_owed_inr for s in e.splits.exclude(person=person)
        )
        if total_owed_by_others:
            net += total_owed_by_others
            breakdown.append({
                "type": "paid_expense", "expense_id": e.id, "row": e.source_row,
                "description": e.description, "amount": str(total_owed_by_others),
                "detail": f"{person.name} paid; others' share of this expense",
            })

    owed_qs = Split.objects.filter(person=person, expense__status="confirmed").exclude(expense__paid_by=person)
    if group:
        owed_qs = owed_qs.filter(expense__group=group)
    for s in owed_qs:
        net -= s.amount_owed_inr
        breakdown.append({
            "type": "owes_expense", "expense_id": s.expense_id, "row": s.expense.source_row,
            "description": s.expense.description, "amount": str(-s.amount_owed_inr),
            "detail": f"{person.name}'s share, paid by {s.expense.paid_by}",
        })

    settle_qs = Settlement.objects.filter(paid_by=person)
    if group:
        settle_qs = settle_qs.filter(group=group)
    for s in settle_qs:
        net += s.amount_inr  # paying someone back reduces what you owe -> increases your net
        breakdown.append({
            "type": "settlement_paid", "row": s.source_row,
            "description": f"Paid {s.paid_to}", "amount": str(s.amount_inr), "detail": "settlement",
        })

    settle_recv_qs = Settlement.objects.filter(paid_to=person)
    if group:
        settle_recv_qs = settle_recv_qs.filter(group=group)
    for s in settle_recv_qs:
        net -= s.amount_inr
        breakdown.append({
            "type": "settlement_received", "row": s.source_row,
            "description": f"Received from {s.paid_by}", "amount": str(-s.amount_inr), "detail": "settlement",
        })

    return net, breakdown


def all_net_balances(group):
    people = Person.objects.filter(memberships__group=group).distinct()
    return {p: individual_balance(p, group)[0] for p in people}


def simplified_debts(group):
    """Greedy min-cash-flow settlement: fewest payments that zero everyone out."""
    nets = all_net_balances(group)
    creditors = sorted([(p, n) for p, n in nets.items() if n > 0], key=lambda x: -x[1])
    debtors = sorted([(p, n) for p, n in nets.items() if n < 0], key=lambda x: x[1])
    creditors = [[p, n] for p, n in creditors]
    debtors = [[p, n] for p, n in debtors]

    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor, d_amt = debtors[i]
        creditor, c_amt = creditors[j]
        pay = min(-d_amt, c_amt)
        if pay > 0:
            transactions.append((debtor, creditor, pay))
        debtors[i][1] += pay
        creditors[j][1] -= pay
        if abs(debtors[i][1]) < Decimal("0.01"):
            i += 1
        if abs(creditors[j][1]) < Decimal("0.01"):
            j += 1
    return transactions
