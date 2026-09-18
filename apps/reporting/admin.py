from django.contrib import admin

from .models import ExportProfile


@admin.register(ExportProfile)
class ExportProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "grouping", "is_shared", "updated_at")
    list_filter = ("grouping", "is_shared")
    search_fields = ("name", "owner__email")
