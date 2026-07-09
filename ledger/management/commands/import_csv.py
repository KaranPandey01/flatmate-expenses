from django.core.management.base import BaseCommand
from ledger.importer import run_import

class Command(BaseCommand):
    help = "Import expenses_export.csv and print the anomaly report."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **opts):
        result = run_import(opts["csv_path"], filename_label=opts["csv_path"])
        for line in result.report_lines:
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS(
            f"Batch #{result.batch.id}: {result.batch.total_rows} rows, "
            f"{result.batch.anomalies_found} anomalies, {result.batch.rows_pending_review} pending review."
        ))
