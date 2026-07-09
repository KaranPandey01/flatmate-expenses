from django.db import models
from django.contrib.auth.models import User


class Person(models.Model):
    """
    A flatmate/participant. Separate from Django's auth User because not every
    person in the CSV necessarily has (or needs) a login -- but a Person CAN be
    linked to a User once onboarded.

    `aliases` exists because the source data has the same human appearing as
    'Priya', 'priya', and 'Priya S'. We resolve those to one canonical Person
    at import time (see ledger/importer.py) rather than silently merging --
    every alias resolution is itself logged as an anomaly.
    """
    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.name


class Group(models.Model):
    name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    """
    Time-bounded group membership. This answers Sam's complaint ("why would
    March electricity affect my balance?") and the Meera stale-membership
    anomaly: an expense only splits across people whose membership window
    (joined_at <= expense.date <= left_at_or_open) covers that expense's
    date -- regardless of what the raw split_with column says.
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='memberships')
    joined_at = models.DateField()
    left_at = models.DateField(null=True, blank=True)  # null = still active

    class Meta:
        unique_together = ('group', 'person', 'joined_at')

    def covers(self, date):
        if self.left_at is None:
            return self.joined_at <= date
        return self.joined_at <= date <= self.left_at

    def __str__(self):
        return f"{self.person} in {self.group} ({self.joined_at} - {self.left_at or 'active'})"


SPLIT_TYPE_CHOICES = [
    ('equal', 'Equal'),
    ('unequal', 'Unequal (explicit amounts)'),
    ('percentage', 'Percentage'),
    ('share', 'Share-based (ratios)'),
]

EXPENSE_STATUS_CHOICES = [
    ('confirmed', 'Confirmed'),
    ('pending_review', 'Pending Review'),  # Meera's requirement: nothing auto-applies silently
    ('rejected', 'Rejected'),
]


class Expense(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255)
    paid_by = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='expenses_paid', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    amount_inr = models.DecimalField(max_digits=12, decimal_places=2, help_text="Amount converted to INR at a documented fixed rate (see DECISIONS.md)")
    date = models.DateField()
    split_type = models.CharField(max_length=20, choices=SPLIT_TYPE_CHOICES, default='equal')
    status = models.CharField(max_length=20, choices=EXPENSE_STATUS_CHOICES, default='confirmed')
    notes = models.TextField(blank=True)
    source_row = models.IntegerField(null=True, blank=True, help_text="Original CSV row number, for traceability")
    import_batch = models.ForeignKey('ImportBatch', null=True, blank=True, on_delete=models.SET_NULL, related_name='expenses')

    def __str__(self):
        return f"{self.description} ({self.amount} {self.currency})"


class Split(models.Model):
    """
    One row per (expense, person) -- this is what makes Rohan's 'no magic
    numbers' requirement possible: his balance is always just
    sum(Split.amount_owed_inr) filtered by person, and every row traces back
    to one Expense.
    """
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='splits')
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='splits')
    amount_owed_inr = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = ('expense', 'person')


class Settlement(models.Model):
    """A direct payment between two people that resolves debt -- NOT an expense."""
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='settlements')
    paid_by = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='settlements_paid')
    paid_to = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='settlements_received')
    amount_inr = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    notes = models.TextField(blank=True)
    source_row = models.IntegerField(null=True, blank=True)
    import_batch = models.ForeignKey('ImportBatch', null=True, blank=True, on_delete=models.SET_NULL, related_name='settlements')

    def __str__(self):
        return f"{self.paid_by} -> {self.paid_to}: {self.amount_inr}"


class ImportBatch(models.Model):
    filename = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)
    total_rows = models.IntegerField(default=0)
    anomalies_found = models.IntegerField(default=0)
    rows_pending_review = models.IntegerField(default=0)

    def __str__(self):
        return f"Import {self.filename} @ {self.imported_at:%Y-%m-%d %H:%M}"


ANOMALY_TYPES = [
    ('duplicate', 'Duplicate entry'),
    ('missing_field', 'Missing required field'),
    ('invalid_date', 'Invalid or wrong date'),
    ('ambiguous_date', 'Ambiguous date format'),
    ('name_variant', 'Name casing/typo/alias'),
    ('precision', 'Excess decimal precision'),
    ('split_math_invalid', 'Split percentages/shares do not resolve correctly'),
    ('misclassified', 'Settlement logged as expense (or vice versa)'),
    ('stale_membership', 'Includes a person outside their membership window'),
    ('non_member', 'Includes a person not in the group at all'),
    ('currency_mixed', 'Non-INR currency needing conversion'),
    ('negative_amount', 'Negative amount (possible refund)'),
    ('zero_amount', 'Zero-value row'),
    ('metadata_contradiction', 'split_type contradicts split_details'),
    ('other', 'Other'),
]

RESOLUTION_STATUS = [
    ('auto_resolved', 'Auto-resolved by policy'),
    ('pending_review', 'Pending human approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


class AnomalyLog(models.Model):
    """
    The persistent, queryable anomaly ledger -- the core differentiator of
    this submission. Every detected issue lives here, tied to its source row,
    with the policy applied and whether it's still awaiting human sign-off
    (Meera's requirement).
    """
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='anomalies')
    source_row = models.IntegerField()
    raw_data = models.JSONField()
    anomaly_type = models.CharField(max_length=30, choices=ANOMALY_TYPES)
    description = models.TextField()
    policy_applied = models.TextField(help_text="Which documented policy handled this, and what it did")
    resolution_status = models.CharField(max_length=20, choices=RESOLUTION_STATUS, default='pending_review')
    linked_expense = models.ForeignKey(Expense, null=True, blank=True, on_delete=models.SET_NULL)
    linked_settlement = models.ForeignKey(Settlement, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"[{self.anomaly_type}] row {self.source_row}: {self.description[:60]}"
