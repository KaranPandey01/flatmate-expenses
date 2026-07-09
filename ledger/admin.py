from django.contrib import admin
from .models import Person, Group, Membership, Expense, Split, Settlement, ImportBatch, AnomalyLog

admin.site.register(Person)
admin.site.register(Group)
admin.site.register(Membership)
admin.site.register(Split)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('description', 'paid_by', 'amount', 'currency', 'date', 'split_type', 'status', 'source_row')
    list_filter = ('status', 'split_type', 'currency', 'group')
    search_fields = ('description',)

@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ('paid_by', 'paid_to', 'amount_inr', 'date', 'source_row')

@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('filename', 'imported_at', 'total_rows', 'anomalies_found', 'rows_pending_review')

@admin.register(AnomalyLog)
class AnomalyLogAdmin(admin.ModelAdmin):
    list_display = ('source_row', 'anomaly_type', 'resolution_status', 'import_batch')
    list_filter = ('anomaly_type', 'resolution_status')
    search_fields = ('description',)
