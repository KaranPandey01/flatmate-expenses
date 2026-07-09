# Flatmate Shared Expenses App

Django app for tracking shared flat expenses, built for the Spreetail internship take-home.

## Stack
Python 3.12, Django 6.0, DRF, SQLite locally / Postgres in production (via `DATABASE_URL`), WhiteNoise for static files, Gunicorn for serving.

## AI tool used
Claude (Anthropic), used as primary development collaborator per the assignment's explicit invitation. See `AI_USAGE.md` for prompts and specific corrections.

## Setup (local)
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py import_csv data/expenses_export.csv
python manage.py runserver
```
Visit `http://localhost:8000/login/` and log in with your superuser.

## Key URLs
- `/` — dashboard: net balance per person + suggested settlements
- `/person/<id>/` — full traceable breakdown for one person (every contributing expense/settlement)
- `/import-report/` — every anomaly detected on the last CSV import, with the policy applied
- `/admin/` — full CRUD on groups, memberships, expenses, splits, settlements, and the anomaly log

## Re-running the import
The importer is idempotent-safe to re-run against a fresh DB; it is NOT currently safe to run twice against the same DB without a flush (each run creates a new ImportBatch and new rows) — a known limitation, documented in DECISIONS.md.
