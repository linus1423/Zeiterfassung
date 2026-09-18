from django.contrib import admin

from .models import CorrectionRequest


@admin.register(CorrectionRequest)
class CorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "requested_by", "group", "kind", "status", "decided_by")
    list_filter = ("status", "kind", "group")
    search_fields = ("reason", "requested_by__email")
    autocomplete_fields = ("requested_by", "decided_by")
    date_hierarchy = "created_at"
