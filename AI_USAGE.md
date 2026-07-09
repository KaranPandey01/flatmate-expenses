# AI_USAGE.md

## Tool
Claude (Anthropic), used as primary development collaborator, per the assignment's explicit invitation to do so. Used conversationally to design the schema, write the importer/policy logic, and generate views/templates, with each piece reviewed, run against the real CSV, and corrected where wrong.

## Key prompts (paraphrased from the actual session)
- "Catalog every real anomaly in this CSV before writing any code" — used to build the anomaly table in SCOPE.md from the actual file, not from assumption.
- "Design the schema so membership can change over time and expenses resolve against who was active on that date" — drove the `Membership.covers(date)` design.
- "Build the importer as small named detector functions, each either auto-resolving or flagging pending_review — never a silent guess" — the core policy architecture.
- "Run it against the real file and show me the anomaly log" — used repeatedly to verify claims against actual output rather than trusting the code by inspection alone.

## Three concrete cases where the AI got it wrong

**1. Dev wrongly excluded from every trip expense.**
First version gave Dev no `Membership` row at all (reasoning: "he's a guest, not a resident"). Running the importer against the real CSV showed Dev flagged `non_member` on every single Goa-trip row — which is wrong, since Dev is a real participant who pays for and owes shares of those expenses; only Kabir (his one-off guest) should be excluded. Caught by reading the anomaly log output, not by inspecting the code. Fixed by giving Dev a time-boxed membership scoped to just the trip dates, which correctly includes him in trip expenses while never pulling him into rent/utilities.

**2. Date parser crashed on the real file's format.**
The importer's `parse_date` assumed `YYYY-MM-DD`. The actual exported CSV stores datetimes as `YYYY-MM-DD HH:MM:SS` (e.g. `2026-02-01 00:00:00`), which crashed the very first import run with `unconverted data remains: 00:00:00`. This was a case of writing code against an assumed format instead of the real file — fixed by trying multiple format strings and testing against the actual export.

**3. Dead code left behind from an earlier draft.**
An early version of a membership-check helper was rewritten mid-session, but the old, broken version (referencing an undefined name, wrapped in a `... if False else ...` no-op) was accidentally left in the file alongside the working replacement. It was never called, so tests passed and it wouldn't have crashed in normal use — but it's exactly the kind of line a live interviewer could point at and ask "why does this exist," with no good answer. Found and removed during a final code-review pass before writing this file, specifically because I knew that question type was coming.

## What was NOT delegated to AI
The actual policy decisions (how to handle each anomaly type, what counts as auto-resolvable vs requiring approval, the fixed FX rate, the Dev/Kabir distinction) were made and are owned by me — the AI helped implement them, and helped surface the Dev bug by running real output I could inspect, but did not choose the policies.
