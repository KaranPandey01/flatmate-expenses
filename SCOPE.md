# SCOPE.md — Anomaly Log & Database Schema

## Anomaly log (from expenses_export.csv, 42 data rows)

| Row | Issue | Type | Policy |
|---|---|---|---|
| 5,6 | "Dinner at Marina Bites" / "dinner - marina bites" — same payer/amount/date | Duplicate | Keep first as confirmed, second flagged `pending_review` — not auto-deleted (Meera's requirement) |
| 6,19-27 | "Dev" listed in split_with on trip rows | Initially misclassified as non-member | Dev modeled as a time-boxed member (trip window only), not a resident — corrected after first test run |
| 9 | `paid_by = "priya"` (lowercase) | Name variant | Normalized via alias table, auto-resolved |
| 10 | Amount `899.995` | Sub-paisa precision | Rounded half-up to 2dp, auto-resolved |
| 11 | `paid_by = "Priya S"` | Name variant | Normalized, auto-resolved |
| 13 | `paid_by` missing entirely | Missing required field | Expense created with `paid_by=NULL`, `status=pending_review`, excluded from balances until a human assigns a payer |
| 14 | "Rohan paid Aisha back" ₹5000, single recipient in split_with | Settlement logged as expense | Reclassified as a `Settlement` record, not an `Expense` — auto-resolved |
| 15, 32 | Pizza Friday / Weekend brunch: percentages sum to 110% | Invalid split math | Proportionally rescaled to sum to 100%, preserving relative weights, auto-resolved |
| 20,21,23,26 | USD amounts (villa, lunch, parasailing, refund) | Currency mismatch | Converted to INR at a fixed documented rate (₹83/USD), not a live rate — keeps balances reproducible |
| 23 | "Dev's friend Kabir" in split_with | Non-member guest | Excluded from the computed split entirely — cost absorbed among tracked members (see DECISIONS.md) |
| 26 | Parasailing refund: `-30 USD` | Negative amount | Treated as a legitimate refund (per the note), not an error — reduces the relevant splits naturally |
| 27 | `paid_by = "rohan "` (trailing space) | Name variant | Normalized, auto-resolved |
| 27 | Date = `2014-03-01` | Invalid date (wrong year, not a format issue) | NOT auto-corrected — flagged `pending_review`, excluded from balances until a human supplies the right date |
| 28 | Groceries DMart, currency blank | Missing field | Defaulted to INR (dataset is INR-dominant, no other currency signal on this row), auto-resolved |
| 31 | Dinner order Swiggy, amount = 0 | Zero-value row, self-flagged as "counted twice earlier" | Recorded but `pending_review` — a human should confirm delete vs keep |
| 36 | Groceries BigBasket (Apr): Meera in split_with | Stale membership (she left end of March) | Excluded from computed split — membership window is source of truth over the raw column, auto-resolved |
| 38 | Sam deposit share: single recipient (Aisha), before Sam is a confirmed resident | Misclassified as expense | Reclassified as a `Settlement` (Sam → Aisha), same detection rule as row 14 |
| 42 | Furniture: `split_type=equal` but `split_details` populated with explicit (uniform) shares | Metadata contradiction | Shares are uniform, so outcome is unaffected — auto-resolved, logged for transparency |
| 8 (Movie night snacks) | Meera intentionally excluded, noted "Meera skipped" | **Not treated as an anomaly** | Deliberately left alone — false-positive guard, since this is intentional per the notes field |

**21 total anomalies logged, 5 rows left `pending_review`** on the last run (see `/import-report/`).

## Database schema (relational, SQLite locally / Postgres in prod)

- `Person` — canonical identity, optional link to Django `User` for login
- `Group` — the flat
- `Membership` — (group, person, joined_at, left_at) — time-boxed, so membership can change over time and old expenses still resolve correctly against who was actually present on that date
- `Expense` — one row per CSV expense row, with `status` (confirmed/pending_review/rejected), `source_row` for traceability, `amount` + `currency` + `amount_inr` (converted)
- `Split` — one row per (expense, person), the atomic unit balances are computed from
- `Settlement` — direct payments between two people, separate from `Expense`
- `ImportBatch` — one row per CSV import run
- `AnomalyLog` — one row per detected issue, linked to its source row and (if applicable) the resulting Expense/Settlement — the persistent anomaly ledger

## Known limitation
Net balances do not sum to exactly zero while `pending_review` rows remain unresolved — this is intentional (those rows are fully excluded from both sides of the ledger until approved), not a rounding bug.
