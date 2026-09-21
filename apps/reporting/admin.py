from django.contrib import admin

from .models import ExportProfile, ExportRun, ExportSchedule


@admin.register(ExportProfile)
class ExportProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "grouping", "is_shared", "updated_at")
    list_filter = ("grouping", "is_shared")
    search_fields = ("name", "owner__email")


@admin.register(ExportSchedule)
class ExportScheduleAdmin(admin.ModelAdmin):
    list_display = ("profile", "day_of_month", "timeframe", "export_format", "is_active")
    list_filter = ("timeframe", "export_format", "is_active")
    search_fields = ("profile__name", "created_by__email")


@admin.register(ExportRun)
class ExportRunAdmin(admin.ModelAdmin):
    list_display = ("schedule", "due_on", "status", "attempts", "row_count", "finished_at")
    list_filter = ("status",)
    search_fields = ("schedule__profile__name",)
    readonly_fields = ("started_at", "finished_at")
