# DECISIONS.md — Decision Log

## 1. Persistent Anomaly Ledger instead of a one-time import report
**Options considered:** (a) print a report and discard it, (b) write a report file, (c) a DB table linked to source rows and downstream records.
**Chose (c).** Meera wants to approve anything changed; that only works if anomalies are queryable, filterable by status, and linked to what they produced. A throwaway report can't support an approval workflow.

## 2. Time-boxed Membership instead of a static group member list
**Options considered:** (a) a flat list of "current members" on the Group, (b) a join/leave timestamp pair per person, (c) full membership history with intervals.
**Chose (c).** Directly answers Sam's complaint and the Meera stale-membership anomaly: an expense's split is computed from who was active *on that expense's date*, not who's active *now*. This one modeling decision resolves two separate anomalies for free.

## 3. Dev modeled as a time-boxed member (trip window only), not a resident, not a guest
**Options considered:** (a) no Person/Membership at all — treat every Dev row as a special case, (b) full resident membership, (c) a Membership row scoped to just the trip dates.
**Chose (c).** Tried (a) first — it wrongly excluded Dev from every trip expense he legitimately paid for, since the importer's non-member exclusion rule doesn't distinguish "resident" from "real participant." Caught this by inspecting the anomaly log after the first test run (see AI_USAGE.md) and fixed by giving Dev a membership window covering exactly the Goa trip.

## 4. Kabir (Dev's one-off guest) is excluded from the split entirely, not billed
**Options considered:** (a) create a Person for Kabir and track his debt, (b) exclude him and absorb his share among tracked members.
**Chose (b).** The app has no way to actually collect from someone who was never a flatmate and appears exactly once. Splitting his share among the people who can actually be billed is the more useful default; this is explicitly flagged in the anomaly log so it's a visible, reversible decision rather than a silent one.

## 5. Settlement detection: single-recipient rule
**Options considered:** (a) rely on a `notes` keyword like "settlement", (b) rely on `split_type` being blank, (c) a structural rule: split_with names exactly one person who isn't the payer.
**Chose (c).** Both row 14 (Rohan→Aisha) and row 38 (Sam→Aisha) fit this single structural pattern even though their raw `split_type` fields differ ("" vs "equal") — a structural rule generalizes better than relying on notes text, which won't always be present.

## 6. Fixed USD→INR rate (₹83), not a live exchange rate
**Options considered:** (a) fetch live FX rate at import time, (b) fixed documented constant.
**Chose (b).** A live rate makes re-running the same import non-reproducible — two imports of the same file would silently produce different balances. A fixed, documented rate trades a small amount of real-world accuracy for reproducibility, which matters more for a shared ledger people are trusting.

## 7. Invalid dates are never auto-corrected
**Options considered:** (a) guess the intended date (e.g. assume typo, use nearby date), (b) flag and exclude from balances until resolved.
**Chose (b).** The airport cab row's date (`2014-03-01`) is off by exactly 12 years from the surrounding trip dates — an obvious guess, but still a guess involving money. Per the assignment's own framing ("a silent guess is a failing answer"), this stays `pending_review`.

## 8. Percentage splits that don't sum to 100% are rescaled proportionally, not rejected
**Options considered:** (a) reject the row, (b) assume the last person's percentage is wrong, (c) proportionally rescale all percentages to sum to 100%.
**Chose (c).** Preserves the relative weighting the flatmates actually intended (e.g. Meera consistently gets a smaller share at 20% vs 30%) without picking a single "wrong" person's number to override.

## 9. Commit history structured by logical layer, not literal chronological order
Given the compressed build timeline, commits are grouped by dependency layer (config → models → admin → importer → balances → views → deploy config) rather than literal minute-by-minute history. Each commit is independently reviewable and buildable on top of the previous one.
