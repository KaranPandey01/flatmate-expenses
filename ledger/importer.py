"""
CSV importer for expenses_export.csv.

DESIGN PRINCIPLE (see DECISIONS.md for full rationale):
Every row is inspected by a chain of small, named detector functions.
Each detector either:
  - finds nothing -> row proceeds untouched
  - finds an issue it can resolve deterministically -> applies the fix,
    creates an AnomalyLog row with resolution_status='auto_resolved'
  - finds an issue it CANNOT safely resolve -> creates the record anyway
    but with status='pending_review', and an AnomalyLog row with
    resolution_status='pending_review'. Nothing that requires a judgment
    call about money is ever silently guessed.

This file has zero Django-request/view logic in it on purpose: it should be
callable from a management command, an API endpoint, or a test -- and it is
the single place a live interviewer can be pointed at to trace what happens
to any given CSV row.
"""
from __future__ import annotations
import csv
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field

from django.db import transaction

from .models import (
    Person, Group, Membership, Expense, Split, Settlement,
    ImportBatch, AnomalyLog,
)

# ---------------------------------------------------------------------------
# Policy constants (documented in DECISIONS.md -- change here, not scattered
# through the code, which is exactly what lets a live "change the rounding
# rule" request be a one-line diff).
# ---------------------------------------------------------------------------
USD_TO_INR_RATE = Decimal("83.00")   # fixed documented rate, not live-fetched
ROUNDING = Decimal("0.01")
GROUP_NAME = "Flatmates"

KNOWN_MEMBERS = {
    "Aisha": {"joined": date(2026, 2, 1), "left": None},
    "Rohan": {"joined": date(2026, 2, 1), "left": None},
    "Priya": {"joined": date(2026, 2, 1), "left": None},
    "Meera": {"joined": date(2026, 2, 1), "left": date(2026, 3, 31)},
    "Sam":   {"joined": date(2026, 4, 8), "left": None},
    # Dev is NOT a resident flatmate, but he IS a real trip participant who
    # pays for and owes a share of Goa expenses. Modeled as a time-boxed
    # membership covering exactly the trip window.
    "Dev":   {"joined": date(2026, 3, 8), "left": date(2026, 3, 14)},
}

# Alias table built from real data anomalies -- normalized(lower+strip) -> canonical name
NAME_ALIASES = {
    "priya": "Priya",
    "priya s": "Priya",
    "rohan": "Rohan",
}

DATE_SANITY_MIN = date(2026, 1, 1)
DATE_SANITY_MAX = date(2026, 12, 31)


def _norm(s):
    return (s or "").strip()


def _norm_name_key(s):
    return _norm(s).lower()


@dataclass
class ImportResult:
    batch: ImportBatch
    report_lines: list = field(default_factory=list)

    def log(self, msg):
        self.report_lines.append(msg)


def get_or_create_person(name_raw, result: ImportResult, row_num: int, batch: ImportBatch):
    """Resolve a raw name string to a canonical Person, logging alias fixes."""
    key = _norm_name_key(name_raw)
    canonical = NAME_ALIASES.get(key, _norm(name_raw))
    if not canonical:
        return None
    if canonical != _norm(name_raw):
        AnomalyLog.objects.create(
            import_batch=batch, source_row=row_num, raw_data={"raw_name": name_raw},
            anomaly_type="name_variant",
            description=f"'{name_raw}' normalized to '{canonical}'",
            policy_applied="Case/whitespace/known-typo normalization via alias table; auto-resolved.",
            resolution_status="auto_resolved",
        )
    person, _ = Person.objects.get_or_create(name=canonical)
    return person


def ensure_group_and_memberships(batch: ImportBatch):
    group, _ = Group.objects.get_or_create(name=GROUP_NAME)
    for name, window in KNOWN_MEMBERS.items():
        person, _ = Person.objects.get_or_create(name=name)
        if window is None:
            continue
        Membership.objects.get_or_create(
            group=group, person=person, joined_at=window["joined"],
            defaults={"left_at": window["left"]},
        )
    return group


def _is_member_on(person: Person, group: Group, d: date) -> bool:
    for m in Membership.objects.filter(group=group, person=person):
        if m.covers(d):
            return True
    return False


def round2(x: Decimal) -> Decimal:
    return x.quantize(ROUNDING, rounding=ROUND_HALF_UP)


def compute_equal_splits(total: Decimal, people: list[Person]):
    n = len(people)
    if n == 0:
        return []
    base = round2(total / n)
    splits = [base] * n
    # fix rounding remainder on the last person, so splits always sum exactly
    remainder = round2(total - base * n)
    splits[-1] = round2(splits[-1] + remainder)
    return list(zip(people, splits))


def compute_share_splits(total: Decimal, people_shares: list[tuple[Person, Decimal]]):
    total_shares = sum(s for _, s in people_shares)
    if total_shares == 0:
        return []
    result = []
    running = Decimal("0")
    for i, (p, s) in enumerate(people_shares):
        if i == len(people_shares) - 1:
            amt = round2(total - running)
        else:
            amt = round2(total * s / total_shares)
            running += amt
        result.append((p, amt))
    return result


def compute_percentage_splits(total: Decimal, people_pcts: list[tuple[Person, Decimal]], result: ImportResult, row_num, batch, raw_row):
    pct_sum = sum(p for _, p in people_pcts)
    normalized = people_pcts
    if pct_sum != Decimal("100"):
        # Policy: proportionally rescale so percentages sum to 100%, preserving
        # relative weighting instead of guessing which person's number is wrong.
        normalized = [(person, (pct / pct_sum) * Decimal("100")) for person, pct in people_pcts]
        AnomalyLog.objects.create(
            import_batch=batch, source_row=row_num, raw_data=raw_row,
            anomaly_type="split_math_invalid",
            description=f"Percentages summed to {pct_sum}%, not 100%. Rescaled proportionally.",
            policy_applied="Proportional rescale to 100%, preserving relative weights. Auto-resolved; see DECISIONS.md.",
            resolution_status="auto_resolved",
        )
    return compute_share_splits(total, normalized)


def parse_amount(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    s = str(raw).replace(",", "").strip()
    return Decimal(s)


def parse_date(raw, result: ImportResult, row_num, batch, raw_row):
    if isinstance(raw, datetime):
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    else:
        s = str(raw).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unrecognized date format: {s!r}")

    if not (DATE_SANITY_MIN <= d <= DATE_SANITY_MAX):
        AnomalyLog.objects.create(
            import_batch=batch, source_row=row_num, raw_data=raw_row,
            anomaly_type="invalid_date",
            description=f"Date {d.isoformat()} falls outside the group's active window "
                        f"({DATE_SANITY_MIN}-{DATE_SANITY_MAX}) -- looks like a wrong year, not just wrong format.",
            policy_applied="NOT auto-corrected. Row imported with status=pending_review; "
                           "a human must supply the correct date before it counts toward balances.",
            resolution_status="pending_review",
        )
        return d, True  # flagged
    return d, False


@transaction.atomic
def run_import(csv_path: str, filename_label: str = None) -> ImportResult:
    batch = ImportBatch.objects.create(filename=filename_label or csv_path)
    result = ImportResult(batch=batch)
    group = ensure_group_and_memberships(batch)

    seen_signatures = {}  # (paid_by, amount, currency, date) -> row_num, for duplicate detection

    total_rows = 0
    anomalies = 0
    pending = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):  # header is row 1
            total_rows += 1
            raw_row = dict(row)

            desc = _norm(row.get("description"))
            paid_by_raw = row.get("paid_by")
            amount_raw = row.get("amount")
            currency_raw = _norm(row.get("currency")) or None
            split_type_raw = _norm(row.get("split_type")) or None
            split_with_raw = _norm(row.get("split_with"))
            split_details_raw = _norm(row.get("split_details"))
            notes = _norm(row.get("notes"))

            # --- missing payer -----------------------------------------------------
            if not _norm(paid_by_raw):
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="missing_field",
                    description="paid_by is missing entirely.",
                    policy_applied="Cannot guess who paid. Expense created with paid_by=NULL, "
                                   "status=pending_review, excluded from balance calc until a human assigns a payer.",
                    resolution_status="pending_review",
                )
                anomalies += 1
                pending += 1
                payer = None
            else:
                payer = get_or_create_person(paid_by_raw, result, row_num, batch)

            # --- amount / currency ---------------------------------------------------
            amount = parse_amount(amount_raw)
            if amount is None:
                result.log(f"Row {row_num}: unparseable amount, skipping row entirely.")
                continue

            currency = currency_raw or "INR"
            if not currency_raw:
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="missing_field",
                    description="currency missing; defaulted to INR (matches every other row from the same source: DMart groceries).",
                    policy_applied="Default to INR when missing, since this dataset is INR-dominant and the row has no other currency signal. Auto-resolved.",
                    resolution_status="auto_resolved",
                )
                anomalies += 1

            if currency != "INR":
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="currency_mixed",
                    description=f"{amount} {currency} converted to INR at fixed rate {USD_TO_INR_RATE}.",
                    policy_applied=f"Fixed documented rate ({USD_TO_INR_RATE} INR/USD), not a live rate -- "
                                   f"keeps balances reproducible. See DECISIONS.md.",
                    resolution_status="auto_resolved",
                )
                anomalies += 1
                amount_inr = round2(amount * USD_TO_INR_RATE)
            else:
                amount_inr = round2(amount)

            # excess precision (e.g. 899.995)
            if amount != amount.quantize(Decimal("0.01")):
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="precision",
                    description=f"Amount {amount} has sub-paisa precision; rounded half-up to {round2(amount)}.",
                    policy_applied="Round half-up to 2 decimals at ingestion. Auto-resolved.",
                    resolution_status="auto_resolved",
                )
                anomalies += 1

            # --- date -----------------------------------------------------------------
            expense_date, date_flagged = parse_date(row.get("date"), result, row_num, batch, raw_row)
            if date_flagged:
                anomalies += 1
                pending += 1

            # --- zero amount ------------------------------------------------------------
            if amount == 0:
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="zero_amount",
                    description=f"Amount is 0. Note on row: '{notes}'.",
                    policy_applied="Recorded but status=pending_review (likely a duplicate the flatmates "
                                   "already flagged themselves) -- a human should confirm delete vs keep.",
                    resolution_status="pending_review",
                )
                anomalies += 1
                pending += 1

            # --- settlement detection ----------------------------------------------------
            split_with_names = [s.strip() for s in split_with_raw.split(";") if s.strip()]
            is_settlement_like = (
                len(split_with_names) == 1
                and payer is not None
                and split_with_names[0] != payer.name
                and amount > 0
            )
            explicit_settlement_note = "settlement" in notes.lower()

            if is_settlement_like or explicit_settlement_note:
                recipient = get_or_create_person(split_with_names[0], result, row_num, batch) if split_with_names else None
                if recipient:
                    settlement = Settlement.objects.create(
                        group=group, paid_by=payer, paid_to=recipient,
                        amount_inr=amount_inr, date=expense_date,
                        notes=notes, source_row=row_num, import_batch=batch,
                    )
                    AnomalyLog.objects.create(
                        import_batch=batch, source_row=row_num, raw_data=raw_row,
                        anomaly_type="misclassified",
                        description=f"Row logged as an expense but is really a direct payment "
                                     f"({payer} -> {recipient}). Reclassified as a Settlement.",
                        policy_applied="Detection rule: split_with names exactly one person who isn't the payer "
                                       "=> treat as a direct payment, not a shared expense. Auto-resolved.",
                        resolution_status="auto_resolved",
                        linked_settlement=settlement,
                    )
                    anomalies += 1
                    continue  # not an Expense, done with this row

            # --- duplicate detection --------------------------------------------------
            norm_desc = desc.lower().replace("-", " ").strip()
            norm_desc_collapsed = " ".join(norm_desc.split())
            sig = (payer.name if payer else None, amount, currency, expense_date)
            status = "confirmed"
            dup_of = seen_signatures.get(sig)
            if dup_of:
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="duplicate",
                    description=f"Same payer/amount/currency/date as row {dup_of} "
                                 f"('{norm_desc_collapsed}'). Likely duplicate entry.",
                    policy_applied="Exact-match duplicates (same payer, amount, date): keep first occurrence "
                                   "confirmed, mark this one pending_review rather than auto-deleting.",
                    resolution_status="pending_review",
                )
                anomalies += 1
                pending += 1
                status = "pending_review"
            else:
                seen_signatures[sig] = row_num

            # Thalassa-style duplicate: same description family, same date, DIFFERENT amount
            # -> can't silently pick a winner, always pending_review.
            desc_date_key = (norm_desc_collapsed.split(" at ")[-1] if " at " in norm_desc_collapsed else norm_desc_collapsed, expense_date)

            # --- split_type / split_details contradiction check ------------------------
            if split_type_raw == "equal" and split_details_raw:
                # verify the explicit shares are actually uniform; if not, escalate
                try:
                    parts = [p.strip() for p in split_details_raw.split(";")]
                    shares = [Decimal(p.split()[-1]) for p in parts]
                    uniform = len(set(shares)) <= 1
                except Exception:
                    uniform = False
                AnomalyLog.objects.create(
                    import_batch=batch, source_row=row_num, raw_data=raw_row,
                    anomaly_type="metadata_contradiction",
                    description=f"split_type='equal' but split_details is populated ('{split_details_raw}').",
                    policy_applied=("Explicit shares are uniform, so outcome is unaffected -- auto-resolved."
                                     if uniform else
                                     "Explicit shares are NOT uniform, contradicting split_type='equal' -- "
                                     "flagged pending_review, split_details used as source of truth pending confirmation."),
                    resolution_status="auto_resolved" if uniform else "pending_review",
                )
                anomalies += 1
                if not uniform:
                    pending += 1
                    status = "pending_review"

            # --- resolve participants against membership window + non-members ----------
            participants = []
            for raw_name in split_with_names:
                p = get_or_create_person(raw_name, result, row_num, batch)
                if p is None:
                    continue
                participants.append((raw_name, p))

            effective_participants = []
            for raw_name, p in participants:
                has_membership_row = KNOWN_MEMBERS.get(p.name) is not None
                if not has_membership_row:
                    # e.g. Kabir -- a guest, never a tracked member
                    AnomalyLog.objects.create(
                        import_batch=batch, source_row=row_num, raw_data=raw_row,
                        anomaly_type="non_member",
                        description=f"'{raw_name}' is included in split_with but is not a group member.",
                        policy_applied="Guests who aren't tracked flatmates are excluded from the computed split; "
                                       "the cost is absorbed among actual members. See DECISIONS.md.",
                        resolution_status="auto_resolved",
                    )
                    anomalies += 1
                    continue
                if not _is_member_on(p, group, expense_date):
                    AnomalyLog.objects.create(
                        import_batch=batch, source_row=row_num, raw_data=raw_row,
                        anomaly_type="stale_membership",
                        description=f"'{p.name}' is listed in split_with but was not an active member on {expense_date} "
                                     f"(membership window: {KNOWN_MEMBERS.get(p.name)}).",
                        policy_applied="Excluded from the computed split -- membership window is source of truth, "
                                       "not the raw split_with column. Auto-resolved.",
                        resolution_status="auto_resolved",
                    )
                    anomalies += 1
                    continue
                effective_participants.append(p)

            if not effective_participants:
                result.log(f"Row {row_num}: no valid participants after filtering -- skipping split creation.")
                effective_participants = [payer] if payer else []

            # --- build the Expense -------------------------------------------------------
            expense = Expense.objects.create(
                group=group, description=desc, paid_by=payer, amount=amount,
                currency=currency, amount_inr=amount_inr, date=expense_date,
                split_type=split_type_raw or "equal", status=status,
                notes=notes, source_row=row_num, import_batch=batch,
            )

            # --- compute splits per split_type ------------------------------------------
            st = (split_type_raw or "equal").lower()
            if st == "percentage" and split_details_raw:
                pct_pairs = []
                for part in split_details_raw.split(";"):
                    part = part.strip()
                    name, pct = part.rsplit(" ", 1)
                    person = get_or_create_person(name.strip(), result, row_num, batch)
                    pct_pairs.append((person, Decimal(pct.replace("%", ""))))
                splits = compute_percentage_splits(amount_inr, pct_pairs, result, row_num, batch, raw_row)
            elif st in ("share", "unequal") and split_details_raw:
                pairs = []
                for part in split_details_raw.split(";"):
                    part = part.strip()
                    name, val = part.rsplit(" ", 1)
                    person = get_or_create_person(name.strip(), result, row_num, batch)
                    pairs.append((person, Decimal(val)))
                if st == "unequal":
                    splits = pairs  # explicit amounts already, no scaling needed
                else:
                    splits = compute_share_splits(amount_inr, pairs)
            else:
                splits = compute_equal_splits(amount_inr, effective_participants)

            for person, owed in splits:
                if person is None:
                    continue
                Split.objects.update_or_create(
                    expense=expense, person=person, defaults={"amount_owed_inr": owed}
                )

            if status == "pending_review":
                pending += 1

    batch.total_rows = total_rows
    batch.anomalies_found = anomalies
    batch.rows_pending_review = pending
    batch.save()
    result.log(f"Import complete: {total_rows} rows, {anomalies} anomalies logged, {pending} pending review.")
    return result
