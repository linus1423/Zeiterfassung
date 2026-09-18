from django.contrib import admin

from .models import BreakEntry, TimeEntry


class BreakInline(admin.TabularInline):
    model = BreakEntry
    extra = 0


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "activity", "start", "end", "source", "is_incomplete")
    list_filter = ("group", "source", "is_incomplete")
    search_fields = ("user__email", "user__last_name", "note")
    date_hierarchy = "start"
    inlines = [BreakInline]
    autocomplete_fields = ("user",)
